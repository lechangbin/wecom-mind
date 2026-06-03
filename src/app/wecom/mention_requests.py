import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.models import MentionRequest, Message, MessageRaw, now_utc
from app.wecom.message_identity import (
    assign_message_identity,
    resolve_business_identity_key,
)
from app.wecom.reply_sessions import content_fingerprint

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {
    "claimed",
    "running",
    "succeeded",
    "outbox_pending",
    "sending",
    "completed",
    "failed",
    "stalled",
}


@dataclass(frozen=True)
class MentionRequestClaim:
    request: MentionRequest | None
    created: bool
    should_process: bool
    reason: str


def claim_for_frame(
    session: Session,
    *,
    frame: dict[str, Any],
    settings: Settings,
    owner: str = "long_connection",
) -> MentionRequestClaim:
    identity = _frame_identity(frame)
    if not identity["chatid"]:
        return MentionRequestClaim(None, False, False, "missing_chatid")
    if not identity["userid"]:
        logger.error(
            "Mention request claim skipped because userid is missing chatid=%s source_msgid=%s",
            identity["chatid"],
            identity["source_msgid"],
        )
        return MentionRequestClaim(None, False, False, "missing_userid")

    business_key, canonical_message_id, _business_duplicated = resolve_business_identity_key(
        session,
        chatid=str(identity["chatid"]),
        userid=identity["userid"],
        content=identity["content"],
        create_time=identity["create_time"],
        bucket_seconds=settings.wecom_message_identity_bucket_seconds,
    )
    return _claim(
        session,
        business_identity_key=business_key,
        canonical_message_id=canonical_message_id,
        chatid=str(identity["chatid"]),
        userid=identity["userid"],
        content=identity["content"],
        source_msgid=identity["source_msgid"],
        req_id=identity["req_id"],
        owner=owner,
        settings=settings,
    )


def claim_for_message(
    session: Session,
    *,
    message: Message,
    settings: Settings,
    owner: str,
) -> MentionRequestClaim:
    if not message.userid:
        logger.error(
            "Mention request claim skipped because userid is missing message_id=%s chatid=%s",
            message.id,
            message.chatid,
        )
        return MentionRequestClaim(None, False, False, "missing_userid")

    business_identity_key, canonical_message_id, _business_duplicated = (
        assign_message_identity(
            session,
            message,
            bucket_seconds=settings.wecom_message_identity_bucket_seconds,
        )
    )
    if not business_identity_key:
        return MentionRequestClaim(None, False, False, "missing_business_identity")

    raw = session.get(MessageRaw, message.raw_message_id)
    claim = _claim(
        session,
        business_identity_key=business_identity_key,
        canonical_message_id=canonical_message_id,
        chatid=message.chatid,
        userid=message.userid,
        content=message.content_text,
        source_msgid=message.external_msgid,
        req_id=raw.req_id if raw else None,
        owner=owner,
        settings=settings,
    )
    if claim.request is not None:
        attach_message(session, request=claim.request, message=message)
    return claim


def inspect(
    session: Session,
    *,
    business_identity_key: str,
    settings: Settings,
) -> MentionRequest | None:
    request = session.scalar(
        select(MentionRequest).where(
            MentionRequest.business_identity_key == business_identity_key
        )
    )
    if request:
        maybe_mark_stalled(session, request=request, settings=settings)
    return request


def attach_message(
    session: Session,
    *,
    request: MentionRequest,
    message: Message,
) -> MentionRequest:
    canonical_id = message.canonical_message_id or message.id
    if request.canonical_message_id is None:
        request.canonical_message_id = canonical_id
    if not request.source_msgid and message.external_msgid:
        request.source_msgid = message.external_msgid
    request.updated_at = now_utc()
    session.flush()
    return request


def mark_running(
    session: Session,
    request: MentionRequest | None,
    *,
    trigger_event_id: int | None = None,
    ai_run_id: int | None = None,
) -> None:
    if request is None:
        return
    request.status = "running"
    request.trigger_event_id = trigger_event_id or request.trigger_event_id
    request.ai_run_id = ai_run_id or request.ai_run_id
    request.started_at = request.started_at or now_utc()
    request.updated_at = now_utc()
    session.flush()


def mark_succeeded(
    session: Session,
    request: MentionRequest | None,
    *,
    ai_run_id: int | None = None,
) -> None:
    if request is None:
        return
    request.status = "succeeded"
    request.ai_run_id = ai_run_id or request.ai_run_id
    request.updated_at = now_utc()
    session.flush()


def mark_outbox_pending(
    session: Session,
    request: MentionRequest | None,
    *,
    outbox_id: str | None,
) -> None:
    if request is None:
        return
    request.status = "outbox_pending"
    request.outbox_id = outbox_id or request.outbox_id
    request.updated_at = now_utc()
    session.flush()


def mark_sending(session: Session, request: MentionRequest | None) -> None:
    if request is None:
        return
    request.status = "sending"
    request.updated_at = now_utc()
    session.flush()


def mark_completed(session: Session, request: MentionRequest | None) -> None:
    if request is None:
        return
    request.status = "completed"
    request.completed_at = now_utc()
    request.updated_at = now_utc()
    session.flush()


def mark_failed(
    session: Session,
    request: MentionRequest | None,
    *,
    error_message: str | None,
) -> None:
    if request is None:
        return
    request.status = "failed"
    request.last_error = error_message
    request.updated_at = now_utc()
    session.flush()


def mark_stalled(
    session: Session,
    request: MentionRequest | None,
    *,
    error_message: str | None = None,
) -> None:
    if request is None:
        return
    request.status = "stalled"
    request.stalled_at = now_utc()
    request.last_error = error_message or request.last_error
    request.updated_at = now_utc()
    session.flush()


def maybe_mark_stalled(
    session: Session,
    *,
    request: MentionRequest,
    settings: Settings,
) -> bool:
    if request.status != "running" or request.started_at is None:
        return False
    started_at = _to_utc(request.started_at)
    if started_at > now_utc() - timedelta(
        seconds=settings.wecom_mention_request_stalled_after_seconds
    ):
        return False
    mark_stalled(
        session,
        request,
        error_message=(
            "mention request running exceeded "
            f"{settings.wecom_mention_request_stalled_after_seconds}s"
        ),
    )
    logger.error(
        "Mention request stalled request_id=%s business_identity_key=%s",
        request.request_id,
        request.business_identity_key,
    )
    return True


def find_by_outbox_id(session: Session, outbox_id: str) -> MentionRequest | None:
    return session.scalar(
        select(MentionRequest).where(MentionRequest.outbox_id == outbox_id)
    )


def _claim(
    session: Session,
    *,
    business_identity_key: str,
    canonical_message_id: int | None,
    chatid: str,
    userid: str | None,
    content: str | None,
    source_msgid: str | None,
    req_id: str | None,
    owner: str,
    settings: Settings,
) -> MentionRequestClaim:
    existing = inspect(
        session,
        business_identity_key=business_identity_key,
        settings=settings,
    )
    if existing:
        _patch_existing_identity(
            session,
            existing,
            canonical_message_id=canonical_message_id,
            source_msgid=source_msgid,
            req_id=req_id,
        )
        return MentionRequestClaim(
            existing,
            False,
            existing.status not in ACTIVE_STATUSES,
            existing.status,
        )

    request = MentionRequest(
        request_id=_new_request_id(),
        business_identity_key=business_identity_key,
        canonical_message_id=canonical_message_id,
        chatid=chatid,
        userid=userid,
        content_fingerprint=content_fingerprint(content or ""),
        source_msgid=source_msgid,
        req_id=req_id,
        status="claimed",
        owner=owner,
        claimed_at=now_utc(),
    )
    session.add(request)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = inspect(
            session,
            business_identity_key=business_identity_key,
            settings=settings,
        )
        return MentionRequestClaim(
            existing,
            False,
            bool(existing and existing.status not in ACTIVE_STATUSES),
            existing.status if existing else "claim_race_lost",
        )
    return MentionRequestClaim(request, True, True, "created")


def _patch_existing_identity(
    session: Session,
    request: MentionRequest,
    *,
    canonical_message_id: int | None,
    source_msgid: str | None,
    req_id: str | None,
) -> None:
    changed = False
    if request.canonical_message_id is None and canonical_message_id is not None:
        request.canonical_message_id = canonical_message_id
        changed = True
    if not request.source_msgid and source_msgid:
        request.source_msgid = source_msgid
        changed = True
    if not request.req_id and req_id:
        request.req_id = req_id
        changed = True
    if changed:
        request.updated_at = now_utc()
        session.flush()


def _frame_identity(frame: dict[str, Any]) -> dict[str, Any]:
    body = frame.get("body") if isinstance(frame.get("body"), dict) else {}
    headers = frame.get("headers") if isinstance(frame.get("headers"), dict) else {}
    from_user = body.get("from") if isinstance(body.get("from"), dict) else {}
    return {
        "chatid": _optional_str(body.get("chatid") or body.get("chat_id")),
        "userid": _optional_str(from_user.get("userid") or body.get("userid")),
        "source_msgid": _optional_str(body.get("msgid") or body.get("external_msgid")),
        "req_id": _optional_str(headers.get("req_id") or body.get("req_id")),
        "content": _frame_text_content(body),
        "create_time": _parse_frame_time(body.get("create_time") or body.get("send_time")),
    }


def _frame_text_content(body: dict[str, Any]) -> str:
    text = body.get("text")
    if isinstance(text, dict) and text.get("content") is not None:
        return str(text["content"])
    if text is not None:
        return str(text)

    mixed = body.get("mixed")
    if not isinstance(mixed, dict):
        return ""
    items = mixed.get("items")
    if not isinstance(items, list):
        return ""
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text_item = item.get("text")
        if isinstance(text_item, dict) and text_item.get("content") is not None:
            parts.append(str(text_item["content"]))
    return "".join(parts)


def _parse_frame_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _to_utc(value)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(
                timezone.utc
            )
        except ValueError:
            try:
                return datetime.fromtimestamp(float(text), tz=timezone.utc)
            except ValueError:
                return now_utc()
    return now_utc()


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _new_request_id() -> str:
    return f"mention_request_{now_utc().strftime('%Y%m%d')}_{uuid4().hex[:12]}"
