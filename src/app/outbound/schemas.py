from typing import Any

from pydantic import BaseModel, Field


class OutboxCreateRequest(BaseModel):
    scene: str = Field(min_length=1)
    chatid: str = Field(min_length=1)
    target_userids: list[str] = Field(default_factory=list)
    msgtype: str = Field(min_length=1)
    content: dict[str, Any]
    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    idempotency_key: str | None = None
    outbox_id: str | None = None
