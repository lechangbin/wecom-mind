import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from app.config.settings import get_settings
from app.core.logging import configure_logging
from app.db.connection import create_engine_from_settings, initialize_database
from app.db.defaults import initialize_default_records
from app.db.session import build_session_factory
from app.dify.client import build_dify_client
from app.outbound.sender import WeComAiBotWsMessageSender
from app.wecom.aibot import WeComAiBotLongConnectionWorker, _build_ws_client

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings)

    engine = create_engine_from_settings(settings)
    initialize_database(engine)
    session_factory = build_session_factory(engine)
    initialize_default_records(session_factory)

    ws_client = _build_ws_client(settings)
    sender = WeComAiBotWsMessageSender(ws_client=ws_client)
    worker = WeComAiBotLongConnectionWorker(
        settings=settings,
        session_factory=session_factory,
        dify_client=build_dify_client(settings),
        sender=sender,
        ws_client_factory=lambda _settings: ws_client,
    )

    await worker.start()
    logger.info("WeCom AiBot long-connection worker started")
    try:
        await asyncio.Event().wait()
    finally:
        await worker.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("WeCom AiBot long-connection worker stopped")
