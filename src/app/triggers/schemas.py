from typing import Any

from pydantic import BaseModel


class TriggerEvaluateRequest(BaseModel):
    message_id: int | None = None
    chatid: str | None = None
    userid: str | None = None
    content_text: str | None = None
    create_time: str | None = None
    message: dict[str, Any] | None = None

    def resolved_message_id(self) -> int | None:
        if self.message_id is not None:
            return self.message_id
        if self.message and self.message.get("message_id") is not None:
            return int(self.message["message_id"])
        return None
