from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.ai_memory.full_test import parse_target_date, run_ai_memory_full_test_once
from app.ai_memory.schemas import AiMemoryFullTestRunRequest
from app.core.request_context import get_request_id
from app.core.responses import success_response
from app.db.session import get_session

router = APIRouter(prefix="/api/ai-memory", tags=["ai-memory"])


@router.post("/full-test/run")
def run_full_test(
    payload: AiMemoryFullTestRunRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    settings = request.app.state.settings
    target_date = parse_target_date(
        payload.target_date,
        days_back=settings.ai_memory_full_test_days_back,
    )
    result = run_ai_memory_full_test_once(
        session,
        target_date=target_date,
        chatids=payload.chatids,
        run_profiles=payload.run_profiles,
        dify_client=request.app.state.dify_client,
    )
    return success_response(result, request_id=get_request_id())
