import logging
from typing import Any, Callable

from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings
from app.db.models import Message, OutboxMessage
from app.dify.client import DifyClient
from app.outbound.services import outbox_to_dict, send_outbox_message_async
from app.triggers.schemas import TriggerEvaluateRequest
from app.triggers.services import evaluate_triggers
from app.wecom.schemas import MessageIngestRequest
from app.wecom.services import ingest_message

logger = logging.getLogger(__name__)


class AiBotFrameNormalizer:
    def normalize(self, frame: dict[str, Any]) -> MessageIngestRequest | None:
        body = frame.get("body")
        if not isinstance(body, dict) or not body.get("msgtype"):
            return None

        raw_message = self._raw_message(frame, body)
        return MessageIngestRequest(
            source="aibot_ws",
            idempotency_key=self._idempotency_key(frame, raw_message),
            raw_message=raw_message,
        )

    def _raw_message(
        self,
        frame: dict[str, Any],
        body: dict[str, Any],
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

        if raw.get("msgtype") == "mixed" and "text" not in raw:
            mixed_text = _mixed_text(raw.get("mixed"))
            if mixed_text:
                raw["text"] = {"content": mixed_text}

        return raw

    def _idempotency_key(self, frame: dict[str, Any], raw_message: dict[str, Any]) -> str:
        msgid = raw_message.get("msgid")
        if msgid:
            return f"aibot_ws_msg_{msgid}"

        headers = frame.get("headers")
        if isinstance(headers, dict) and headers.get("req_id"):
            return f"aibot_ws_req_{headers['req_id']}"

        raise ValueError("AiBot frame requires msgid or headers.req_id for idempotency")


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
        trigger_result = evaluate_triggers(
            session,
            payload=TriggerEvaluateRequest(message_id=message.id),
            dify_client=dify_client,
        )

    sent_outboxes = []
    if auto_send and sender is not None:
        sent_outboxes = await _send_trigger_outboxes(
            session,
            trigger_result=trigger_result,
            sender=sender,
        )
        session.commit()

    return {
        "accepted": True,
        "ingest": ingest_result,
        "trigger": trigger_result,
        "sent_outboxes": sent_outboxes,
    }


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

    async def start(self) -> None:
        self._register_handlers()
        await self.ws_client.connect()

    async def stop(self) -> None:
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
        with self.session_factory() as session:
            result = await process_incoming_aibot_frame(
                session,
                frame=frame,
                settings=self.settings,
                dify_client=self.dify_client,
                sender=self.sender,
                auto_send=self.auto_send,
                normalizer=self.normalizer,
            )
            logger.info(
                "Processed AiBot frame accepted=%s matched=%s sent=%s",
                result["accepted"],
                result["trigger"]["matched"],
                len(result["sent_outboxes"]),
            )


async def _send_trigger_outboxes(
    session: Session,
    *,
    trigger_result: dict[str, Any],
    sender: Any,
) -> list[dict[str, Any]]:
    sent = []
    for event in trigger_result.get("events", []):
        outbox_info = event.get("outbox")
        if not isinstance(outbox_info, dict) or not outbox_info.get("outbox_id"):
            continue
        outbox, duplicated = await send_outbox_message_async(
            session,
            outbox_identifier=str(outbox_info["outbox_id"]),
            sender=sender,
        )
        sent.append(outbox_to_dict(outbox, duplicated=duplicated))
    return sent


def _build_ws_client(settings: Settings) -> Any:
    missing = []
    if not settings.wecom_aibot_id:
        missing.append("WECOM_AIBOT_ID")
    if not settings.wecom_aibot_secret:
        missing.append("WECOM_AIBOT_SECRET")
    if missing:
        raise ValueError(f"{', '.join(missing)} are required for AiBot long connection")

    try:
        from wecom_aibot_sdk import WSClient
    except ImportError as exc:
        raise RuntimeError("wecom-aibot-sdk is required for AiBot long connection") from exc

    return WSClient(
        settings.wecom_aibot_id,
        settings.wecom_aibot_secret,
        ws_url=settings.wecom_aibot_ws_url or "",
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


def _chat_type(value: Any) -> str:
    if str(value) == "2":
        return "group"
    if str(value) == "1":
        return "single"
    return str(value)
