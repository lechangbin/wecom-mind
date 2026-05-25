import logging
from collections.abc import Mapping, Sequence
from typing import Any

from app.config.settings import Settings
from app.core.request_context import get_request_id

SENSITIVE_KEYWORDS = (
    "secret",
    "token",
    "api_key",
    "access_token",
    "encoding_aes_key",
    "password",
)
REDACTED = "***REDACTED***"


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return any(keyword in normalized for keyword in SENSITIVE_KEYWORDS)


def redact_sensitive_values(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: REDACTED if _is_sensitive_key(str(key)) else redact_sensitive_values(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact_sensitive_values(item) for item in value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [redact_sensitive_values(item) for item in value]
    return value


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        record.args = redact_sensitive_values(record.args)
        record.msg = redact_sensitive_values(record.msg)
        return True


def _install_log_record_factory() -> None:
    current_factory = logging.getLogRecordFactory()
    if getattr(current_factory, "_wecom_bot_context_factory", False):
        return

    def record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = current_factory(*args, **kwargs)
        record.request_id = get_request_id()
        record.args = redact_sensitive_values(record.args)
        record.msg = redact_sensitive_values(record.msg)
        return record

    record_factory._wecom_bot_context_factory = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(record_factory)


def configure_logging(settings: Settings) -> None:
    _install_log_record_factory()

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s [request_id=%(request_id)s] %(name)s: %(message)s",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level)

    if not any(isinstance(filter_, RequestContextFilter) for filter_ in root_logger.filters):
        root_logger.addFilter(RequestContextFilter())
