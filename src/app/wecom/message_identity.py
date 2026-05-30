import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Message
from app.wecom.reply_sessions import canonical_content


def compute_business_identity_key(
    *,
    chatid: str,
    userid: str | None,
    content: str | None,
    create_time: datetime,
    bucket_seconds: int = 5,
) -> str:
    timestamp = _to_utc(create_time).timestamp()
    bucket = int(timestamp) // max(bucket_seconds, 1)
    payload = {
        "chatid": chatid,
        "userid": userid or "",
        "content": canonical_content(content or ""),
        "time_bucket": bucket,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"bizmsg_{digest[:32]}"


def assign_message_identity(
    session: Session,
    message: Message,
    *,
    bucket_seconds: int = 5,
) -> tuple[str | None, int | None, bool]:
    if not message.chatid or not message.create_time:
        return message.business_identity_key, message.canonical_message_id, False

    key = message.business_identity_key or compute_business_identity_key(
        chatid=message.chatid,
        userid=message.userid,
        content=message.content_text,
        create_time=message.create_time,
        bucket_seconds=bucket_seconds,
    )
    message.business_identity_key = key

    existing = session.scalar(
        select(Message)
        .where(
            Message.business_identity_key == key,
            Message.id != message.id,
        )
        .order_by(Message.id.asc())
    )
    if existing:
        if existing.canonical_message_id is None:
            existing.canonical_message_id = existing.id
        canonical_id = existing.canonical_message_id or existing.id
        message.canonical_message_id = canonical_id
        session.flush()
        return key, canonical_id, True

    if message.id is not None and message.canonical_message_id is None:
        message.canonical_message_id = message.id
    session.flush()
    return key, message.canonical_message_id, False


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
