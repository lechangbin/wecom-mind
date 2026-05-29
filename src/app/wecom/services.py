import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.core.errors import AppError, ErrorCode
from app.db.models import (
    Message,
    MessageIngestionJob,
    MessageRaw,
    WeComChat,
    WeComMcpCallback,
    WeComUser,
    now_utc,
)
from app.wecom.schemas import MessageIngestRequest


def record_mcp_callback(
    session: Session,
    *,
    query: Mapping[str, str],
    body_payload: Any,
    raw_body: bytes,
    signature_valid: bool,
) -> dict[str, Any]:
    idempotency_key = _callback_idempotency_key(query, body_payload, raw_body)
    existing = session.scalar(
        select(WeComMcpCallback).where(
            WeComMcpCallback.idempotency_key == idempotency_key
        )
    )
    if existing:
        return {
            "callback_id": existing.callback_id,
            "accepted": True,
            "duplicated": True,
        }

    body_dict = body_payload if isinstance(body_payload, dict) else {}
    callback_id = _new_callback_id()
    callback = WeComMcpCallback(
        callback_id=callback_id,
        event_type=_optional_str(
            _first_present(body_dict, "event_type", "Event", "MsgType")
        ),
        chatid=_optional_str(_first_present(body_dict, "chatid", "ChatId", "chat_id")),
        raw_query=dict(query),
        raw_body=body_payload,
        signature_valid=signature_valid,
        idempotency_key=idempotency_key,
        status="received",
    )
    session.add(callback)
    session.flush()

    job = _build_callback_job(callback)
    existing_job = session.scalar(
        select(MessageIngestionJob).where(
            MessageIngestionJob.idempotency_key == job.idempotency_key
        )
    )
    if not existing_job:
        session.add(job)

    session.commit()

    return {
        "callback_id": callback.callback_id,
        "accepted": True,
        "duplicated": False,
    }


def ingest_message(
    session: Session,
    *,
    payload: MessageIngestRequest,
    settings: Settings,
) -> dict[str, Any]:
    raw_message = payload.raw_message
    source = payload.source
    external_msgid = _external_msgid(raw_message)

    duplicate_conditions = [
        MessageRaw.idempotency_key == payload.idempotency_key,
        (MessageRaw.source == source) & (MessageRaw.external_msgid == external_msgid),
    ]
    if external_msgid and not external_msgid.startswith("payload:"):
        duplicate_conditions.append(MessageRaw.external_msgid == external_msgid)

    existing_raw = session.scalar(
        select(MessageRaw).where(or_(*duplicate_conditions)).limit(1)
    )
    if existing_raw:
        existing_message = _message_for_raw(session, existing_raw.id)
        return {
            "raw_message_id": existing_raw.id,
            "message_id": existing_message.id,
            "duplicated": True,
        }

    chatid = _required_str(raw_message.get("chatid"), "raw_message.chatid")
    msgtype = _required_str(raw_message.get("msgtype"), "raw_message.msgtype")
    from_user = raw_message.get("from") if isinstance(raw_message.get("from"), dict) else {}
    userid = _optional_str(from_user.get("userid") or raw_message.get("userid"))
    create_time = _parse_message_time(raw_message.get("create_time"))

    raw = MessageRaw(
        source=source,
        external_msgid=external_msgid,
        req_id=_optional_str(raw_message.get("req_id")),
        chatid=chatid,
        userid=userid,
        msgtype=msgtype,
        raw_payload=raw_message,
        idempotency_key=payload.idempotency_key,
        create_time=create_time,
    )
    session.add(raw)
    session.flush()

    content_text = _extract_content_text(raw_message, msgtype)
    mentioned_users = _extract_mentioned_users(raw_message)
    quote_message = raw_message.get("quote")
    sender_type, bot_role = _sender_classification(userid, settings)
    message = Message(
        raw_message_id=raw.id,
        external_msgid=external_msgid,
        chatid=chatid,
        chattype=_optional_str(raw_message.get("chattype")) or "group",
        userid=userid,
        msgtype=msgtype,
        sender_type=sender_type,
        bot_role=bot_role,
        content_text=content_text,
        normalized_content=_normalized_content(msgtype, content_text),
        quote_message=quote_message,
        quote_msgid=_quote_msgid(quote_message),
        mentioned_bot=_mentioned_bot(content_text, mentioned_users, settings),
        mentioned_users=mentioned_users,
        create_time=create_time,
    )
    session.add(message)
    _upsert_chat(session, raw_message, chatid, source, create_time)
    _upsert_user(session, from_user, userid, create_time)
    session.flush()
    session.commit()

    return {
        "raw_message_id": raw.id,
        "message_id": message.id,
        "duplicated": False,
    }


def backfill_message_sender_classification(
    session: Session,
    settings: Settings,
) -> dict[str, int]:
    messages = session.scalars(select(Message)).all()
    updated_count = 0
    for message in messages:
        sender_type, bot_role = _sender_classification(message.userid, settings)
        if message.sender_type == sender_type and message.bot_role == bot_role:
            continue
        message.sender_type = sender_type
        message.bot_role = bot_role
        updated_count += 1

    if updated_count:
        session.commit()
    return {"updated_count": updated_count}


def _build_callback_job(callback: WeComMcpCallback) -> MessageIngestionJob:
    body = callback.raw_body if isinstance(callback.raw_body, dict) else {}
    cursor = _optional_str(_first_present(body, "cursor", "Cursor"))
    chatid = callback.chatid

    if chatid and cursor:
        job_type = "pull"
        idempotency_key = f"pull:{chatid}:{cursor}"
    else:
        job_type = "normalize"
        idempotency_key = f"callback:{callback.callback_id}:normalize"

    return MessageIngestionJob(
        job_type=job_type,
        chatid=chatid,
        callback_id=callback.callback_id,
        cursor_before=cursor,
        status="pending",
        idempotency_key=idempotency_key,
    )


def _callback_idempotency_key(
    query: Mapping[str, str],
    body_payload: Any,
    raw_body: bytes,
) -> str:
    if isinstance(body_payload, dict) and body_payload.get("idempotency_key"):
        return str(body_payload["idempotency_key"])

    fingerprint = {
        "query": dict(sorted(query.items())),
        "body": body_payload if body_payload is not None else raw_body.decode("utf-8", "replace"),
    }
    return "mcp_callback:" + _stable_hash(fingerprint)


def _new_callback_id() -> str:
    return f"cb_{now_utc().strftime('%Y%m%d')}_{uuid4().hex[:12]}"


def _external_msgid(raw_message: dict[str, Any]) -> str:
    external_msgid = raw_message.get("msgid") or raw_message.get("external_msgid")
    if external_msgid:
        return str(external_msgid)
    return "payload:" + _stable_hash(raw_message)


def _message_for_raw(session: Session, raw_message_id: int) -> Message:
    message = session.scalar(
        select(Message).where(Message.raw_message_id == raw_message_id)
    )
    if not message:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            "Raw message exists without normalized message",
        )
    return message


def _extract_content_text(raw_message: dict[str, Any], msgtype: str) -> str | None:
    if msgtype not in {"text", "mixed"}:
        return None
    text = raw_message.get("text")
    if isinstance(text, dict):
        value = text.get("content")
        return str(value) if value is not None else ""
    if text is not None:
        return str(text)

    mixed = raw_message.get("mixed")
    if isinstance(mixed, dict):
        items = mixed.get("items")
        if isinstance(items, list):
            parts = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                body = item.get("text")
                if isinstance(body, dict) and body.get("content") is not None:
                    parts.append(str(body["content"]))
            return "".join(parts)
    return ""


def _sender_classification(userid: str | None, settings: Settings) -> tuple[str, str | None]:
    if not userid:
        return "system", None
    if userid == settings.effective_reply_aibot_id:
        return "bot", "reply_bot"
    if userid == settings.effective_intent_aibot_id:
        return "bot", "intent_bot"
    if userid in {settings.wecom_aibot_id, settings.wecom_bot_id}:
        return "bot", "unknown_bot"
    return "user", None


def _normalized_content(msgtype: str, content_text: str | None) -> dict[str, Any]:
    if msgtype in {"text", "mixed"}:
        return {"type": "text", "text": content_text or ""}
    return {"type": msgtype}


def _extract_mentioned_users(raw_message: dict[str, Any]) -> list[Any]:
    candidates = [
        raw_message.get("mentioned_users"),
        raw_message.get("mentions"),
    ]
    text = raw_message.get("text")
    if isinstance(text, dict):
        candidates.append(text.get("mentioned_users"))
        candidates.append(text.get("mentions"))

    for candidate in candidates:
        if isinstance(candidate, list):
            normalized = []
            for item in candidate:
                if isinstance(item, dict):
                    normalized.append(item.get("userid") or item.get("id") or item)
                else:
                    normalized.append(item)
            return normalized
    return []


def _mentioned_bot(
    content_text: str | None,
    mentioned_users: list[Any],
    settings: Settings,
) -> bool:
    bot_id = settings.effective_reply_aibot_id
    if bot_id and bot_id in {str(user) for user in mentioned_users}:
        return True

    bot_name = settings.effective_reply_aibot_name
    if bot_name and content_text and f"@{bot_name}" in content_text:
        return True

    return False


def _quote_msgid(quote_message: Any) -> str | None:
    if not isinstance(quote_message, dict):
        return None
    value = (
        quote_message.get("msgid")
        or quote_message.get("quote_msgid")
        or quote_message.get("external_msgid")
    )
    return str(value) if value is not None else None


def _upsert_chat(
    session: Session,
    raw_message: dict[str, Any],
    chatid: str,
    source: str,
    create_time: datetime,
) -> None:
    chat = session.scalar(select(WeComChat).where(WeComChat.chatid == chatid))
    if not chat:
        chat = WeComChat(chatid=chatid)
        session.add(chat)

    chat.chattype = _optional_str(raw_message.get("chattype")) or chat.chattype or "group"
    chat.name = _chat_name(raw_message) or chat.name
    chat.source = source
    chat.status = "active"
    if not chat.last_message_at or _as_utc(create_time) > _as_utc(chat.last_message_at):
        chat.last_message_at = create_time
    chat.updated_at = now_utc()


def _upsert_user(
    session: Session,
    from_user: dict[str, Any],
    userid: str | None,
    create_time: datetime,
) -> None:
    if not userid:
        return

    user = session.scalar(select(WeComUser).where(WeComUser.userid == userid))
    if not user:
        user = WeComUser(userid=userid)
        session.add(user)

    user.name = _optional_str(from_user.get("name")) or user.name
    user.alias = _optional_str(from_user.get("alias")) or user.alias
    user.department = _optional_str(from_user.get("department")) or user.department
    user.status = "active"
    if not user.last_active_at or _as_utc(create_time) > _as_utc(user.last_active_at):
        user.last_active_at = create_time
    user.updated_at = now_utc()


def _chat_name(raw_message: dict[str, Any]) -> str | None:
    chat = raw_message.get("chat")
    if isinstance(chat, dict):
        return _optional_str(chat.get("name"))
    return _optional_str(raw_message.get("chat_name"))


def _parse_message_time(value: Any) -> datetime:
    if value is None:
        return now_utc()
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        if value.isdigit():
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise AppError(ErrorCode.INVALID_ARGUMENT, "Unsupported raw_message.create_time")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _required_str(value: Any, field_name: str) -> str:
    if value is None or str(value) == "":
        raise AppError(ErrorCode.INVALID_ARGUMENT, f"{field_name} is required")
    return str(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _first_present(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _stable_hash(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
