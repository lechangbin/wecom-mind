import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Message, WeComReplySession, now_utc


def record_reply_session_placeholder(
    session: Session,
    *,
    frame: dict[str, Any],
    stream_id: str,
    mention_request_id: int | None = None,
) -> WeComReplySession | None:
    identity = _frame_identity(frame)
    if not identity["chatid"]:
        return None

    existing = _find_session_by_identity(
        session,
        identity,
        mention_request_id=mention_request_id,
    )
    if existing:
        if not _can_reuse_session(existing, mention_request_id=mention_request_id):
            return None
        existing.frame_json = frame
        existing.stream_id = stream_id
        existing.mention_request_id = mention_request_id or existing.mention_request_id
        existing.placeholder_status = "sent"
        existing.final_status = "pending"
        existing.updated_at = now_utc()
        session.flush()
        return existing

    reply_session = WeComReplySession(
        session_id=_new_session_id(),
        chatid=identity["chatid"],
        userid=identity["userid"],
        mention_request_id=mention_request_id,
        source_msgid=identity["source_msgid"],
        req_id=identity["req_id"],
        content_fingerprint=identity["content_fingerprint"],
        frame_json=frame,
        stream_id=stream_id,
        placeholder_status="sent",
        final_status="pending",
        expires_at=now_utc() + timedelta(minutes=10),
    )
    session.add(reply_session)
    session.flush()
    return reply_session


def attach_reply_session_to_message_outbox(
    session: Session,
    *,
    message: Message,
    trigger_event_id: int | None,
    outbox_id: str,
    mention_request_id: int | None = None,
) -> WeComReplySession | None:
    reply_session = find_reply_session_for_message(
        session,
        message,
        mention_request_id=mention_request_id,
    )
    if reply_session is None:
        return None
    if not _can_bind_outbox(
        reply_session,
        outbox_id=outbox_id,
        mention_request_id=mention_request_id,
    ):
        return None
    reply_session.message_id = message.id
    reply_session.mention_request_id = mention_request_id or reply_session.mention_request_id
    reply_session.trigger_event_id = trigger_event_id
    reply_session.outbox_id = outbox_id
    reply_session.updated_at = now_utc()
    session.flush()
    return reply_session


def attach_reply_session_to_frame_outbox(
    session: Session,
    *,
    frame: dict[str, Any],
    trigger_event_id: int | None,
    outbox_id: str,
    mention_request_id: int | None = None,
) -> WeComReplySession | None:
    identity = _frame_identity(frame)
    reply_session = _find_session_by_identity(
        session,
        identity,
        mention_request_id=mention_request_id,
    )
    if reply_session is None:
        return None
    if not _can_bind_outbox(
        reply_session,
        outbox_id=outbox_id,
        mention_request_id=mention_request_id,
    ):
        return None
    reply_session.mention_request_id = mention_request_id or reply_session.mention_request_id
    reply_session.trigger_event_id = trigger_event_id
    reply_session.outbox_id = outbox_id
    reply_session.updated_at = now_utc()
    session.flush()
    return reply_session


def find_reply_session_for_message(
    session: Session,
    message: Message,
    *,
    mention_request_id: int | None = None,
) -> WeComReplySession | None:
    if message.external_msgid:
        exact = session.scalar(
            select(WeComReplySession)
            .where(WeComReplySession.source_msgid == message.external_msgid)
            .order_by(WeComReplySession.created_at.desc(), WeComReplySession.id.desc())
        )
        if exact and _can_reuse_session(exact, mention_request_id=mention_request_id):
            return exact

    fingerprint = content_fingerprint(message.content_text or "")
    if not fingerprint:
        return None

    by_fingerprint = session.scalar(
        select(WeComReplySession)
        .where(
            WeComReplySession.chatid == message.chatid,
            WeComReplySession.userid == message.userid,
            WeComReplySession.content_fingerprint == fingerprint,
            WeComReplySession.created_at >= now_utc() - timedelta(minutes=10),
        )
        .order_by(WeComReplySession.created_at.desc(), WeComReplySession.id.desc())
    )
    if by_fingerprint and _can_reuse_session(
        by_fingerprint,
        mention_request_id=mention_request_id,
    ):
        return by_fingerprint

    return _find_session_by_canonical_content(
        session,
        chatid=message.chatid,
        userid=message.userid,
        canonical=canonical_content(message.content_text or ""),
        mention_request_id=mention_request_id,
    )


def mark_reply_session_delivery(
    session: Session,
    *,
    outbox_id: str,
    status: str,
    error_message: str | None = None,
) -> None:
    reply_session = session.scalar(
        select(WeComReplySession).where(WeComReplySession.outbox_id == outbox_id)
    )
    if not reply_session:
        return
    reply_session.final_status = status
    reply_session.last_error = error_message
    reply_session.updated_at = now_utc()
    session.flush()


def content_fingerprint(content: str) -> str | None:
    normalized = canonical_content(content)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


_WHITESPACE_RE = re.compile(r"\s+")


def canonical_content(content: str) -> str:
    return _WHITESPACE_RE.sub("", content or "")


def _find_session_by_identity(
    session: Session,
    identity: dict[str, str | None],
    *,
    mention_request_id: int | None = None,
) -> WeComReplySession | None:
    if identity["source_msgid"]:
        by_msgid = session.scalar(
            select(WeComReplySession)
            .where(WeComReplySession.source_msgid == identity["source_msgid"])
            .order_by(WeComReplySession.created_at.desc(), WeComReplySession.id.desc())
        )
        if by_msgid and _can_reuse_session(
            by_msgid,
            mention_request_id=mention_request_id,
        ):
            return by_msgid

    if identity["req_id"]:
        by_req = session.scalar(
            select(WeComReplySession)
            .where(WeComReplySession.req_id == identity["req_id"])
            .order_by(WeComReplySession.created_at.desc(), WeComReplySession.id.desc())
        )
        if by_req and _can_reuse_session(
            by_req,
            mention_request_id=mention_request_id,
        ):
            return by_req

    fingerprint = identity["content_fingerprint"]
    if not identity["chatid"] or not fingerprint:
        return None

    by_fingerprint = session.scalar(
        select(WeComReplySession)
        .where(
            WeComReplySession.chatid == identity["chatid"],
            WeComReplySession.userid == identity["userid"],
            WeComReplySession.content_fingerprint == fingerprint,
            WeComReplySession.created_at >= now_utc() - timedelta(minutes=10),
        )
        .order_by(WeComReplySession.created_at.desc(), WeComReplySession.id.desc())
    )
    if by_fingerprint and _can_reuse_session(
        by_fingerprint,
        mention_request_id=mention_request_id,
    ):
        return by_fingerprint

    return _find_session_by_canonical_content(
        session,
        chatid=identity["chatid"],
        userid=identity["userid"],
        canonical=identity["canonical_content"],
        mention_request_id=mention_request_id,
    )


def _find_session_by_canonical_content(
    session: Session,
    *,
    chatid: str | None,
    userid: str | None,
    canonical: str | None,
    mention_request_id: int | None = None,
) -> WeComReplySession | None:
    if not chatid or not canonical:
        return None

    candidates = session.scalars(
        select(WeComReplySession)
        .where(
            WeComReplySession.chatid == chatid,
            WeComReplySession.userid == userid,
            WeComReplySession.created_at >= now_utc() - timedelta(minutes=10),
        )
        .order_by(WeComReplySession.created_at.desc(), WeComReplySession.id.desc())
    ).all()
    for reply_session in candidates:
        if (
            _session_canonical_content(reply_session) == canonical
            and _can_reuse_session(
                reply_session,
                mention_request_id=mention_request_id,
            )
        ):
            return reply_session
    return None


def _can_reuse_session(
    reply_session: WeComReplySession,
    *,
    mention_request_id: int | None,
) -> bool:
    if reply_session.final_status == "sent":
        return False
    if reply_session.outbox_id:
        return False
    if (
        mention_request_id is not None
        and reply_session.mention_request_id is not None
        and reply_session.mention_request_id != mention_request_id
    ):
        return False
    return True


def _can_bind_outbox(
    reply_session: WeComReplySession,
    *,
    outbox_id: str,
    mention_request_id: int | None,
) -> bool:
    if reply_session.final_status == "sent":
        return False
    if reply_session.outbox_id and reply_session.outbox_id != outbox_id:
        return False
    if (
        mention_request_id is not None
        and reply_session.mention_request_id is not None
        and reply_session.mention_request_id != mention_request_id
    ):
        return False
    return True


def _frame_identity(frame: dict[str, Any]) -> dict[str, str | None]:
    body = frame.get("body") if isinstance(frame.get("body"), dict) else {}
    headers = frame.get("headers") if isinstance(frame.get("headers"), dict) else {}
    from_user = body.get("from") if isinstance(body.get("from"), dict) else {}
    content = _frame_text_content(body)
    return {
        "chatid": _optional_str(body.get("chatid") or body.get("chat_id")),
        "userid": _optional_str(from_user.get("userid") or body.get("userid")),
        "source_msgid": _optional_str(body.get("msgid") or body.get("external_msgid")),
        "req_id": _optional_str(headers.get("req_id") or body.get("req_id")),
        "content_fingerprint": content_fingerprint(content),
        "canonical_content": canonical_content(content),
    }


def _session_canonical_content(reply_session: WeComReplySession) -> str | None:
    frame = reply_session.frame_json
    if not isinstance(frame, dict):
        return None
    body = frame.get("body") if isinstance(frame.get("body"), dict) else {}
    return canonical_content(_frame_text_content(body))


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
    parts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text_item = item.get("text")
        if isinstance(text_item, dict) and text_item.get("content") is not None:
            parts.append(str(text_item["content"]))
    return "".join(parts)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _new_session_id() -> str:
    return f"reply_session_{datetime.now(timezone.utc).strftime('%Y%m%d')}_{uuid4().hex[:12]}"
