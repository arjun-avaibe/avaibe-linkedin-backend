"""Small helpers shared by the API views: errors, request validation, response shape.

Errors keep the shape the React app expects: {"detail": "..."} with an HTTP status.
Request bodies are validated with the Pydantic schemas in core/schemas.py (422 on invalid input).
"""

from typing import TypeVar

from pydantic import BaseModel, ValidationError
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler as drf_exception_handler

M = TypeVar("M", bound=BaseModel)


class ApiError(APIException):
    def __init__(self, status_code: int, detail):
        self.status_code = status_code
        super().__init__(detail)
        # APIException wraps strings/lists; keep plain structures for the client
        self.detail = detail


def fail(status_code: int, detail) -> None:
    raise ApiError(status_code, detail)


def parse(schema: type[M], data) -> M:
    """Validate a request body with a Pydantic schema; 422 with field errors if invalid."""
    if not isinstance(data, dict):
        fail(422, "Expected a JSON object")
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        fail(422, [{"loc": list(err.get("loc", ())), "msg": err.get("msg", "Invalid value")} for err in e.errors()])


def int_param(request, name: str, default: int | None = None, required: bool = False) -> int | None:
    raw = request.query_params.get(name)
    if raw in (None, ""):
        if required:
            fail(422, f"Missing query parameter '{name}'")
        return default
    try:
        return int(raw)
    except ValueError:
        fail(422, f"'{name}' must be a number")


def choice_param(request, name: str, options: set[str] | list[str], default: str) -> str:
    v = request.query_params.get(name, default)
    if v not in options:
        fail(422, f"'{name}' must be one of {sorted(options)}")
    return v


def exception_handler(exc, context):
    resp = drf_exception_handler(exc, context)
    if resp is not None and isinstance(exc, ApiError):
        resp.data = {"detail": exc.detail}
    elif resp is not None and isinstance(resp.data, dict) and "detail" in resp.data:
        resp.data = {"detail": str(resp.data["detail"])}
    return resp


NO_CONTENT = status.HTTP_204_NO_CONTENT
