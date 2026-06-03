import hashlib
import json
from datetime import datetime, timedelta, timezone

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

    nearby = _find_nearby_business_message(
        session,
        chatid=message.chatid,
        userid=message.userid,
        content=message.content_text,
        create_time=message.create_time,
        bucket_seconds=bucket_seconds,
        exclude_message_id=message.id,
    )
    if nearby is not None:
        key = _ensure_message_key(session, nearby, bucket_seconds=bucket_seconds)
        canonical_id = nearby.canonical_message_id or nearby.id
        nearby.canonical_message_id = canonical_id
        message.business_identity_key = key
        message.canonical_message_id = canonical_id
        session.flush()
        return key, canonical_id, True

    key = message.business_identity_key or _compute_message_key(
        message,
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


def resolve_business_identity_key(
    session: Session,
    *,
    chatid: str,
    userid: str | None,
    content: str | None,
    create_time: datetime,
    bucket_seconds: int = 5,
) -> tuple[str, int | None, bool]:
    nearby = _find_nearby_business_message(
        session,
        chatid=chatid,
        userid=userid,
        content=content,
        create_time=create_time,
        bucket_seconds=bucket_seconds,
        exclude_message_id=None,
    )
    if nearby is not None:
        key = _ensure_message_key(session, nearby, bucket_seconds=bucket_seconds)
        canonical_id = nearby.canonical_message_id or nearby.id
        nearby.canonical_message_id = canonical_id
        session.flush()
        return key, canonical_id, True

    return (
        compute_business_identity_key(
            chatid=chatid,
            userid=userid,
            content=content,
            create_time=create_time,
            bucket_seconds=bucket_seconds,
        ),
        None,
        False,
    )


def _find_nearby_business_message(
    session: Session,
    *,
    chatid: str,
    userid: str | None,
    content: str | None,
    create_time: datetime,
    bucket_seconds: int,
    exclude_message_id: int | None,
) -> Message | None:
    canonical = canonical_content(content or "")
    if not chatid or not userid or not canonical:
        return None

    create_time_utc = _to_utc(create_time)
    span = max(bucket_seconds, 1)
    query = (
        select(Message)
        .where(
            Message.chatid == chatid,
            Message.userid == userid,
            Message.create_time >= create_time_utc - timedelta(seconds=span),
            Message.create_time <= create_time_utc + timedelta(seconds=span),
        )
        .order_by(Message.id.asc())
    )
    if exclude_message_id is not None:
        query = query.where(Message.id != exclude_message_id)

    for candidate in session.scalars(query).all():
        if canonical_content(candidate.content_text or "") == canonical:
            return candidate
    return None


def _ensure_message_key(
    session: Session,
    message: Message,
    *,
    bucket_seconds: int,
) -> str:
    if not message.business_identity_key:
        message.business_identity_key = _compute_message_key(
            message,
            bucket_seconds=bucket_seconds,
        )
    if message.canonical_message_id is None and message.id is not None:
        message.canonical_message_id = message.id
    session.flush()
    return message.business_identity_key


def _compute_message_key(message: Message, *, bucket_seconds: int) -> str:
    return compute_business_identity_key(
        chatid=message.chatid,
        userid=message.userid,
        content=message.content_text,
        create_time=message.create_time,
        bucket_seconds=bucket_seconds,
    )


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
