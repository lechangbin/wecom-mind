from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.admin_frontend.services import list_messages
from app.core.request_context import get_request_id
from app.core.responses import success_response
from app.db.session import get_session

router = APIRouter(prefix="/api/messages", tags=["messages"])


@router.get("")
def messages(
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    chatid: str | None = Query(default=None),
    sender_type: str | None = Query(default=None),
    mentioned_bot: bool | None = Query(default=None),
    include_source_duplicates: bool = Query(default=False),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    result = list_messages(
        session,
        start_date=start_date,
        end_date=end_date,
        chatid=chatid,
        sender_type=sender_type,
        mentioned_bot=mentioned_bot,
        include_source_duplicates=include_source_duplicates,
        q=q,
        limit=limit,
        offset=offset,
    )
    return success_response(result, request_id=get_request_id())
