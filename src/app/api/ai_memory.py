from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.ai_memory.full_test import parse_target_date, run_ai_memory_full_test_once
from app.ai_memory.schemas import AiMemoryFullTestRunRequest
from app.core.errors import AppError, ErrorCode
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
    lock = request.app.state.ai_memory_full_test_lock
    state = request.app.state.ai_memory_full_test_state
    if not lock.acquire(blocking=False):
        raise AppError(
            ErrorCode.DUPLICATED,
            "AI memory full-test is already running",
            details=state.get("current") or {"status": "running"},
        )

    settings = request.app.state.settings
    try:
        target_date = parse_target_date(
            payload.target_date,
            days_back=settings.ai_memory_full_test_days_back,
        )
        state["status"] = "running"
        state["current"] = {
            "status": "running",
            "target_date": target_date.isoformat(),
            "chatids": payload.chatids,
            "run_profiles": payload.run_profiles,
        }
        state["last_error"] = None
        result = run_ai_memory_full_test_once(
            session,
            target_date=target_date,
            chatids=payload.chatids,
            run_profiles=payload.run_profiles,
            dify_client=request.app.state.dify_client,
        )
        state["status"] = "succeeded"
        state["last_result"] = result
        state["current"] = None
        return success_response(result, request_id=get_request_id())
    except Exception as exc:
        state["status"] = "failed"
        state["last_error"] = str(exc)
        state["current"] = None
        raise
    finally:
        lock.release()


@router.get("/full-test/status")
def full_test_status(request: Request):
    return success_response(
        request.app.state.ai_memory_full_test_state,
        request_id=get_request_id(),
    )
