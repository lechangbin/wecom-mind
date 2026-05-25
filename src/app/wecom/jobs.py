from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.core.errors import AppError, ErrorCode
from app.db.models import MessageIngestionJob, WeComMcpCallback, now_utc
from app.wecom.schemas import MessageIngestRequest
from app.wecom.services import ingest_message


TERMINAL_SUCCESS_STATUSES = {"succeeded", "skipped"}


def process_message_ingestion_job(
    session: Session,
    job_id: int,
    settings: Settings,
) -> dict[str, Any]:
    job = session.get(MessageIngestionJob, job_id)
    if not job:
        raise AppError(ErrorCode.NOT_FOUND, "Message ingestion job not found")

    if job.job_type != "normalize":
        return _job_result(
            job,
            ingested_count=0,
            duplicated_count=0,
            skipped_count=0,
            error_message="pull_jobs_not_supported",
        )

    if job.status in TERMINAL_SUCCESS_STATUSES:
        return _job_result(
            job,
            ingested_count=0,
            duplicated_count=0,
            skipped_count=0,
        )

    callback = _callback_for_job(session, job)
    now = now_utc()
    job.status = "running"
    job.started_at = now
    job.finished_at = None
    job.error_message = None
    session.commit()

    ingested_count = 0
    duplicated_count = 0
    skipped_count = 0

    try:
        raw_messages, skipped_count = _raw_messages_from_callback(callback.raw_body)
        if not raw_messages:
            _mark_skipped(session, job, callback, "no_normalizable_messages")
            return _job_result(
                job,
                ingested_count=0,
                duplicated_count=0,
                skipped_count=skipped_count,
                error_message=job.error_message,
            )

        for index, raw_message in enumerate(raw_messages):
            result = ingest_message(
                session,
                payload=MessageIngestRequest(
                    source="mcp",
                    idempotency_key=_message_idempotency_key(
                        callback.callback_id,
                        raw_message,
                        index,
                    ),
                    raw_message=raw_message,
                ),
                settings=settings,
            )
            if result.get("duplicated"):
                duplicated_count += 1
            else:
                ingested_count += 1

        _mark_succeeded(session, job, callback)
        return _job_result(
            job,
            ingested_count=ingested_count,
            duplicated_count=duplicated_count,
            skipped_count=skipped_count,
        )
    except Exception as exc:
        session.rollback()
        job = session.get(MessageIngestionJob, job_id)
        callback = _callback_for_job(session, job) if job else None
        error_message = _short_error(exc)
        if job:
            now = now_utc()
            job.status = "failed"
            job.retry_count += 1
            job.finished_at = now
            job.error_message = error_message
        if callback:
            callback.status = "failed"
            callback.error_message = error_message
        session.commit()
        return _job_result(
            job,
            ingested_count=ingested_count,
            duplicated_count=duplicated_count,
            skipped_count=skipped_count,
            error_message=error_message,
        )


def process_pending_message_ingestion_jobs(
    session: Session,
    settings: Settings,
    *,
    limit: int = 20,
    job_type: str = "normalize",
) -> dict[str, Any]:
    if job_type != "normalize":
        return {
            "processed_jobs": 0,
            "succeeded_jobs": 0,
            "failed_jobs": 0,
            "skipped_jobs": 0,
            "ingested_messages": 0,
            "duplicated_messages": 0,
            "results": [],
        }

    safe_limit = max(1, min(limit, 100))
    jobs = session.scalars(
        select(MessageIngestionJob)
        .where(
            MessageIngestionJob.status == "pending",
            MessageIngestionJob.job_type == job_type,
        )
        .order_by(MessageIngestionJob.created_at.asc(), MessageIngestionJob.id.asc())
        .limit(safe_limit)
    ).all()

    results = [
        process_message_ingestion_job(session, job.id, settings)
        for job in jobs
    ]
    return {
        "processed_jobs": len(results),
        "succeeded_jobs": sum(1 for item in results if item["status"] == "succeeded"),
        "failed_jobs": sum(1 for item in results if item["status"] == "failed"),
        "skipped_jobs": sum(1 for item in results if item["status"] == "skipped"),
        "ingested_messages": sum(item["ingested_count"] for item in results),
        "duplicated_messages": sum(item["duplicated_count"] for item in results),
        "results": results,
    }


def list_message_ingestion_jobs(
    session: Session,
    *,
    status: str | None = None,
    job_type: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    safe_limit = max(1, min(limit, 100))
    safe_offset = max(offset, 0)
    statement = select(MessageIngestionJob)
    count_statement = select(func.count()).select_from(MessageIngestionJob)

    conditions = []
    if status:
        conditions.append(MessageIngestionJob.status == status)
    if job_type:
        conditions.append(MessageIngestionJob.job_type == job_type)
    if conditions:
        statement = statement.where(*conditions)
        count_statement = count_statement.where(*conditions)

    jobs = session.scalars(
        statement
        .order_by(MessageIngestionJob.created_at.desc(), MessageIngestionJob.id.desc())
        .limit(safe_limit)
        .offset(safe_offset)
    ).all()
    return {
        "total": session.scalar(count_statement) or 0,
        "limit": safe_limit,
        "offset": safe_offset,
        "items": [_job_item(job) for job in jobs],
    }


def _callback_for_job(
    session: Session,
    job: MessageIngestionJob | None,
) -> WeComMcpCallback:
    if not job or not job.callback_id:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "Normalize job missing callback_id")
    callback = session.scalar(
        select(WeComMcpCallback).where(
            WeComMcpCallback.callback_id == job.callback_id
        )
    )
    if not callback:
        raise AppError(ErrorCode.NOT_FOUND, "Callback for ingestion job not found")
    return callback


def _raw_messages_from_callback(raw_body: Any) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(raw_body, dict):
        return [], 1

    raw_message = raw_body.get("raw_message")
    if isinstance(raw_message, dict):
        return [raw_message], 0

    messages = raw_body.get("messages")
    if isinstance(messages, list):
        raw_messages = [item for item in messages if isinstance(item, dict)]
        return raw_messages, len(messages) - len(raw_messages)

    if _looks_like_raw_message(raw_body):
        return [raw_body], 0

    xml_message = _raw_message_from_wecom_xml_dict(raw_body)
    if xml_message:
        return [xml_message], 0

    return [], 1


def _looks_like_raw_message(payload: dict[str, Any]) -> bool:
    return all(payload.get(key) is not None for key in ("msgid", "chatid", "msgtype"))


def _raw_message_from_wecom_xml_dict(payload: dict[str, Any]) -> dict[str, Any] | None:
    msgtype = _first_present(payload, "MsgType", "msgtype")
    if msgtype != "text":
        return None

    chatid = _first_present(payload, "ChatId", "ChatID", "chatid")
    if not chatid:
        return None

    msgid = _first_present(payload, "MsgId", "MsgID", "msgid")
    raw_message: dict[str, Any] = {
        "chatid": str(chatid),
        "chattype": "group",
        "from": {"userid": str(_first_present(payload, "FromUserName", "userid") or "")},
        "msgtype": "text",
        "text": {"content": str(_first_present(payload, "Content", "content") or "")},
        "create_time": _first_present(payload, "CreateTime", "create_time"),
    }
    if msgid:
        raw_message["msgid"] = str(msgid)
    return raw_message


def _message_idempotency_key(
    callback_id: str,
    raw_message: dict[str, Any],
    index: int,
) -> str:
    msgid = raw_message.get("msgid") or raw_message.get("external_msgid")
    if msgid:
        return f"mcp_msg_{msgid}"
    return f"callback_{callback_id}_message_{index}"


def _mark_succeeded(
    session: Session,
    job: MessageIngestionJob,
    callback: WeComMcpCallback,
) -> None:
    now = now_utc()
    job.status = "succeeded"
    job.finished_at = now
    job.error_message = None
    callback.status = "processed"
    callback.error_message = None
    callback.processed_at = now
    session.commit()


def _mark_skipped(
    session: Session,
    job: MessageIngestionJob,
    callback: WeComMcpCallback,
    reason: str,
) -> None:
    now = now_utc()
    job.status = "skipped"
    job.finished_at = now
    job.error_message = reason
    callback.status = "processed"
    callback.error_message = None
    callback.processed_at = now
    session.commit()


def _job_result(
    job: MessageIngestionJob | None,
    *,
    ingested_count: int,
    duplicated_count: int,
    skipped_count: int,
    error_message: str | None = None,
) -> dict[str, Any]:
    return {
        "job_id": job.id if job else None,
        "status": job.status if job else "failed",
        "callback_id": job.callback_id if job else None,
        "ingested_count": ingested_count,
        "duplicated_count": duplicated_count,
        "skipped_count": skipped_count,
        "error_message": error_message if error_message is not None else (job.error_message if job else None),
    }


def _job_item(job: MessageIngestionJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "chatid": job.chatid,
        "callback_id": job.callback_id,
        "retry_count": job.retry_count,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _short_error(exc: Exception) -> str:
    message = str(exc) or exc.__class__.__name__
    return message[:500]
