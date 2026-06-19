from pydantic import BaseModel, Field


class UserProfileTimeRange(BaseModel):
    start: str = Field(min_length=1)
    end: str = Field(min_length=1)


class UserProfileAnalyzeRequest(BaseModel):
    userid: str = Field(min_length=1)
    mode: str = "incremental"
    time_range: UserProfileTimeRange


class UserProfileGenerateRequest(BaseModel):
    force: bool = False
