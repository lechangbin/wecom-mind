import asyncio
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings
from app.dify.client import DifyClient
from app.wecom.message_reconcile import WeComHistoryMessageSource, run_message_reconcile_once

logger = logging.getLogger(__name__)


class MessageReconcileWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: sessionmaker[Session],
        message_source: WeComHistoryMessageSource,
        dify_client: DifyClient,
        sender: Any | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.message_source = message_source
        self.dify_client = dify_client
        self.sender = sender
        self._stop_event = asyncio.Event()

    def run_once(self, *, now: datetime | None = None) -> dict[str, Any]:
        chatids = self.settings.message_reconcile_chatid_list
        results = []
        for chatid in chatids:
            with self.session_factory() as session:
                item = run_message_reconcile_once(
                    session,
                    chatid=chatid,
                    settings=self.settings,
                    message_source=self.message_source,
                    dify_client=self.dify_client,
                    auto_enqueue=self.settings.wecom_message_reconcile_auto_enqueue,
                    auto_send=self.settings.wecom_message_reconcile_auto_send,
                    sender=self.sender,
                    now=now,
                )
                item["chatid"] = chatid
                results.append(item)
        return {"chat_count": len(chatids), "results": results}

    async def run_forever(self) -> None:
        if not self.settings.wecom_message_reconcile_enabled:
            logger.info("Message reconcile worker disabled")
            return
        if not self.settings.message_reconcile_chatid_list:
            logger.warning("Message reconcile worker enabled without configured chatids")
            return

        logger.info(
            "Message reconcile worker started chat_count=%s interval=%ss",
            len(self.settings.message_reconcile_chatid_list),
            self.settings.wecom_message_reconcile_interval_seconds,
        )
        while not self._stop_event.is_set():
            try:
                result = await asyncio.to_thread(self.run_once)
                logger.info("Message reconcile cycle finished: %s", result)
            except Exception:
                logger.exception("Message reconcile cycle failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.settings.wecom_message_reconcile_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._stop_event.set()
