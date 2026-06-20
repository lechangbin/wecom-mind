from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.admin_frontend.services import list_reply_tasks, stream_admin_events
from app.core.request_context import get_request_id
from app.core.responses import success_response
from app.db.session import get_session

router = APIRouter(prefix="/api/admin", tags=["admin-frontend"])


@router.get("/reply-tasks")
def reply_tasks(
    status: str | None = Query(default=None),
    task_type: str | None = Query(default=None),
    chatid: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    result = list_reply_tasks(
        session,
        status=status,
        task_type=task_type,
        chatid=chatid,
        start_date=start_date,
        end_date=end_date,
        q=q,
        limit=limit,
        offset=offset,
    )
    return success_response(result, request_id=get_request_id())


@router.get("/events/stream")
def admin_events_stream(request: Request):
    return StreamingResponse(
        stream_admin_events(request.app.state.SessionLocal),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
