import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.admin_analytics import router as admin_analytics_router
from app.api.ai_runs import router as ai_runs_router
from app.api.health import router as health_router
from app.api.outbox import router as outbox_router
from app.api.proactive_replies import router as proactive_replies_router
from app.api.triggers import router as triggers_router
from app.api.wecom import router as wecom_router
from app.config.settings import Settings, get_settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import configure_logging
from app.core.request_context import (
    REQUEST_ID_HEADER,
    get_request_id,
    new_request_id,
    reset_request_id,
    set_request_id,
)
from app.core.responses import error_response
from app.db.connection import create_engine_from_settings, initialize_database
from app.db.defaults import initialize_default_records
from app.db.session import build_session_factory
from app.dify.client import build_dify_client
from app.outbound.sender import build_wecom_message_sender
from app.wecom.verifier import build_wecom_callback_verifier

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(title="WeCom Dify Bot Backend", version="0.1.0")
    app.state.settings = settings
    app.state.engine = create_engine_from_settings(settings)
    initialize_database(app.state.engine)
    app.state.SessionLocal = build_session_factory(app.state.engine)
    initialize_default_records(app.state.SessionLocal)
    app.state.wecom_callback_verifier = build_wecom_callback_verifier(settings)
    app.state.dify_client = build_dify_client(settings)
    app.state.wecom_message_sender = build_wecom_message_sender(settings)

    register_middleware(app)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(wecom_router)
    app.include_router(triggers_router)
    app.include_router(ai_runs_router)
    app.include_router(outbox_router)
    app.include_router(proactive_replies_router)
    app.include_router(admin_analytics_router)

    return app


def register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
        token = set_request_id(request_id)

        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            reset_request_id(token)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(
                exc.code,
                exc.message,
                request_id=get_request_id(),
                details=exc.details,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content=error_response(
                ErrorCode.INVALID_ARGUMENT,
                "Request validation failed",
                request_id=get_request_id(),
                details=exc.errors(),
            ),
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(_request: Request, exc: HTTPException):
        code = _http_status_to_error_code(exc.status_code)
        message = str(exc.detail) if exc.detail else code.value
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(code, message, request_id=get_request_id()),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_request: Request, exc: Exception):
        logger.exception("Unhandled application error: %s", exc)
        return JSONResponse(
            status_code=500,
            content=error_response(
                ErrorCode.INTERNAL_ERROR,
                "Internal server error",
                request_id=get_request_id(),
            ),
        )


def _http_status_to_error_code(status_code: int) -> ErrorCode:
    return {
        400: ErrorCode.INVALID_ARGUMENT,
        401: ErrorCode.UNAUTHORIZED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NOT_FOUND,
        409: ErrorCode.DUPLICATED,
    }.get(status_code, ErrorCode.INTERNAL_ERROR)


app = create_app()
