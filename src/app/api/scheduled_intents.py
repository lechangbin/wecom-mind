from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.request_context import get_request_id
from app.core.responses import success_response
from app.db.session import get_session
from app.proactive_replies.schemas import ProactiveReplyRunRequest
from app.proactive_replies.services import run_proactive_reply
from app.scheduled_intents.schemas import ScheduledIntentRunRequest
from app.scheduled_intents.services import (
    list_scheduled_intents,
)

router = APIRouter(prefix="/api/scheduled-intents", tags=["scheduled-intents"])


@router.post("/run")
def run_scheduled_intents(
    payload: ScheduledIntentRunRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    result = run_proactive_reply(
        session,
        payload=ProactiveReplyRunRequest(
            chatid=payload.chatid,
            time_range={
                "start": payload.time_range.start,
                "end": payload.time_range.end,
            },
            auto_enqueue=payload.auto_enqueue,
        ),
        dify_client=request.app.state.dify_client,
    )
    result["compatibility_mode"] = "scheduled_intents"
    result.setdefault("intents", [])
    return success_response(result, request_id=get_request_id())


@router.get("")
def list_intents(
    chatid: str | None = Query(default=None),
    status: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    result = list_scheduled_intents(session, chatid=chatid, status=status)
    return success_response(result, request_id=get_request_id())
