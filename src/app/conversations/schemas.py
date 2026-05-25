from pydantic import BaseModel, Field


class ConversationSegmentRunRequest(BaseModel):
    chatid: str = Field(min_length=1)
    start_time: str = Field(min_length=1)
    end_time: str = Field(min_length=1)
    mode: str = "auto"
