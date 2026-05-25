from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.request_context import get_request_id
from app.core.responses import success_response
from app.db.session import get_session
from app.triggers.schemas import TriggerEvaluateRequest
from app.triggers.services import evaluate_triggers

router = APIRouter(prefix="/api/triggers", tags=["triggers"])


@router.post("/evaluate")
def evaluate_trigger_rules(
    payload: TriggerEvaluateRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    result = evaluate_triggers(
        session,
        payload=payload,
        dify_client=request.app.state.dify_client,
    )
    return success_response(result, request_id=get_request_id())
