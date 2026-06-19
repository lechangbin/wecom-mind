from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.request_context import get_request_id
from app.core.responses import success_response
from app.db.session import get_session
from app.profiles.schemas import UserProfileAnalyzeRequest, UserProfileGenerateRequest
from app.profiles.services import (
    get_latest_user_profile,
    list_user_profile_versions,
    run_recent_user_profile_generation,
    run_user_profile_analysis,
)

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


@router.post("/api/users/{userid}/profile/generate")
def generate_recent_user_profile(
    userid: str,
    request: Request,
    payload: UserProfileGenerateRequest | None = None,
    session: Session = Depends(get_session),
):
    result = run_recent_user_profile_generation(
        session,
        userid=userid,
        dify_client=request.app.state.dify_client,
        force=payload.force if payload else False,
    )
    return success_response(result, request_id=get_request_id())


@router.get("/api/users/{userid}/profile")
def get_user_profile(userid: str, session: Session = Depends(get_session)):
    result = get_latest_user_profile(session, userid)
    return success_response(result, request_id=get_request_id())


@router.get("/api/users/{userid}/profile/versions")
def user_profile_versions(
    userid: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    result = list_user_profile_versions(
        session,
        userid,
        limit=limit,
        offset=offset,
    )
    return success_response(result, request_id=get_request_id())
