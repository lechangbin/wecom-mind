from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.schema_validator import JsonSchemaValidationError, validate_json_schema
from app.db.models import AiRun, Message, OutboxMessage, ScheduledIntent, now_utc
from app.dify.client import DifyClient
from app.dify.services import get_enabled_workflow
from app.outbound.schemas import OutboxCreateRequest
from app.outbound.services import create_outbox_message
from app.scheduled_intents.schemas import ScheduledIntentRunRequest


class ScheduledIntentOutputValidationError(Exception):
    pass


def run_scheduled_intent_detection(
    session: Session,
    *,
    payload: ScheduledIntentRunRequest,
    dify_client: DifyClient,
) -> dict[str, Any]:
    start_time = _parse_datetime(payload.time_range.start)
    end_time = _parse_datetime(payload.time_range.end)
    if start_time >= end_time:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "time_range.start must be earlier than end")

    messages = _messages_in_window(session, payload.chatid, start_time, end_time)
    if not messages:
        raise AppError(
            ErrorCode.INVALID_ARGUMENT,
            f"No messages found for chatid={payload.chatid} in the requested window",
        )

    workflow = get_enabled_workflow(
        session,
        workflow_code="intent_detection",
        version="v1",
    )
    input_json = _build_intent_input(
        payload=payload,
        start_time=start_time,
        end_time=end_time,
        messages=messages,
    )
    validate_json_schema(input_json, workflow.input_schema)

    ai_run = AiRun(
        run_id=_new_run_id(),
        workflow_code=workflow.workflow_code,
        workflow_version=workflow.version,
        trigger_event_id=None,
        input_json=input_json,
        response_mode=workflow.response_mode,
        status="running",
        created_at=now_utc(),
        started_at=now_utc(),
    )
    session.add(ai_run)
    session.flush()

    started = perf_counter()
    intents: list[tuple[ScheduledIntent, bool]] = []
    try:
        output_json = dify_client.run_workflow(workflow, input_json)
        ai_run.output_json = output_json
        validate_json_schema(output_json, workflow.output_schema)
        _validate_intent_output(output_json, input_json)
        intents = _write_intents(
            session,
            ai_run=ai_run,
            input_json=input_json,
            output_json=output_json,
            auto_enqueue=payload.auto_enqueue,
        )
        ai_run.status = "success"
        ai_run.error_message = None
    except (JsonSchemaValidationError, ScheduledIntentOutputValidationError) as exc:
        ai_run.status = "invalid_output"
        ai_run.error_message = getattr(exc, "message", str(exc))
    except Exception as exc:
        ai_run.status = "failed"
        ai_run.error_message = str(exc)
    finally:
        ai_run.latency_ms = int((perf_counter() - started) * 1000)
        ai_run.finished_at = now_utc()

    session.commit()

    return {
        "run_id": ai_run.run_id,
        "status": ai_run.status,
        "intents": [
            scheduled_intent_to_dict(intent, duplicated=duplicated)
            for intent, duplicated in intents
        ],
    }


def list_scheduled_intents(
    session: Session,
    *,
    chatid: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    statement = select(ScheduledIntent)
    count_statement = select(func.count()).select_from(ScheduledIntent)
    filters = []
    if chatid:
        filters.append(ScheduledIntent.chatid == chatid)
    if status:
        filters.append(ScheduledIntent.status == status)
    if filters:
        statement = statement.where(*filters)
        count_statement = count_statement.where(*filters)

    total = session.scalar(count_statement)
    items = session.scalars(
        statement.order_by(ScheduledIntent.created_at.desc(), ScheduledIntent.id.desc())
    ).all()
    return {
        "total": total,
        "items": [scheduled_intent_to_dict(item) for item in items],
    }


def scheduled_intent_to_dict(
    intent: ScheduledIntent,
    *,
    duplicated: bool | None = None,
) -> dict[str, Any]:
    data = {
        "id": intent.id,
        "intent_id": intent.intent_id,
        "chatid": intent.chatid,
        "target_userid": intent.target_userid,
        "intent_type": intent.intent_type,
        "evidence_msgids": intent.evidence_msgids,
        "reason": intent.reason,
        "suggested_message": intent.suggested_message,
        "priority": intent.priority,
        "confidence": float(intent.confidence),
        "ai_run_id": intent.ai_run_id,
        "outbox_id": intent.outbox_id,
        "status": intent.status,
        "idempotency_key": intent.idempotency_key,
        "created_at": _iso(intent.created_at),
    }
    if duplicated is not None:
        data["duplicated"] = duplicated
    return data


def _messages_in_window(
    session: Session,
    chatid: str,
    start_time: datetime,
    end_time: datetime,
) -> list[Message]:
    candidates = session.scalars(
        select(Message).where(Message.chatid == chatid).order_by(Message.create_time.asc())
    ).all()
    return [
        message
        for message in candidates
        if start_time <= _to_utc(message.create_time) <= end_time
    ]


def _build_intent_input(
    *,
    payload: ScheduledIntentRunRequest,
    start_time: datetime,
    end_time: datetime,
    messages: list[Message],
) -> dict[str, Any]:
    known_users = []
    for message in messages:
        if message.userid and message.userid not in known_users:
            known_users.append(message.userid)

    return {
        "trigger_source": "schedule_scan",
        "chatid": payload.chatid,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "matched_keywords": [],
        "messages": [
            {
                "message_id": str(message.id),
                "msgid": message.external_msgid,
                "chatid": message.chatid,
                "userid": message.userid,
                "content": message.content_text,
                "create_time": _to_utc(message.create_time).isoformat(),
            }
            for message in messages
        ],
        "known_users": known_users,
        "existing_actions": [],
    }


def _validate_intent_output(output_json: dict[str, Any], input_json: dict[str, Any]) -> None:
    known_users = set(input_json["known_users"])
    valid_msgids = {message["msgid"] for message in input_json["messages"]}

    for action in output_json.get("actions", []):
        target_userids = action.get("target_userids") or []
        if not target_userids:
            raise ScheduledIntentOutputValidationError("target_userids is required")

        missing_userids = [userid for userid in target_userids if userid not in known_users]
        if missing_userids:
            raise ScheduledIntentOutputValidationError(
                f"target_userids {missing_userids[0]} not found in known_users"
            )

        evidence_msgids = action.get("evidence_msgids") or []
        if not evidence_msgids:
            raise ScheduledIntentOutputValidationError("evidence_msgids is required")

        missing_msgids = [msgid for msgid in evidence_msgids if msgid not in valid_msgids]
        if missing_msgids:
            raise ScheduledIntentOutputValidationError(
                f"evidence_msgids {missing_msgids[0]} not found in input messages"
            )

        if (
            action.get("action_type") == "reply"
            and float(action["confidence"]) >= 0.7
            and not (action.get("reply_instruction") or "").strip()
        ):
            raise ScheduledIntentOutputValidationError(
                "reply_instruction is required for high confidence reply actions"
            )


def _write_intents(
    session: Session,
    *,
    ai_run: AiRun,
    input_json: dict[str, Any],
    output_json: dict[str, Any],
    auto_enqueue: bool,
) -> list[tuple[ScheduledIntent, bool]]:
    written = []
    chatid = input_json["chatid"]
    start_time = input_json["start_time"]
    end_time = input_json["end_time"]

    for intent_data in output_json.get("actions", []):
        target_userid = (intent_data.get("target_userids") or [""])[0]
        suggested_message = intent_data.get("reply_instruction") or ""
        idempotency_key = _intent_idempotency_key(
            chatid=chatid,
            start_time=start_time,
            end_time=end_time,
            target_userid=target_userid,
            first_evidence_msgid=intent_data["evidence_msgids"][0],
        )
        existing = session.scalar(
            select(ScheduledIntent).where(
                ScheduledIntent.idempotency_key == idempotency_key
            )
        )
        if existing:
            written.append((existing, True))
            continue

        confidence = float(intent_data["confidence"])
        status = "ignored_low_confidence" if confidence < 0.7 else "pending"
        intent = ScheduledIntent(
            intent_id=_new_intent_id(),
            chatid=chatid,
            target_userid=target_userid,
            intent_type=intent_data["intent_type"],
            evidence_msgids=intent_data.get("evidence_msgids") or [],
            reason=intent_data.get("reason") or "",
            suggested_message=suggested_message,
            priority=intent_data.get("priority") or "medium",
            confidence=confidence,
            ai_run_id=ai_run.id,
            status=status,
            idempotency_key=idempotency_key,
        )
        session.add(intent)
        session.flush()

        if confidence >= 0.7 and auto_enqueue:
            outbox, _duplicated = create_outbox_message(
                session,
                OutboxCreateRequest(
                    scene="proactive",
                    chatid=chatid,
                    target_userids=[intent.target_userid],
                    msgtype="markdown",
                    content={
                        "markdown": {
                            "content": (
                                f"<@{intent.target_userid}> "
                                f"{intent.suggested_message}"
                            )
                        }
                    },
                    source_type="ai_run",
                    source_id=ai_run.run_id,
                    idempotency_key=idempotency_key,
                ),
            )
            intent.outbox_id = outbox.outbox_id
            intent.status = "enqueued"

        written.append((intent, False))

    session.flush()
    return written


def _intent_idempotency_key(
    *,
    chatid: str,
    start_time: str,
    end_time: str,
    target_userid: str,
    first_evidence_msgid: str,
) -> str:
    return (
        f"intent_{chatid}_{_key_part(start_time)}_{_key_part(end_time)}_"
        f"{target_userid}_{first_evidence_msgid}"
    )


def _key_part(value: str) -> str:
    return (
        value.replace(":", "")
        .replace("-", "")
        .replace("+", "")
        .replace(".", "")
    )


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _new_run_id() -> str:
    return f"airun_{now_utc().strftime('%Y%m%d')}_{uuid4().hex[:12]}"


def _new_intent_id() -> str:
    return f"intent_{now_utc().strftime('%Y%m%d')}_{uuid4().hex[:12]}"


def _iso(value):
    return value.isoformat() if value else None
