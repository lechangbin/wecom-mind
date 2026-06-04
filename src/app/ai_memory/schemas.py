from pydantic import BaseModel, Field


class AiMemoryFullTestRunRequest(BaseModel):
    target_date: str | None = Field(default=None, description="YYYY-MM-DD; defaults to worker date")
    chatids: list[str] | None = None
    run_profiles: bool = True
