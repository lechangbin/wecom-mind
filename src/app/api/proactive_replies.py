from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.request_context import get_request_id
from app.core.responses import success_response
from app.db.session import get_session
from app.proactive_replies.schemas import ProactiveReplyRunRequest
from app.proactive_replies.services import run_proactive_reply

router = APIRouter(prefix="/api/proactive-replies", tags=["proactive-replies"])


@router.post("/run")
def run_proactive_replies(
    payload: ProactiveReplyRunRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    result = run_proactive_reply(
        session,
        payload=payload,
        dify_client=request.app.state.dify_client,
    )
    return success_response(result, request_id=get_request_id())
