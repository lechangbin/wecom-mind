import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings
from app.db.models import MentionRequest, Message, OutboxMessage, WeComReplySession
from app.dify.client import DifyClient
from app.outbound.services import outbox_to_dict, send_outbox_message_async
from app.triggers.schemas import TriggerEvaluateRequest
from app.triggers.services import evaluate_triggers
from app.wecom.reply_sessions import (
    attach_reply_session_to_frame_outbox,
    mark_reply_session_delivery,
    record_reply_session_placeholder,
)
from app.wecom.mention_requests import (
    attach_message as attach_mention_request_message,
    claim_for_frame,
    claim_for_message,
    find_by_outbox_id,
    mark_completed,
    mark_failed,
    mark_sending,
)
from app.wecom.schemas import MessageIngestRequest
from app.wecom.services import ingest_message

logger = logging.getLogger(__name__)


class AiBotFrameNormalizer:
    def normalize(self, frame: dict[str, Any]) -> MessageIngestRequest | None:
        body = frame.get("body")
        if not isinstance(body, dict):
            return None

        msgtype = self._message_type(body)
        if not msgtype:
            return None

        raw_message = self._raw_message(frame, body, msgtype)
        return MessageIngestRequest(
            source="aibot_ws",
            idempotency_key=self._idempotency_key(frame, raw_message),
            raw_message=raw_message,
        )

    def _raw_message(
        self,
        frame: dict[str, Any],
        body: dict[str, Any],
        msgtype: str,
    ) -> dict[str, Any]:
        raw: dict[str, Any] = {}
        for key in (
            "msgid",
            "chatid",
            "chat_id",
            "chattype",
            "chat_type",
            "userid",
            "msgtype",
            "text",
            "mixed",
            "image",
            "voice",
            "file",
            "video",
            "quote",
            "mentioned_users",
            "mentions",
            "create_time",
            "send_time",
            "chat",
            "chat_name",
        ):
            if key in body:
                raw[key] = body[key]

        headers = frame.get("headers")
        if isinstance(headers, dict) and headers.get("req_id"):
            raw["req_id"] = str(headers["req_id"])

        from_user = body.get("from")
        if isinstance(from_user, dict):
            raw["from"] = from_user

        if "chatid" not in raw and raw.get("chat_id") is not None:
            raw["chatid"] = raw["chat_id"]
        if "chattype" not in raw and raw.get("chat_type") is not None:
            raw["chattype"] = _chat_type(raw["chat_type"])
        raw["msgtype"] = msgtype

        if raw.get("msgtype") == "mixed" and "text" not in raw:
            mixed_text = _mixed_text(raw.get("mixed"))
            if mixed_text:
                raw["text"] = {"content": mixed_text}

        return raw

    def _message_type(self, body: dict[str, Any]) -> str | None:
        msgtype = body.get("msgtype")
        if msgtype:
            return str(msgtype)
        if isinstance(body.get("text"), dict) or body.get("text") is not None:
            return "text"
        if isinstance(body.get("mixed"), dict):
            return "mixed"
        return None

    def _idempotency_key(self, frame: dict[str, Any], raw_message: dict[str, Any]) -> str:
        msgid = raw_message.get("msgid")
        if msgid:
            return f"aibot_ws_msg_{msgid}"

        headers = frame.get("headers")
        if isinstance(headers, dict) and headers.get("req_id"):
            return f"aibot_ws_req_{headers['req_id']}"

        return "aibot_ws_payload_" + _stable_hash(raw_message)


async def process_incoming_aibot_frame(
    session: Session,
    *,
    frame: dict[str, Any],
    settings: Settings,
    dify_client: DifyClient,
    sender: Any | None = None,
    auto_send: bool = False,
    normalizer: AiBotFrameNormalizer | None = None,
) -> dict[str, Any]:
    result = _ingest_and_evaluate_aibot_frame(
        session,
        frame=frame,
        settings=settings,
        dify_client=dify_client,
        normalizer=normalizer,
    )
    if not result["accepted"] or (result.get("ingest") or {}).get("duplicated"):
        return result

    if auto_send and sender is not None:
        result["sent_outboxes"] = await _send_trigger_outboxes(
            session,
            trigger_result=result["trigger"],
            sender=sender,
        )
        session.commit()

    return result


def _ingest_and_evaluate_aibot_frame(
    session: Session,
    *,
    frame: dict[str, Any],
    settings: Settings,
    dify_client: DifyClient,
    normalizer: AiBotFrameNormalizer | None = None,
    mention_request_id: int | None = None,
) -> dict[str, Any]:
    payload = (normalizer or AiBotFrameNormalizer()).normalize(frame)
    if payload is None:
        return {
            "accepted": False,
            "reason": "unsupported_frame",
            "ingest": None,
            "trigger": {"matched": False, "events": []},
            "sent_outboxes": [],
        }

    ingest_result = ingest_message(session, payload=payload, settings=settings)
    if ingest_result.get("duplicated"):
        return {
            "accepted": True,
            "ingest": ingest_result,
            "trigger": {"matched": False, "events": []},
            "sent_outboxes": [],
        }

    message = session.get(Message, ingest_result["message_id"])
    trigger_result = {"matched": False, "events": []}
    if message and message.mentioned_bot:
        mention_request = (
            session.get(MentionRequest, mention_request_id)
            if mention_request_id is not None
            else None
        )
        if mention_request is None:
            claim = claim_for_message(
                session,
                message=message,
                settings=settings,
                owner="long_connection",
            )
            if not claim.should_process or claim.request is None:
                session.commit()
                return {
                    "accepted": True,
                    "ingest": ingest_result,
                    "trigger": {"matched": False, "events": []},
                    "sent_outboxes": [],
                    "mention_request_id": claim.request.id if claim.request else None,
                }
            mention_request = claim.request
            mention_request_id = mention_request.id
        else:
            attach_mention_request_message(
                session,
                request=mention_request,
                message=message,
            )
            session.commit()
        trigger_result = evaluate_triggers(
            session,
            payload=TriggerEvaluateRequest(message_id=message.id),
            dify_client=dify_client,
            mention_request_id=mention_request_id,
        )

    return {
        "accepted": True,
        "ingest": ingest_result,
        "trigger": trigger_result,
        "sent_outboxes": [],
        "mention_request_id": mention_request_id,
    }


def _ingest_and_evaluate_aibot_frame_in_new_session(
    session_factory: sessionmaker[Session],
    *,
    frame: dict[str, Any],
    settings: Settings,
    dify_client: DifyClient,
    normalizer: AiBotFrameNormalizer,
    mention_request_id: int | None = None,
) -> dict[str, Any]:
    with session_factory() as session:
        return _ingest_and_evaluate_aibot_frame(
            session,
            frame=frame,
            settings=settings,
            dify_client=dify_client,
            normalizer=normalizer,
            mention_request_id=mention_request_id,
        )


class WeComAiBotLongConnectionWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: sessionmaker[Session],
        dify_client: DifyClient,
        sender: Any,
        ws_client_factory: Callable[[Settings], Any] | None = None,
        auto_send: bool = True,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.dify_client = dify_client
        self.sender = sender
        self.auto_send = auto_send
        self.ws_client = (ws_client_factory or _build_ws_client)(settings)
        self.normalizer = AiBotFrameNormalizer()
        self._send_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._outbox_dispatch_started_at: datetime | None = None

    async def start(self) -> None:
        self._register_handlers()
        await self.ws_client.connect()
        if self.auto_send and self.sender is not None and self.settings.wecom_message_reconcile_auto_send:
            self._outbox_dispatch_started_at = datetime.now(timezone.utc)
            task = asyncio.create_task(self._dispatch_proactive_outboxes_forever())
            self._tasks.add(task)
            task.add_done_callback(self._handle_task_done)

    async def stop(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.ws_client.disconnect()

    def _register_handlers(self) -> None:
        for event_name in (
            "message.text",
            "message.mixed",
            "message.image",
            "message.voice",
            "message.file",
            "message.video",
        ):
            self.ws_client.on(event_name, self._handle_frame)

    async def _handle_frame(self, frame: dict[str, Any]) -> None:
        task = asyncio.create_task(self._process_frame(frame))
        self._tasks.add(task)
        task.add_done_callback(self._handle_task_done)

    def _handle_task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("AiBot background frame task failed")

    async def _process_frame(self, frame: dict[str, Any]) -> None:
        callback_stream_id = None
        mention_request_id = None
        if (
            self.auto_send
            and self.sender is not None
            and _frame_mentions_reply_bot(frame, self.settings)
        ):
            with self.session_factory() as session:
                claim = claim_for_frame(
                    session,
                    frame=frame,
                    settings=self.settings,
                    owner="long_connection",
                )
                if not claim.should_process or claim.request is None:
                    session.commit()
                    logger.info(
                        "Skipped duplicate/active AiBot mention frame reason=%s",
                        claim.reason,
                    )
                    return
                mention_request_id = claim.request.id
                session.commit()
            callback_stream_id = await _begin_callback_reply_stream(
                sender=self.sender,
                frame=frame,
            )
            if callback_stream_id:
                await _record_reply_session_placeholder_with_retry(
                    self.session_factory,
                    frame=frame,
                    stream_id=callback_stream_id,
                    mention_request_id=mention_request_id,
                )

        result = await asyncio.to_thread(
            _ingest_and_evaluate_aibot_frame_in_new_session,
            self.session_factory,
            frame=frame,
            settings=self.settings,
            dify_client=self.dify_client,
            normalizer=self.normalizer,
            mention_request_id=mention_request_id,
        )

        duplicated = (result.get("ingest") or {}).get("duplicated")
        if self.auto_send and self.sender is not None and not duplicated:
            async with self._send_lock:
                with self.session_factory() as session:
                    result["sent_outboxes"] = await _send_trigger_outboxes(
                        session,
                        trigger_result=result["trigger"],
                        sender=self.sender,
                        frame=frame,
                        callback_stream_id=callback_stream_id,
                        mention_request_id=result.get("mention_request_id"),
                    )
                    session.commit()

        logger.info(
            "Processed AiBot frame accepted=%s matched=%s sent=%s",
            result["accepted"],
            result["trigger"]["matched"],
            len(result["sent_outboxes"]),
        )

    async def _dispatch_proactive_outboxes_forever(self) -> None:
        while True:
            try:
                sent = await self._dispatch_proactive_outboxes_once()
                if sent:
                    logger.info("Dispatched proactive outboxes count=%s", sent)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Proactive outbox dispatch failed")
            await asyncio.sleep(0.25)

    async def _dispatch_proactive_outboxes_once(self) -> int:
        if self.sender is None:
            return 0
        async with self._send_lock:
            with self.session_factory() as session:
                proactive_sent = await _send_pending_proactive_outboxes(
                    session,
                    sender=self.sender,
                    created_after=self._outbox_dispatch_started_at,
                )
                recovery_sent = await _send_pending_reply_recovery_outboxes(
                    session,
                    sender=self.sender,
                    created_after=self._outbox_dispatch_started_at,
                )
                session.commit()
                return proactive_sent + recovery_sent


async def _send_trigger_outboxes(
    session: Session,
    *,
    trigger_result: dict[str, Any],
    sender: Any,
    frame: dict[str, Any] | None = None,
    callback_stream_id: str | None = None,
    mention_request_id: int | None = None,
) -> list[dict[str, Any]]:
    sent = []
    delivery_sender = _callback_bound_sender(
        sender,
        frame=frame,
        callback_stream_id=callback_stream_id,
    )
    for event in trigger_result.get("events", []):
        outbox_info = event.get("outbox")
        if not isinstance(outbox_info, dict) or not outbox_info.get("outbox_id"):
            continue
        mention_request = (
            session.get(MentionRequest, mention_request_id)
            if mention_request_id is not None
            else None
        )
        outbox_id = str(outbox_info["outbox_id"])
        if (
            mention_request is not None
            and mention_request.outbox_id
            and mention_request.outbox_id != outbox_id
        ):
            logger.warning(
                "Skipped outbox send because mention request owns another outbox request_id=%s outbox_id=%s owned_outbox_id=%s",
                mention_request.request_id,
                outbox_id,
                mention_request.outbox_id,
            )
            continue
        if frame is not None:
            attach_reply_session_to_frame_outbox(
                session,
                frame=frame,
                trigger_event_id=event.get("trigger_event_id"),
                outbox_id=outbox_id,
                mention_request_id=mention_request_id,
            )
        mark_sending(session, mention_request)
        outbox, duplicated = await send_outbox_message_async(
            session,
            outbox_identifier=outbox_id,
            sender=delivery_sender,
        )
        mark_reply_session_delivery(
            session,
            outbox_id=outbox.outbox_id,
            status=outbox.status,
            error_message=outbox.error_message,
        )
        if outbox.status == "sent":
            mark_completed(session, mention_request)
        elif outbox.status == "failed":
            mark_failed(session, mention_request, error_message=outbox.error_message)
        sent.append(outbox_to_dict(outbox, duplicated=duplicated))
    return sent


async def _send_pending_proactive_outboxes(
    session: Session,
    *,
    sender: Any,
    created_after: datetime | None,
    limit: int = 10,
) -> int:
    query = (
        select(OutboxMessage)
        .where(
            OutboxMessage.scene == "proactive",
            OutboxMessage.status == "pending",
        )
        .order_by(OutboxMessage.created_at, OutboxMessage.id)
        .limit(limit)
    )
    if created_after is not None:
        query = query.where(OutboxMessage.created_at >= created_after)

    sent_count = 0
    for outbox in session.scalars(query).all():
        sent, duplicated = await send_outbox_message_async(
            session,
            outbox_identifier=outbox.outbox_id,
            sender=sender,
        )
        if not duplicated and sent.status == "sent":
            sent_count += 1
    return sent_count


async def _send_pending_reply_recovery_outboxes(
    session: Session,
    *,
    sender: Any,
    created_after: datetime | None,
    limit: int = 10,
) -> int:
    query = (
        select(OutboxMessage)
        .where(
            OutboxMessage.scene == "reply_recovery",
            OutboxMessage.status == "pending",
        )
        .order_by(OutboxMessage.created_at, OutboxMessage.id)
        .limit(limit)
    )
    if created_after is not None:
        query = query.where(OutboxMessage.created_at >= created_after)

    sent_count = 0
    for outbox in session.scalars(query).all():
        mention_request = find_by_outbox_id(session, outbox.outbox_id)
        if mention_request is not None:
            if mention_request.status == "completed":
                continue
            if mention_request.outbox_id and mention_request.outbox_id != outbox.outbox_id:
                continue
            if mention_request.status == "running":
                continue
        reply_session = session.scalar(
            select(WeComReplySession).where(
                WeComReplySession.outbox_id == outbox.outbox_id
            )
        )
        delivery_sender = sender
        if reply_session and reply_session.frame_json:
            delivery_sender = _callback_bound_sender(
                sender,
                frame=reply_session.frame_json,
                callback_stream_id=reply_session.stream_id,
            )
        mark_sending(session, mention_request)
        sent, duplicated = await send_outbox_message_async(
            session,
            outbox_identifier=outbox.outbox_id,
            sender=delivery_sender,
        )
        mark_reply_session_delivery(
            session,
            outbox_id=sent.outbox_id,
            status=sent.status,
            error_message=sent.error_message,
        )
        if sent.status == "sent":
            mark_completed(session, mention_request)
        elif sent.status == "failed":
            mark_failed(session, mention_request, error_message=sent.error_message)
        if not duplicated and sent.status == "sent":
            sent_count += 1
    return sent_count


async def _begin_callback_reply_stream(
    *,
    sender: Any,
    frame: dict[str, Any],
) -> str | None:
    begin = getattr(sender, "begin_callback_stream_async", None)
    if begin is None:
        return None
    try:
        result = await begin(frame, "正在查询相关资料，请稍等。")
    except Exception:
        logger.exception("Failed to begin AiBot callback stream")
        return None
    if isinstance(result, dict) and result.get("success"):
        stream_id = result.get("stream_id")
        return str(stream_id) if stream_id else None
    logger.warning("AiBot callback stream begin failed: %s", result)
    return None


async def _record_reply_session_placeholder_with_retry(
    session_factory: sessionmaker[Session],
    *,
    frame: dict[str, Any],
    stream_id: str,
    mention_request_id: int | None = None,
    attempts: int = 3,
) -> bool:
    for attempt in range(1, attempts + 1):
        try:
            with session_factory() as session:
                reply_session = record_reply_session_placeholder(
                    session,
                    frame=frame,
                    stream_id=stream_id,
                    mention_request_id=mention_request_id,
                )
                if mention_request_id is not None and reply_session is not None:
                    request = session.get(MentionRequest, mention_request_id)
                    if request is not None:
                        request.reply_session_id = reply_session.id
                        request.updated_at = datetime.now(timezone.utc)
                session.commit()
            return True
        except OperationalError as exc:
            if not _is_database_locked(exc):
                logger.exception("Failed to record AiBot callback reply session")
                return False
            logger.warning(
                "AiBot callback reply session record hit database lock attempt=%s/%s",
                attempt,
                attempts,
            )
            await asyncio.sleep(0.08 * attempt)
        except Exception:
            logger.exception("Failed to record AiBot callback reply session")
            return False

    logger.error("Failed to record AiBot callback reply session after database lock retries")
    return False


def _is_database_locked(exc: OperationalError) -> bool:
    return "database is locked" in str(exc).lower()


def _callback_bound_sender(
    sender: Any,
    *,
    frame: dict[str, Any] | None,
    callback_stream_id: str | None,
) -> Any:
    if frame is None:
        return sender
    if getattr(sender, "send_callback_final_async", None) is None:
        return sender
    return _CallbackBoundReplySender(
        sender,
        frame=frame,
        callback_stream_id=callback_stream_id,
    )


class _CallbackBoundReplySender:
    def __init__(
        self,
        sender: Any,
        *,
        frame: dict[str, Any],
        callback_stream_id: str | None,
    ) -> None:
        self.sender = sender
        self.frame = frame
        self.callback_stream_id = callback_stream_id

    async def send_async(self, outbox: OutboxMessage) -> dict[str, Any]:
        return await self.sender.send_callback_final_async(
            outbox,
            frame=self.frame,
            stream_id=self.callback_stream_id,
        )


def _frame_mentions_reply_bot(frame: dict[str, Any], settings: Settings) -> bool:
    body = frame.get("body")
    if not isinstance(body, dict):
        return False

    bot_id = settings.effective_reply_aibot_id
    bot_name = settings.effective_reply_aibot_name
    mentioned_values = _mention_values(body.get("mentioned_users"))
    mentioned_values.update(_mention_values(body.get("mentions")))
    if bot_id and bot_id in mentioned_values:
        return True
    if bot_name and bot_name in mentioned_values:
        return True

    content = _frame_text_content(body)
    return bool(bot_name and f"@{bot_name}" in content)


def _mention_values(value: Any) -> set[str]:
    values: set[str] = set()
    if not isinstance(value, list):
        return values
    for item in value:
        if isinstance(item, str):
            values.add(item)
            continue
        if not isinstance(item, dict):
            continue
        for key in ("userid", "user_id", "id", "name"):
            if item.get(key) is not None:
                values.add(str(item[key]))
    return values


def _frame_text_content(body: dict[str, Any]) -> str:
    text = body.get("text")
    if isinstance(text, dict) and text.get("content") is not None:
        return str(text["content"])
    if body.get("msgtype") == "mixed":
        return _mixed_text(body.get("mixed"))
    return ""


def _build_ws_client(settings: Settings) -> Any:
    missing = []
    bot_id = settings.effective_reply_aibot_id
    bot_secret = settings.effective_reply_aibot_secret
    if not bot_id:
        missing.append("WECOM_REPLY_AIBOT_ID")
    if not bot_secret:
        missing.append("WECOM_REPLY_AIBOT_SECRET")
    if missing:
        raise ValueError(f"{', '.join(missing)} are required for AiBot long connection")

    try:
        from wecom_aibot_sdk import WSClient
    except ImportError as exc:
        raise RuntimeError("wecom-aibot-sdk is required for AiBot long connection") from exc

    return WSClient(
        bot_id,
        bot_secret,
        ws_url=settings.effective_reply_aibot_ws_url or "",
        reconnect_interval=1000,
        max_reconnect_attempts=settings.wecom_max_retries,
        heartbeat_interval=30000,
        request_timeout=settings.wecom_timeout_seconds * 1000,
    )


def _mixed_text(mixed: Any) -> str:
    if not isinstance(mixed, dict):
        return ""
    items = mixed.get("items")
    if not isinstance(items, list):
        return ""
    parts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, dict) and text.get("content") is not None:
            parts.append(str(text["content"]))
    return "".join(parts)


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _chat_type(value: Any) -> str:
    if str(value) == "2":
        return "group"
    if str(value) == "1":
        return "single"
    return str(value)
