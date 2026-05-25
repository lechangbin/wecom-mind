from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.admin_analytics.services import (
    get_dashboard_overview,
    get_message_stats,
    get_workflow_stats,
    list_chat_messages,
    list_chats,
    list_users,
)
from app.core.request_context import get_request_id
from app.core.responses import success_response
from app.db.session import get_session

router = APIRouter(tags=["admin-analytics"])


@router.get("/api/dashboard/overview")
def dashboard_overview(
    date: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    result = get_dashboard_overview(session, date=date)
    return success_response(result, request_id=get_request_id())


@router.get("/api/chats")
def chats(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    result = list_chats(session, limit=limit, offset=offset)
    return success_response(result, request_id=get_request_id())


@router.get("/api/chats/{chatid}/messages")
def chat_messages(
    chatid: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    result = list_chat_messages(session, chatid=chatid, limit=limit, offset=offset)
    return success_response(result, request_id=get_request_id())


@router.get("/api/users")
def users(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    result = list_users(session, limit=limit, offset=offset)
    return success_response(result, request_id=get_request_id())


@router.get("/api/stats/messages")
def message_stats(
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    group_by: str = Query(default="day"),
    session: Session = Depends(get_session),
):
    result = get_message_stats(session, start=start, end=end, group_by=group_by)
    return success_response(result, request_id=get_request_id())


@router.get("/api/stats/workflows")
def workflow_stats(
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    result = get_workflow_stats(session, start=start, end=end)
    return success_response(result, request_id=get_request_id())
