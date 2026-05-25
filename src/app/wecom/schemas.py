from typing import Any

from pydantic import BaseModel, Field


class MessageIngestRequest(BaseModel):
    source: str = Field(default="mcp", min_length=1)
    idempotency_key: str = Field(min_length=1)
    raw_message: dict[str, Any]
