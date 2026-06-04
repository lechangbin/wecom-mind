import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from app.ai_memory.full_test import default_target_date, run_ai_memory_full_test_once
from app.config.settings import get_settings
from app.core.logging import configure_logging
from app.db.connection import create_engine_from_settings, initialize_database
from app.db.defaults import initialize_default_records
from app.db.session import build_session_factory
from app.dify.client import build_dify_client

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    if not settings.ai_memory_full_test_enabled:
        logger.info("AI memory full-test worker disabled")
        return

    engine = create_engine_from_settings(settings)
    initialize_database(engine)
    SessionLocal = build_session_factory(engine)
    initialize_default_records(SessionLocal)
    dify_client = build_dify_client(settings)
    interval = max(settings.ai_memory_full_test_interval_seconds, 60)

    logger.info(
        "AI memory full-test worker started interval_seconds=%s chatids=%s run_profiles=%s",
        interval,
        settings.ai_memory_full_test_chatid_list or "all",
        settings.ai_memory_full_test_run_profiles,
    )

    while True:
        target_date = default_target_date(settings.ai_memory_full_test_days_back)
        try:
            with SessionLocal() as session:
                result = run_ai_memory_full_test_once(
                    session,
                    target_date=target_date,
                    chatids=settings.ai_memory_full_test_chatid_list or None,
                    run_profiles=settings.ai_memory_full_test_run_profiles,
                    dify_client=dify_client,
                )
            logger.info("AI memory full-test run completed: %s", result)
        except Exception:
            logger.exception("AI memory full-test run failed")

        time.sleep(interval)


if __name__ == "__main__":
    main()
