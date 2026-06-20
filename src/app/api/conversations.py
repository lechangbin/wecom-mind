from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.conversations.schemas import ConversationSegmentRunRequest
from app.conversations.services import (
    conversation_segment_to_dict,
    get_conversation_segment,
    list_conversation_messages,
    list_conversation_segments,
    run_conversation_segmentation,
)
from app.core.request_context import get_request_id
from app.core.responses import success_response
from app.db.session import get_session

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.post("/segment/run")
def run_segment(
    payload: ConversationSegmentRunRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    result = run_conversation_segmentation(
        session,
        payload=payload,
        dify_client=request.app.state.dify_client,
    )
    return success_response(result, request_id=get_request_id())


@router.get("")
def list_conversations(
    chatid: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    result = list_conversation_segments(
        session,
        chatid=chatid,
        start_date=start_date,
        end_date=end_date,
        q=q,
        limit=limit,
        offset=offset,
    )
    return success_response(result, request_id=get_request_id())


@router.get("/{conversation_no}/messages")
def get_conversation_messages(
    conversation_no: str,
    session: Session = Depends(get_session),
):
    result = list_conversation_messages(session, conversation_no=conversation_no)
    return success_response(result, request_id=get_request_id())


@router.get("/{conversation_no}")
def get_conversation(conversation_no: str, session: Session = Depends(get_session)):
    segment = get_conversation_segment(session, conversation_no=conversation_no)
    return success_response(conversation_segment_to_dict(segment), request_id=get_request_id())
