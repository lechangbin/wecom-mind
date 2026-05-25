from pydantic import BaseModel, Field


class ScheduledIntentTimeRange(BaseModel):
    start: str = Field(min_length=1)
    end: str = Field(min_length=1)


class ScheduledIntentRunRequest(BaseModel):
    chatid: str = Field(min_length=1)
    job_id: str | None = None
    time_range: ScheduledIntentTimeRange
    auto_enqueue: bool = True
