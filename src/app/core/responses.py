from typing import Any

from app.core.errors import ErrorCode


def success_response(data: Any = None, *, request_id: str) -> dict[str, Any]:
    return {
        "success": True,
        "request_id": request_id,
        "data": data,
        "error": None,
    }


def error_response(
    code: ErrorCode,
    message: str,
    *,
    request_id: str,
    details: Any | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "request_id": request_id,
        "data": None,
        "error": {
            "code": code.value,
            "message": message,
            "details": details,
        },
    }
