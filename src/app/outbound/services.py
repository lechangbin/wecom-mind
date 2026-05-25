from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.db.models import AiRun, BotReply, Message, OutboxMessage, now_utc
from app.outbound.schemas import OutboxCreateRequest
from app.outbound.sender import WeComMessageSender


def create_outbox_message(
    session: Session,
    payload: OutboxCreateRequest,
) -> tuple[OutboxMessage, bool]:
    if not payload.idempotency_key and not payload.outbox_id:
        raise AppError(
            ErrorCode.INVALID_ARGUMENT,
            "idempotency_key or outbox_id is required",
        )

    existing = _find_existing_outbox(
        session,
        outbox_id=payload.outbox_id,
        idempotency_key=payload.idempotency_key,
    )
    if existing:
        return existing, True

    outbox = OutboxMessage(
        outbox_id=payload.outbox_id or _new_outbox_id(),
        scene=payload.scene,
        chatid=payload.chatid,
        target_userids=payload.target_userids,
        msgtype=payload.msgtype,
        content=payload.content,
        source_type=payload.source_type,
        source_id=payload.source_id,
        status="pending",
        idempotency_key=payload.idempotency_key or payload.outbox_id,
    )
    session.add(outbox)
    session.flush()
    return outbox, False


def create_reply_outbox_for_ai_run(
    session: Session,
    ai_run: AiRun,
) -> dict[str, Any] | None:
    if (
        ai_run.status != "success"
        or ai_run.workflow_code != "reply_generation"
        or not isinstance(ai_run.output_json, dict)
        or ai_run.output_json.get("action") != "reply"
    ):
        return None

    reply = ai_run.output_json.get("reply")
    if not isinstance(reply, dict):
        return None

    content = str(reply.get("content") or "")
    if not content.strip():
        return None

    idempotency_key = f"reply_{ai_run.run_id}"
    existing_outbox = session.scalar(
        select(OutboxMessage).where(OutboxMessage.idempotency_key == idempotency_key)
    )
    existing_reply = session.scalar(select(BotReply).where(BotReply.ai_run_id == ai_run.id))
    if existing_outbox and existing_reply:
        return {
            "bot_reply_id": existing_reply.reply_id,
            "outbox_id": existing_outbox.outbox_id,
            "duplicated": True,
        }

    source_message_id, chatid, userid = _reply_source(session, ai_run.input_json)
    reply_type = _reply_type(reply)
    msgtype = _msgtype_from_reply_type(reply_type)

    bot_reply = existing_reply or BotReply(
        reply_id=_new_reply_id(),
        source_message_id=source_message_id,
        ai_run_id=ai_run.id,
        chatid=chatid,
        userid=userid,
        reply_type=reply_type,
        content=content,
        status="pending",
    )
    if not existing_reply:
        session.add(bot_reply)
        session.flush()

    if existing_outbox:
        outbox = existing_outbox
    else:
        outbox = OutboxMessage(
            outbox_id=_new_outbox_id(),
            scene="reply",
            chatid=bot_reply.chatid,
            target_userids=[],
            msgtype=msgtype,
            content={msgtype: {"content": content}},
            source_type="ai_run",
            source_id=ai_run.run_id,
            status="pending",
            idempotency_key=idempotency_key,
        )
        session.add(outbox)
        session.flush()

    return {
        "bot_reply_id": bot_reply.reply_id,
        "outbox_id": outbox.outbox_id,
        "duplicated": False,
    }


def _reply_source(
    session: Session,
    input_json: dict[str, Any],
) -> tuple[int, str, str | None]:
    legacy_message = input_json.get("message")
    if isinstance(legacy_message, dict):
        return (
            int(legacy_message["message_id"]),
            str(legacy_message["chatid"]),
            legacy_message.get("userid"),
        )

    source_msgid = input_json.get("source_msgid")
    chatid = input_json.get("chatid")
    if not source_msgid or not chatid:
        raise AppError(
            ErrorCode.INVALID_ARGUMENT,
            "reply_generation input requires source_msgid and chatid",
        )

    message = session.scalar(
        select(Message).where(
            Message.external_msgid == str(source_msgid),
            Message.chatid == str(chatid),
        )
    )
    if not message:
        raise AppError(
            ErrorCode.NOT_FOUND,
            f"Source message not found for source_msgid={source_msgid}",
        )
    return message.id, message.chatid, message.userid


def send_outbox_message(
    session: Session,
    *,
    outbox_identifier: str,
    sender: WeComMessageSender,
) -> tuple[OutboxMessage, bool]:
    outbox = get_outbox_message(session, outbox_identifier)

    if outbox.status == "sent":
        return outbox, True

    if outbox.status == "canceled":
        return outbox, True

    outbox.status = "sending"
    session.flush()

    try:
        result = sender.send(outbox)
    except Exception as exc:
        result = {
            "success": False,
            "error_code": "SEND_EXCEPTION",
            "error_message": str(exc),
            "raw_response": {"errmsg": str(exc)},
        }

    raw_response = result.get("raw_response")
    success = result.get("success")
    if success is None:
        success = isinstance(raw_response, dict) and raw_response.get("errcode") == 0

    outbox.raw_response = raw_response
    if success:
        outbox.status = "sent"
        outbox.external_msgid = result.get("external_msgid") or (
            raw_response.get("msgid") if isinstance(raw_response, dict) else None
        )
        outbox.error_code = None
        outbox.error_message = None
        outbox.sent_at = now_utc()
        _sync_bot_reply_send_status(session, outbox, status="sent")
    else:
        outbox.status = "failed"
        outbox.retry_count += 1
        outbox.error_code = result.get("error_code") or _raw_error_code(raw_response)
        outbox.error_message = result.get("error_message") or _raw_error_message(raw_response)
        _sync_bot_reply_send_status(session, outbox, status="failed")

    session.flush()
    return outbox, False


def get_outbox_message(session: Session, outbox_identifier: str) -> OutboxMessage:
    conditions = [OutboxMessage.outbox_id == outbox_identifier]
    if outbox_identifier.isdigit():
        conditions.append(OutboxMessage.id == int(outbox_identifier))

    outbox = session.scalar(select(OutboxMessage).where(or_(*conditions)))
    if not outbox:
        raise AppError(ErrorCode.NOT_FOUND, f"Outbox message not found: {outbox_identifier}")
    return outbox


def outbox_to_dict(outbox: OutboxMessage, *, duplicated: bool | None = None) -> dict[str, Any]:
    data = {
        "id": outbox.id,
        "outbox_id": outbox.outbox_id,
        "scene": outbox.scene,
        "chatid": outbox.chatid,
        "target_userids": outbox.target_userids,
        "msgtype": outbox.msgtype,
        "content": outbox.content,
        "source_type": outbox.source_type,
        "source_id": outbox.source_id,
        "status": outbox.status,
        "retry_count": outbox.retry_count,
        "external_msgid": outbox.external_msgid,
        "error_code": outbox.error_code,
        "error_message": outbox.error_message,
        "raw_response": outbox.raw_response,
        "idempotency_key": outbox.idempotency_key,
        "scheduled_at": _iso(outbox.scheduled_at),
        "sent_at": _iso(outbox.sent_at),
        "created_at": _iso(outbox.created_at),
    }
    if duplicated is not None:
        data["duplicated"] = duplicated
    return data


def _find_existing_outbox(
    session: Session,
    *,
    outbox_id: str | None,
    idempotency_key: str | None,
) -> OutboxMessage | None:
    conditions = []
    if outbox_id:
        conditions.append(OutboxMessage.outbox_id == outbox_id)
    if idempotency_key:
        conditions.append(OutboxMessage.idempotency_key == idempotency_key)
    if not conditions:
        return None
    return session.scalar(select(OutboxMessage).where(or_(*conditions)))


def _reply_type(reply: dict[str, Any]) -> str:
    return str(reply.get("reply_type") or "text")


def _msgtype_from_reply_type(reply_type: str) -> str:
    return "markdown" if reply_type == "markdown" else "text"


def _sync_bot_reply_send_status(
    session: Session,
    outbox: OutboxMessage,
    *,
    status: str,
) -> None:
    if outbox.source_type != "ai_run":
        return

    ai_run = session.scalar(select(AiRun).where(AiRun.run_id == outbox.source_id))
    if not ai_run:
        return

    reply = session.scalar(select(BotReply).where(BotReply.ai_run_id == ai_run.id))
    if not reply:
        return

    reply.status = status
    if status == "sent":
        reply.external_msgid = outbox.external_msgid
        reply.sent_at = outbox.sent_at


def _raw_error_code(raw_response: Any) -> str | None:
    if isinstance(raw_response, dict) and raw_response.get("errcode") is not None:
        return str(raw_response["errcode"])
    return None


def _raw_error_message(raw_response: Any) -> str | None:
    if isinstance(raw_response, dict) and raw_response.get("errmsg") is not None:
        return str(raw_response["errmsg"])
    return None


def _new_reply_id() -> str:
    return f"reply_{now_utc().strftime('%Y%m%d')}_{uuid4().hex[:12]}"


def _new_outbox_id() -> str:
    return f"out_{now_utc().strftime('%Y%m%d')}_{uuid4().hex[:12]}"


def _iso(value):
    return value.isoformat() if value else None
