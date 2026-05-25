from contextvars import ContextVar
from uuid import uuid4

REQUEST_ID_HEADER = "X-Request-ID"

_request_id: ContextVar[str] = ContextVar("request_id", default="")


def new_request_id() -> str:
    return str(uuid4())


def get_request_id() -> str:
    return _request_id.get()


def set_request_id(request_id: str):
    return _request_id.set(request_id)


def reset_request_id(token) -> None:
    _request_id.reset(token)
