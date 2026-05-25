from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    DUPLICATED = "DUPLICATED"
    EXTERNAL_API_ERROR = "EXTERNAL_API_ERROR"
    DIFY_OUTPUT_INVALID = "DIFY_OUTPUT_INVALID"
    WECOM_SEND_FAILED = "WECOM_SEND_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


HTTP_STATUS_BY_ERROR_CODE = {
    ErrorCode.INVALID_ARGUMENT: 400,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.DUPLICATED: 409,
    ErrorCode.EXTERNAL_API_ERROR: 502,
    ErrorCode.DIFY_OUTPUT_INVALID: 422,
    ErrorCode.WECOM_SEND_FAILED: 502,
    ErrorCode.INTERNAL_ERROR: 500,
}


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: Any | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.status_code = status_code or HTTP_STATUS_BY_ERROR_CODE[code]
