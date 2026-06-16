from pydantic import BaseModel
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.admin_auth.services import (
    authenticate_admin,
    create_admin_session_token,
    is_admin_auth_enabled,
    require_admin_session,
)
from app.core.request_context import get_request_id
from app.core.responses import success_response

router = APIRouter(prefix="/api/admin/auth", tags=["admin-auth"])


class AdminLoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(payload: AdminLoginRequest, request: Request):
    settings = request.app.state.settings
    authenticate_admin(settings, payload.username, payload.password)
    token = create_admin_session_token(settings)
    content = success_response(
        {
            "auth_enabled": True,
            "authenticated": True,
            "username": settings.admin_username,
        },
        request_id=get_request_id(),
    )
    response = JSONResponse(content=content)
    response.set_cookie(
        key=settings.admin_session_cookie_name,
        value=token,
        max_age=settings.admin_session_ttl_seconds,
        httponly=True,
        secure=settings.app_base_url.startswith("https://"),
        samesite="lax",
        path="/",
    )
    return response


@router.get("/me")
def me(request: Request):
    if not is_admin_auth_enabled(request.app.state.settings):
        return success_response(
            {"auth_enabled": False, "authenticated": True, "username": None},
            request_id=get_request_id(),
        )
    return success_response(require_admin_session(request), request_id=get_request_id())


@router.post("/logout")
def logout(request: Request):
    settings = request.app.state.settings
    response = JSONResponse(
        content=success_response(
            {
                "auth_enabled": is_admin_auth_enabled(settings),
                "authenticated": False,
                "username": None,
            },
            request_id=get_request_id(),
        )
    )
    response.delete_cookie(settings.admin_session_cookie_name, path="/")
    return response
