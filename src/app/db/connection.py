from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import StaticPool

from app.config.settings import Settings


def _ensure_sqlite_parent_directory(database_url: str) -> None:
    url = make_url(database_url)
    if url.drivername != "sqlite" or not url.database or url.database == ":memory:":
        return

    Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def create_engine_from_settings(settings: Settings) -> Engine:
    _ensure_sqlite_parent_directory(settings.database_url)

    url = make_url(settings.database_url)
    kwargs = {"pool_pre_ping": True, "future": True}
    if url.drivername.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url.database in (None, "", ":memory:"):
            kwargs["poolclass"] = StaticPool

    return create_engine(settings.database_url, **kwargs)


def initialize_database(engine: Engine) -> None:
    from app.db.models import Base

    Base.metadata.create_all(bind=engine)
    _ensure_lightweight_schema_upgrades(engine)


def _ensure_lightweight_schema_upgrades(engine: Engine) -> None:
    url = make_url(str(engine.url))
    if not url.drivername.startswith("sqlite"):
        return

    with engine.begin() as connection:
        workflow_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(ai_workflows)")
        }
        if "dify_webhook_url" not in workflow_columns:
            connection.exec_driver_sql(
                "ALTER TABLE ai_workflows ADD COLUMN dify_webhook_url VARCHAR(1024)"
            )

        message_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(messages)")
        }
        if "sender_type" not in message_columns:
            connection.exec_driver_sql(
                "ALTER TABLE messages ADD COLUMN sender_type VARCHAR(32) "
                "NOT NULL DEFAULT 'user'"
            )
        if "bot_role" not in message_columns:
            connection.exec_driver_sql(
                "ALTER TABLE messages ADD COLUMN bot_role VARCHAR(32)"
            )
        if "business_identity_key" not in message_columns:
            connection.exec_driver_sql(
                "ALTER TABLE messages ADD COLUMN business_identity_key VARCHAR(128)"
            )
        if "canonical_message_id" not in message_columns:
            connection.exec_driver_sql(
                "ALTER TABLE messages ADD COLUMN canonical_message_id INTEGER "
                "REFERENCES messages(id)"
            )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_messages_business_identity_key "
            "ON messages (business_identity_key)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_messages_canonical_message_id "
            "ON messages (canonical_message_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_messages_chatid_sender_type_create_time "
            "ON messages (chatid, sender_type, create_time)"
        )

        reply_session_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(wecom_reply_sessions)"
            )
        }
        if reply_session_columns and "mention_request_id" not in reply_session_columns:
            connection.exec_driver_sql(
                "ALTER TABLE wecom_reply_sessions "
                "ADD COLUMN mention_request_id INTEGER REFERENCES mention_requests(id)"
            )
        if reply_session_columns:
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_wecom_reply_sessions_mention_request_id "
                "ON wecom_reply_sessions (mention_request_id)"
            )


def check_database_connection(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False
