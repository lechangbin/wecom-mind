from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from app.core.errors import AppError, ErrorCode


class JsonSchemaValidationError(AppError):
    pass


def validate_json_schema(
    payload: Any,
    schema: dict[str, Any],
    *,
    code: ErrorCode = ErrorCode.INVALID_ARGUMENT,
) -> Any:
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    try:
        validator.validate(payload)
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.path)
        message = f"{location}: {exc.message}" if location else exc.message
        raise JsonSchemaValidationError(code, message, details={"path": location}) from exc

    return payload
