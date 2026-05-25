from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.request_context import get_request_id
from app.core.responses import success_response
from app.db.session import get_session
from app.profiles.schemas import UserProfileAnalyzeRequest
from app.profiles.services import get_latest_user_profile, run_user_profile_analysis

router = APIRouter(tags=["profiles"])


@router.post("/api/profiles/analyze/run")
def run_profile_analysis(
    payload: UserProfileAnalyzeRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    result = run_user_profile_analysis(
        session,
        payload=payload,
        dify_client=request.app.state.dify_client,
    )
    return success_response(result, request_id=get_request_id())


@router.get("/api/users/{userid}/profile")
def get_user_profile(userid: str, session: Session = Depends(get_session)):
    result = get_latest_user_profile(session, userid)
    return success_response(result, request_id=get_request_id())
