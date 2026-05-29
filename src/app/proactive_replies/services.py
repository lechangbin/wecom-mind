import hashlib
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.schema_validator import JsonSchemaValidationError, validate_json_schema
from app.db.models import AiRun, Message, TriggerEvent, now_utc
from app.dify.client import DifyClient
from app.dify.services import get_enabled_workflow
from app.outbound.schemas import OutboxCreateRequest
from app.outbound.services import create_outbox_message, outbox_to_dict
from app.proactive_replies.schemas import ProactiveReplyRunRequest


class ProactiveReplyOutputValidationError(Exception):
    pass


def run_proactive_reply(
    session: Session,
    *,
    payload: ProactiveReplyRunRequest,
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
        workflow_code="chat_proactive_reminder",
        version="v1",
    )
    input_json = build_chat_proactive_reminder_input(session=session, messages=messages)
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
    outbox_results: list[dict[str, Any]] = []
    try:
        output_json = dify_client.run_workflow(workflow, input_json)
        ai_run.output_json = output_json
        validate_json_schema(output_json, workflow.output_schema)
        _validate_proactive_output(output_json, input_json)
        outbox_results = _create_proactive_outboxes(
            session,
            ai_run=ai_run,
            input_json=input_json,
            output_json=output_json,
            auto_enqueue=payload.auto_enqueue,
        )
        ai_run.status = "success"
        ai_run.error_message = None
    except (JsonSchemaValidationError, ProactiveReplyOutputValidationError) as exc:
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
        "workflow_code": ai_run.workflow_code,
        "status": ai_run.status,
        "outboxes": outbox_results,
    }


def build_chat_proactive_reminder_input(
    *,
    session: Session,
    messages: list[Message],
) -> dict[str, Any]:
    members = []
    seen_userids = set()
    message_items = []
    for message in messages:
        if message.userid and message.userid not in seen_userids:
            seen_userids.add(message.userid)
            members.append({"userid": message.userid})
        message_items.append(
            {
                "message_id": str(message.id),
                "msgid": message.external_msgid,
                "chatid": message.chatid,
                "userid": message.userid,
                "content": message.content_text,
                "create_time": _to_utc(message.create_time).isoformat(),
            }
        )

    recent_texts = [
        str(item["content"]).strip()
        for item in message_items[-5:]
        if item.get("content")
    ]
    question = "\n".join(recent_texts)[:800]
    trigger_message = message_items[-1]
    return {
        "payload": {
            "scenario": "message_window_scan",
            "intent_type": "knowledge_need",
            "question": question,
            "message": trigger_message,
            "messages": message_items,
            "members": members,
            "handled_records": _handled_records_for_messages(session, messages),
        }
    }


def _handled_records_for_messages(
    session: Session,
    messages: list[Message],
) -> list[dict[str, str]]:
    if not messages:
        return []

    by_id = {message.id: message for message in messages}
    events = session.scalars(
        select(TriggerEvent)
        .where(
            TriggerEvent.message_id.in_(by_id.keys()),
            TriggerEvent.status.in_(["handled", "sent", "success"]),
        )
        .order_by(TriggerEvent.created_at.asc(), TriggerEvent.id.asc())
    ).all()

    records = []
    for event in events:
        message = by_id.get(event.message_id)
        if not message:
            continue
        records.append(
            {
                "msgid": message.external_msgid,
                "message_id": str(message.id),
                "handler": event.workflow_code,
                "reason": "mention_already_handled",
            }
        )
    return records


def _messages_in_window(
    session: Session,
    chatid: str,
    start_time: datetime,
    end_time: datetime,
) -> list[Message]:
    start_utc = _to_utc(start_time)
    end_utc = _to_utc(end_time)
    return session.scalars(
        select(Message)
        .where(
            Message.chatid == chatid,
            Message.create_time >= start_utc,
            Message.create_time <= end_utc,
            Message.sender_type == "user",
        )
        .order_by(Message.create_time.asc(), Message.id.asc())
    ).all()


def _validate_proactive_output(output_json: dict[str, Any], input_json: dict[str, Any]) -> None:
    payload = input_json["payload"]
    valid_userids = {
        member["userid"]
        for member in payload["members"]
        if isinstance(member, dict) and member.get("userid")
    }
    valid_msgids = {
        message["msgid"]
        for message in payload["messages"]
        if isinstance(message, dict) and message.get("msgid")
    }

    if not output_json.get("should_send"):
        return

    missing_userids = [
        userid
        for userid in output_json.get("target_userids", [])
        if userid not in valid_userids
    ]
    if missing_userids:
        raise ProactiveReplyOutputValidationError(
            f"target_userids {missing_userids[0]} not found in input members"
        )

    quote_msgid = output_json.get("quote_msgid")
    if quote_msgid not in valid_msgids:
        raise ProactiveReplyOutputValidationError(
            f"quote_msgid {quote_msgid} not found in input messages"
        )


def _create_proactive_outboxes(
    session: Session,
    *,
    ai_run: AiRun,
    input_json: dict[str, Any],
    output_json: dict[str, Any],
    auto_enqueue: bool,
) -> list[dict[str, Any]]:
    if not auto_enqueue or not output_json.get("should_send"):
        return []

    confidence = float(output_json.get("confidence") or 0)
    if confidence < 0.7:
        return []

    target_userids = [str(userid) for userid in output_json.get("target_userids") or []]
    content = str(output_json.get("content") or "").strip()
    quote_msgid = str(output_json.get("quote_msgid") or "")
    if not target_userids or not content:
        return []

    chatid = input_json["payload"]["messages"][0]["chatid"]
    mentioned_prefix = " ".join(f"<@{userid}>" for userid in target_userids)
    markdown_content = f"{mentioned_prefix} {content}".strip()
    idempotency_key = (
        f"proactive_{chatid}_{quote_msgid}_{'_'.join(target_userids)}_"
        f"{_stable_key(content)}"
    )
    outbox, duplicated = create_outbox_message(
        session,
        OutboxCreateRequest(
            scene="proactive",
            chatid=chatid,
            target_userids=target_userids,
            msgtype="markdown",
            content={
                "markdown": {
                    "content": markdown_content,
                    "quote_msgid": quote_msgid,
                }
            },
            source_type="ai_run",
            source_id=ai_run.run_id,
            idempotency_key=idempotency_key,
        ),
    )
    return [outbox_to_dict(outbox, duplicated=duplicated)]


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stable_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _new_run_id() -> str:
    return f"airun_{now_utc().strftime('%Y%m%d')}_{uuid4().hex[:12]}"
