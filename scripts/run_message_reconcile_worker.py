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
from app.outbound.sender import build_wecom_message_sender
from app.wecom.services import backfill_message_sender_classification
from app.wecom.message_reconcile_worker import MessageReconcileWorker
from app.wecom.message_source import WeComMcpMessageSource

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings)

    engine = create_engine_from_settings(settings)
    initialize_database(engine)
    session_factory = build_session_factory(engine)
    initialize_default_records(session_factory)
    with session_factory() as session:
        backfill_message_sender_classification(session, settings)

    source = WeComMcpMessageSource(settings)
    sender = None
    if settings.wecom_sender_mode != "aibot_ws":
        sender = build_wecom_message_sender(settings)

    worker = MessageReconcileWorker(
        settings=settings,
        session_factory=session_factory,
        message_source=source,
        dify_client=build_dify_client(settings),
        sender=sender,
    )
    try:
        await worker.run_forever()
    finally:
        source.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Message reconcile worker stopped")
