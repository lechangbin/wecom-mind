from fastapi import APIRouter, Request

from app.core.request_context import get_request_id
from app.core.responses import success_response
from app.system.services import get_config_summary, get_worker_statuses

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/config-summary")
def config_summary(request: Request):
    return success_response(
        get_config_summary(request.app.state.settings),
        request_id=get_request_id(),
    )


@router.get("/workers")
def workers(request: Request):
    return success_response(
        get_worker_statuses(
            request.app.state.settings,
            request.app.state.ai_memory_full_test_state,
        ),
        request_id=get_request_id(),
    )
