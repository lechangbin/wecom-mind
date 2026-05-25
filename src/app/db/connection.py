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
        columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(ai_workflows)")
        }
        if "dify_webhook_url" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE ai_workflows ADD COLUMN dify_webhook_url VARCHAR(1024)"
            )


def check_database_connection(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False
