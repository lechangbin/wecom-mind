from fastapi import APIRouter, Request

from app.core.errors import AppError, ErrorCode
from app.core.request_context import get_request_id
from app.core.responses import success_response
from app.db.connection import check_database_connection

router = APIRouter(tags=["system"])


@router.get("/health")
def health_check(request: Request):
    database_ok = check_database_connection(request.app.state.engine)
    if not database_ok:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            "Database connection failed",
            status_code=503,
        )

    return success_response(
        {
            "status": "ok",
            "app_env": request.app.state.settings.app_env,
            "database": "ok",
        },
        request_id=get_request_id(),
    )
