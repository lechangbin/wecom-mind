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
    database_url: str = "sqlite:///./data/app.db"
    redis_url: str | None = None

    wecom_corp_id: str | None = None
    wecom_mcp_token: str | None = None
    wecom_mcp_encoding_aes_key: str | None = None
    wecom_aibot_id: str | None = None
    wecom_aibot_secret: str | None = None
    wecom_aibot_ws_url: str | None = None
    wecom_aibot_name: str | None = None
    wecom_mcp_verify_mode: Literal["mock", "real"] = "mock"
    wecom_sender_mode: Literal["mock", "app", "webhook"] = "mock"
    wecom_api_base_url: str = "https://qyapi.weixin.qq.com/cgi-bin"
    wecom_agent_id: str | None = None
    wecom_timeout_seconds: int = 10
    wecom_max_retries: int = 2
    wecom_group_bot_webhook_url: str | None = None

    dify_base_url: str | None = None
    dify_api_key: str | None = None
    dify_client_mode: Literal["mock", "real", "webhook"] = "mock"
    dify_timeout_seconds: int = 30
    dify_max_retries: int = 1
    dify_user: str = "wecom-bot-system"

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO"
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()


@lru_cache
def get_settings() -> Settings:
    return Settings()
