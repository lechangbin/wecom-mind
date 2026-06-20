import base64
import hashlib
import hmac
import json
from time import time
from typing import Any

from fastapi import Request

from app.config.settings import Settings
from app.core.errors import AppError, ErrorCode

ADMIN_AUTH_PUBLIC_PREFIXES = ("/api/admin/auth",)
ADMIN_AUTH_PROTECTED_PREFIXES = (
    "/api/admin",
    "/api/system",
    "/api/dashboard",
    "/api/chats",
    "/api/users",
    "/api/stats",
    "/api/messages",
    "/api/ai-runs",
    "/api/outbox-messages",
    "/api/conversations",
    "/api/proactive-replies",
    "/api/scheduled-intents",
    "/api/ai-memory",
)


def is_admin_auth_enabled(settings: Settings) -> bool:
    return settings.app_env != "test" and bool(settings.admin_username and settings.admin_password)


def admin_path_requires_auth(path: str) -> bool:
    if any(path.startswith(prefix) for prefix in ADMIN_AUTH_PUBLIC_PREFIXES):
        return False
    return any(path.startswith(prefix) for prefix in ADMIN_AUTH_PROTECTED_PREFIXES)


def authenticate_admin(settings: Settings, username: str, password: str) -> None:
    if not is_admin_auth_enabled(settings):
        raise AppError(
            ErrorCode.INVALID_ARGUMENT,
            "Admin auth is not configured",
        )
    username_ok = hmac.compare_digest(username, settings.admin_username or "")
    password_ok = hmac.compare_digest(password, settings.admin_password or "")
    if not username_ok or not password_ok:
        raise AppError(ErrorCode.UNAUTHORIZED, "Invalid admin username or password")


def create_admin_session_token(settings: Settings) -> str:
    if not settings.admin_username:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "Admin auth is not configured")
    payload = {
        "sub": settings.admin_username,
        "exp": int(time()) + settings.admin_session_ttl_seconds,
    }
    body = _base64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = _sign(settings, body)
    return f"{body}.{signature}"


def validate_admin_session_token(settings: Settings, token: str | None) -> dict[str, Any]:
    if not is_admin_auth_enabled(settings):
        return {"auth_enabled": False, "authenticated": True, "username": None}
    if not token or "." not in token:
        raise AppError(ErrorCode.UNAUTHORIZED, "Admin login required")

    body, signature = token.rsplit(".", 1)
    expected_signature = _sign(settings, body)
    if not hmac.compare_digest(signature, expected_signature):
        raise AppError(ErrorCode.UNAUTHORIZED, "Admin login required")

    try:
        payload = json.loads(_base64url_decode(body).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise AppError(ErrorCode.UNAUTHORIZED, "Admin login required") from exc

    if payload.get("sub") != settings.admin_username:
        raise AppError(ErrorCode.UNAUTHORIZED, "Admin login required")
    if int(payload.get("exp") or 0) < int(time()):
        raise AppError(ErrorCode.UNAUTHORIZED, "Admin session expired")

    return {
        "auth_enabled": True,
        "authenticated": True,
        "username": settings.admin_username,
    }


def require_admin_session(request: Request) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    token = request.cookies.get(settings.admin_session_cookie_name)
    return validate_admin_session_token(settings, token)


def _sign(settings: Settings, body: str) -> str:
    secret = settings.admin_session_secret or settings.admin_password or ""
    digest = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256)
    return _base64url_encode(digest.digest())


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
