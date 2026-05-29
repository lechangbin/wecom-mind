import logging

from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import configure_logging, redact_sensitive_values
from app.core.request_context import reset_request_id, set_request_id
from app.core.responses import error_response, success_response
from app.core.schema_validator import JsonSchemaValidationError, validate_json_schema
from app.db.connection import check_database_connection, create_engine_from_settings
from app.main import create_app


def test_settings_read_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./tmp/test.db")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DIFY_API_KEY", "dify-secret")

    settings = Settings()

    assert settings.app_env == "test"
    assert settings.database_url == "sqlite:///./tmp/test.db"
    assert settings.log_level == "DEBUG"
    assert settings.dify_api_key == "dify-secret"


def test_settings_exposes_reply_and_intent_aibot_roles():
    settings = Settings(
        wecom_aibot_id="legacy-bot",
        wecom_aibot_secret="legacy-secret",
        wecom_reply_aibot_id="reply-bot",
        wecom_reply_aibot_secret="reply-secret",
        wecom_reply_aibot_name="回复机器人",
        wecom_intent_aibot_id="intent-bot",
        wecom_intent_aibot_secret="intent-secret",
        wecom_intent_aibot_name="意图机器人",
        wecom_bot_id="fetch-bot",
        wecom_bot_secret="fetch-secret",
    )

    assert settings.effective_reply_aibot_id == "reply-bot"
    assert settings.effective_reply_aibot_secret == "reply-secret"
    assert settings.effective_reply_aibot_name == "回复机器人"
    assert settings.effective_intent_aibot_id == "intent-bot"
    assert settings.effective_intent_aibot_secret == "intent-secret"
    assert settings.effective_intent_aibot_name == "意图机器人"


def test_settings_keeps_legacy_aibot_fallbacks():
    settings = Settings(
        wecom_aibot_id="legacy-bot",
        wecom_aibot_secret="legacy-secret",
        wecom_aibot_name="机器人",
    )

    assert settings.effective_reply_aibot_id == "legacy-bot"
    assert settings.effective_reply_aibot_secret == "legacy-secret"
    assert settings.effective_reply_aibot_name == "机器人"
    assert settings.effective_intent_aibot_id == "legacy-bot"
    assert settings.effective_intent_aibot_secret == "legacy-secret"
    assert settings.effective_intent_aibot_name == "机器人"


def test_message_reconcile_defaults_are_short_window():
    settings = Settings(_env_file=None)

    assert settings.wecom_message_reconcile_enabled is False
    assert settings.wecom_message_reconcile_chatids is None
    assert settings.message_reconcile_chatid_list == []
    assert settings.wecom_message_reconcile_interval_seconds == 10
    assert settings.wecom_message_reconcile_lookback_seconds == 12
    assert settings.wecom_message_reconcile_overlap_seconds == 2
    assert settings.wecom_message_reconcile_pages == 1
    assert settings.wecom_message_reconcile_auto_enqueue is True
    assert settings.wecom_message_reconcile_auto_send is False
    assert settings.wecom_mcp_config_endpoint == (
        "https://qyapi.weixin.qq.com/cgi-bin/aibot/cli/get_mcp_config"
    )


def test_message_reconcile_chatid_list_parses_csv():
    settings = Settings(wecom_message_reconcile_chatids=" CHAT_A,CHAT_B ,, CHAT_C ")

    assert settings.message_reconcile_chatid_list == ["CHAT_A", "CHAT_B", "CHAT_C"]


def test_response_helpers_include_request_id():
    assert success_response({"ok": True}, request_id="req-1") == {
        "success": True,
        "request_id": "req-1",
        "data": {"ok": True},
        "error": None,
    }

    error = error_response(ErrorCode.INVALID_ARGUMENT, "Bad input", request_id="req-2")

    assert error["success"] is False
    assert error["request_id"] == "req-2"
    assert error["error"]["code"] == "INVALID_ARGUMENT"


def test_json_schema_validator_accepts_valid_payload_and_rejects_invalid_payload():
    schema = {
        "type": "object",
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
        "additionalProperties": False,
    }

    assert validate_json_schema({"answer": "hello"}, schema) == {"answer": "hello"}

    try:
        validate_json_schema({"answer": 123}, schema)
    except JsonSchemaValidationError as exc:
        assert exc.code == ErrorCode.INVALID_ARGUMENT
        assert "answer" in exc.message
    else:
        raise AssertionError("invalid schema payload should fail")


def test_database_connection_check_uses_configured_database(tmp_path):
    settings = Settings(app_env="test", database_url=f"sqlite:///{tmp_path / 'app.db'}")
    engine = create_engine_from_settings(settings)

    assert check_database_connection(engine) is True


def test_health_endpoint_returns_unified_response_with_request_id(tmp_path):
    settings = Settings(app_env="test", database_url=f"sqlite:///{tmp_path / 'health.db'}")
    app = create_app(settings=settings)
    client = TestClient(app)

    response = client.get("/health", headers={"X-Request-ID": "req-health"})

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "request_id": "req-health",
        "data": {
            "status": "ok",
            "app_env": "test",
            "database": "ok",
        },
        "error": None,
    }


def test_application_errors_return_unified_error_response(tmp_path):
    settings = Settings(app_env="test", database_url=f"sqlite:///{tmp_path / 'errors.db'}")
    app = create_app(settings=settings)

    @app.get("/raise-app-error")
    def raise_app_error():
        raise AppError(ErrorCode.FORBIDDEN, "No access")

    client = TestClient(app)
    response = client.get("/raise-app-error", headers={"X-Request-ID": "req-error"})

    assert response.status_code == 403
    assert response.json()["success"] is False
    assert response.json()["request_id"] == "req-error"
    assert response.json()["error"] == {
        "code": "FORBIDDEN",
        "message": "No access",
        "details": None,
    }


def test_sensitive_values_are_redacted_from_logs():
    sanitized = redact_sensitive_values(
        {
            "dify_api_key": "dify-secret",
            "nested": {"access_token": "token-secret"},
            "message": "safe",
        }
    )

    assert sanitized["dify_api_key"] == "***REDACTED***"
    assert sanitized["nested"]["access_token"] == "***REDACTED***"
    assert sanitized["message"] == "safe"


def test_logging_records_include_current_request_id(caplog):
    configure_logging(Settings(app_env="test", database_url="sqlite:///:memory:"))
    token = set_request_id("req-log")

    try:
        with caplog.at_level(logging.INFO):
            logging.getLogger("app.child").info("hello")
    finally:
        reset_request_id(token)

    assert caplog.records[-1].request_id == "req-log"
