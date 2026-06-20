from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "local"
    app_base_url: str = "http://127.0.0.1:8000"
    admin_web_host: str = "127.0.0.1"
    admin_web_port: int = 5173
    admin_username: str | None = None
    admin_password: str | None = None
    admin_session_secret: str | None = None
    admin_session_cookie_name: str = "wecom_admin_session"
    admin_session_ttl_seconds: int = 8 * 60 * 60
    database_url: str = "sqlite:///./data/app.db"
    redis_url: str | None = None

    wecom_corp_id: str | None = None
    wecom_mcp_token: str | None = None
    wecom_mcp_encoding_aes_key: str | None = None
    wecom_aibot_id: str | None = None
    wecom_aibot_secret: str | None = None
    wecom_aibot_ws_url: str | None = None
    wecom_aibot_name: str | None = None
    wecom_reply_aibot_id: str | None = None
    wecom_reply_aibot_secret: str | None = None
    wecom_reply_aibot_ws_url: str | None = None
    wecom_reply_aibot_name: str | None = None
    wecom_intent_aibot_id: str | None = None
    wecom_intent_aibot_secret: str | None = None
    wecom_intent_aibot_name: str | None = None
    wecom_bot_id: str | None = None
    wecom_bot_secret: str | None = None
    wecom_mcp_verify_mode: Literal["mock", "real"] = "mock"
    wecom_sender_mode: Literal["mock", "app", "webhook", "aibot_ws"] = "mock"
    wecom_api_base_url: str = "https://qyapi.weixin.qq.com/cgi-bin"
    wecom_agent_id: str | None = None
    wecom_timeout_seconds: int = 10
    wecom_max_retries: int = 2
    wecom_group_bot_webhook_url: str | None = None
    wecom_message_reconcile_enabled: bool = False
    wecom_message_reconcile_chatids: str | None = None
    wecom_message_reconcile_interval_seconds: int = 10
    wecom_message_reconcile_lookback_seconds: int = 12
    wecom_message_reconcile_overlap_seconds: int = 2
    wecom_message_reconcile_pages: int = 1
    wecom_message_reconcile_auto_enqueue: bool = True
    wecom_message_reconcile_auto_send: bool = False
    wecom_message_identity_bucket_seconds: int = 5
    wecom_mention_request_stalled_after_seconds: int = 180
    wecom_mcp_config_endpoint: str = (
        "https://qyapi.weixin.qq.com/cgi-bin/aibot/cli/get_mcp_config"
    )

    dify_base_url: str | None = None
    dify_api_key: str | None = None
    dify_group_knowledge_reply_api_key: str | None = None
    dify_chat_proactive_reminder_api_key: str | None = None
    dify_conversation_boundary_detection_api_key: str | None = None
    dify_user_profile_update_api_key: str | None = None
    dify_client_mode: Literal["mock", "real", "webhook"] = "mock"
    dify_timeout_seconds: int = 120
    dify_max_retries: int = 0
    dify_user: str = "wecom-bot-system"

    ai_memory_full_test_enabled: bool = False
    ai_memory_full_test_chatids: str | None = None
    ai_memory_full_test_interval_seconds: int = 86400
    ai_memory_full_test_days_back: int = 1
    ai_memory_full_test_run_profiles: bool = True

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO"
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @property
    def effective_reply_aibot_id(self) -> str | None:
        return self.wecom_reply_aibot_id or self.wecom_aibot_id

    @property
    def effective_reply_aibot_secret(self) -> str | None:
        return self.wecom_reply_aibot_secret or self.wecom_aibot_secret

    @property
    def effective_reply_aibot_ws_url(self) -> str | None:
        return self.wecom_reply_aibot_ws_url or self.wecom_aibot_ws_url

    @property
    def effective_reply_aibot_name(self) -> str | None:
        return self.wecom_reply_aibot_name or self.wecom_aibot_name

    @property
    def effective_intent_aibot_id(self) -> str | None:
        return self.wecom_intent_aibot_id or self.wecom_bot_id or self.wecom_aibot_id

    @property
    def effective_intent_aibot_secret(self) -> str | None:
        return self.wecom_intent_aibot_secret or self.wecom_bot_secret or self.wecom_aibot_secret

    @property
    def effective_intent_aibot_name(self) -> str | None:
        return self.wecom_intent_aibot_name or self.wecom_aibot_name

    @property
    def message_reconcile_chatid_list(self) -> list[str]:
        if not self.wecom_message_reconcile_chatids:
            return []
        return [
            item.strip()
            for item in self.wecom_message_reconcile_chatids.split(",")
            if item.strip()
        ]

    @property
    def ai_memory_full_test_chatid_list(self) -> list[str]:
        if not self.ai_memory_full_test_chatids:
            return []
        return [
            item.strip()
            for item in self.ai_memory_full_test_chatids.split(",")
            if item.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
