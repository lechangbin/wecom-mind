from pydantic import BaseModel, Field


class ProactiveReplyTimeRange(BaseModel):
    start: str = Field(min_length=1)
    end: str = Field(min_length=1)


class ProactiveReplyRunRequest(BaseModel):
    chatid: str = Field(min_length=1)
    time_range: ProactiveReplyTimeRange
    auto_enqueue: bool = True
