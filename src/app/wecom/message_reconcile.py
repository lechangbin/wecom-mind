import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.core.errors import AppError, ErrorCode
from app.db.models import WeComMcpPullCursor, now_utc
from app.dify.client import DifyClient
from app.outbound.services import send_outbox_message
from app.proactive_replies.schemas import ProactiveReplyRunRequest
from app.proactive_replies.services import run_proactive_reply
from app.wecom.mention_recovery import run_mention_recovery
from app.wecom.mention_requests import (
    find_by_outbox_id,
    mark_completed,
    mark_failed,
    mark_sending,
)
from app.wecom.schemas import MessageIngestRequest
from app.wecom.services import ingest_message

WECOM_MCP_MAX_HISTORY_SECONDS = 7 * 24 * 60 * 60 - 60


class WeComHistoryMessageSource(Protocol):
    def fetch_messages(
        self,
        *,
        chatid: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        ...


def calculate_reconcile_window(
    *,
    now: datetime,
    last_pulled_at: datetime | None,
    lookback_seconds: int,
    overlap_seconds: int,
) -> tuple[datetime, datetime]:
    now = _to_utc(now)
    if last_pulled_at is None:
        start_time = now - timedelta(seconds=lookback_seconds)
    else:
        start_time = _to_utc(last_pulled_at) - timedelta(seconds=overlap_seconds)

    earliest_allowed_start = now - timedelta(seconds=WECOM_MCP_MAX_HISTORY_SECONDS)
    if start_time < earliest_allowed_start:
        start_time = earliest_allowed_start

    return start_time, now


def run_message_reconcile_once(
    session: Session,
    *,
    chatid: str,
    settings: Settings,
    message_source: WeComHistoryMessageSource,
    dify_client: DifyClient,
    auto_enqueue: bool = True,
    auto_send: bool = False,
    sender: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    cursor = _get_or_create_cursor(session, chatid)
    end_time = _to_utc(now or now_utc())
    start_time, end_time = calculate_reconcile_window(
        now=end_time,
        last_pulled_at=_to_utc(cursor.last_pulled_at) if cursor.last_pulled_at else None,
        lookback_seconds=settings.wecom_message_reconcile_lookback_seconds,
        overlap_seconds=settings.wecom_message_reconcile_overlap_seconds,
    )

    raw_messages = message_source.fetch_messages(
        chatid=chatid,
        start_time=start_time,
        end_time=end_time,
    )

    ingested_count = 0
    duplicated_count = 0
    for raw_message in sorted(raw_messages, key=_raw_message_sort_key):
        result = ingest_message(
            session,
            payload=MessageIngestRequest(
                source="wecom_reconcile",
                idempotency_key=_message_idempotency_key(raw_message),
                raw_message=raw_message,
            ),
            settings=settings,
        )
        if result.get("duplicated"):
            duplicated_count += 1
        else:
            ingested_count += 1
    session.commit()

    mention_recovery_status = "skipped_no_messages"
    mention_recovery_count = 0
    mention_recovery_outbox_count = 0
    mention_recovery_sent_outbox_count = 0
    mention_recovery_result = run_mention_recovery(
        session,
        chatid=chatid,
        start_time=start_time,
        end_time=end_time,
        dify_client=dify_client,
        settings=settings,
        auto_enqueue=auto_enqueue,
    )
    mention_recovery_status = str(mention_recovery_result["status"])
    mention_recovery_count = int(mention_recovery_result["recovered_count"])
    mention_recovery_outboxes = mention_recovery_result.get("outboxes") or []
    mention_recovery_outbox_count = len(mention_recovery_outboxes)
    should_send_directly = (
        auto_send
        and sender is not None
        and settings.wecom_sender_mode != "aibot_ws"
    )
    if should_send_directly:
        for outbox_info in mention_recovery_outboxes:
            outbox_id = outbox_info.get("outbox_id")
            if not outbox_id:
                continue
            mention_request = find_by_outbox_id(session, str(outbox_id))
            if mention_request is not None:
                if mention_request.status == "completed":
                    continue
                if (
                    mention_request.outbox_id
                    and mention_request.outbox_id != str(outbox_id)
                ):
                    continue
                if mention_request.status == "running":
                    continue
                mark_sending(session, mention_request)
            _outbox, duplicated = send_outbox_message(
                session,
                outbox_identifier=str(outbox_id),
                sender=sender,
            )
            if _outbox.status == "sent":
                mark_completed(session, mention_request)
            elif _outbox.status == "failed":
                mark_failed(
                    session,
                    mention_request,
                    error_message=_outbox.error_message,
                )
            if not duplicated and _outbox.status == "sent":
                mention_recovery_sent_outbox_count += 1
        session.commit()

    proactive_status = "skipped_no_messages"
    outbox_count = 0
    sent_outbox_count = 0
    try:
        proactive_result = run_proactive_reply(
            session,
            payload=ProactiveReplyRunRequest(
                chatid=chatid,
                time_range={
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat(),
                },
                auto_enqueue=auto_enqueue,
            ),
            dify_client=dify_client,
        )
        proactive_status = str(proactive_result["status"])
        outboxes = proactive_result.get("outboxes") or []
        outbox_count = len(outboxes)
        if should_send_directly:
            for outbox_info in outboxes:
                outbox_id = outbox_info.get("outbox_id")
                if not outbox_id:
                    continue
                _outbox, duplicated = send_outbox_message(
                    session,
                    outbox_identifier=str(outbox_id),
                    sender=sender,
                )
                if not duplicated and _outbox.status == "sent":
                    sent_outbox_count += 1
            session.commit()
    except AppError as exc:
        if exc.code != ErrorCode.INVALID_ARGUMENT or "No messages found" not in exc.message:
            raise

    cursor.last_pulled_at = end_time
    cursor.status = "active"
    session.commit()

    return {
        "fetched_count": len(raw_messages),
        "ingested_count": ingested_count,
        "duplicated_count": duplicated_count,
        "mention_recovery_status": mention_recovery_status,
        "mention_recovery_count": mention_recovery_count,
        "mention_recovery_outbox_count": mention_recovery_outbox_count,
        "mention_recovery_sent_outbox_count": mention_recovery_sent_outbox_count,
        "proactive_status": proactive_status,
        "outbox_count": outbox_count,
        "sent_outbox_count": sent_outbox_count,
    }


def _get_or_create_cursor(session: Session, chatid: str) -> WeComMcpPullCursor:
    cursor = session.scalar(
        select(WeComMcpPullCursor).where(
            WeComMcpPullCursor.chatid == chatid,
            WeComMcpPullCursor.cursor_type == "message_reconcile",
        )
    )
    if cursor:
        return cursor

    cursor = WeComMcpPullCursor(
        chatid=chatid,
        cursor_type="message_reconcile",
        status="active",
    )
    session.add(cursor)
    session.flush()
    return cursor


def _message_idempotency_key(raw_message: dict[str, Any]) -> str:
    msgid = raw_message.get("msgid") or raw_message.get("external_msgid")
    if msgid:
        return f"reconcile_msg_{msgid}"
    return "reconcile_payload_" + _stable_hash(raw_message)


def _raw_message_sort_key(raw_message: dict[str, Any]) -> tuple[datetime, str]:
    return (
        _parse_message_time(raw_message.get("create_time")),
        str(raw_message.get("msgid") or raw_message.get("external_msgid") or ""),
    )


def _parse_message_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _to_utc(value)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _to_utc(parsed)
    return datetime.fromtimestamp(0, tz=timezone.utc)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
