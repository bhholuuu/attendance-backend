import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.errors")


def _safe_error_value(value):
    """Make a Pydantic error ``ctx`` value JSON-serializable.

    Pydantic v2 embeds the raised exception (e.g. a ValueError from a
    ``field_validator``) inside ``ctx['error']``. Exceptions are not JSON
    serializable and must be flattened to their message before building a
    response, otherwise the error handler itself would raise (HTTP 500).
    """
    if isinstance(value, BaseException):
        return str(value)
    if isinstance(value, dict):
        return {k: _safe_error_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_error_value(v) for v in value]
    return value


def _safe_validation_errors(errors):
    """Return validation errors with their ``ctx`` contents JSON-serializable."""
    return [{**e, "ctx": _safe_error_value(e.get("ctx") or {})} for e in errors]


# Keys on a Pydantic error dict that may carry submitted input values and must
# never be written to application logs (PART 15/privacy: passwords, phone
# numbers and other PII should not be persisted in logs).
_LOGGABLE_STRIP_KEYS = ("input", "url", "ctx")


def _loggable_errors(errors):
    """Return a log-safe copy of validation errors without submitted values.

    Pydantic v2 ``exc.errors()`` embeds the raw submitted ``input`` value for
    type/validation failures (e.g. a whole login or student-create body). Those
    must not reach application logs. Locations, error types and messages are
    kept so operators can still diagnose the failure.
    """
    loggable = []
    for e in errors:
        safe = {k: v for k, v in e.items() if k not in _LOGGABLE_STRIP_KEYS}
        loggable.append(safe)
    return loggable


def _error_response(*, status_code: int, code: str, message: str, detail=None) -> JSONResponse:
    """Build a consistent JSON error envelope.

    The top-level ``detail`` is preserved for backward compatibility with
    existing clients while ``error`` carries a stable machine-readable code.
    """
    body = {
        "detail": detail if detail is not None else message,
        "error": {"code": code, "message": message},
    }
    return JSONResponse(status_code=status_code, content=body)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach global exception handlers producing consistent JSON errors."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        # Preserve the existing `detail` for API clients while normalizing the
        # envelope so every error carries a stable `error.code`. Headers such
        # as Retry-After (429 responses) must be forwarded unchanged.
        response = _error_response(
            status_code=exc.status_code,
            code=_code_for_status(exc.status_code),
            message=exc.detail if isinstance(exc.detail, str) else "Request failed",
            detail=exc.detail,
        )
        if exc.headers:
            for key, value in exc.headers.items():
                response.headers[key] = value
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        logger.warning("Validation error on %s: %s", request.url.path, _loggable_errors(exc.errors()))
        # FastAPI's default 422 detail shape is preserved for compatibility.
        # Note: raw exc.errors() may contain non-serializable exceptions in
        # ctx (from field validators), so sanitize before rendering.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": _safe_validation_errors(exc.errors()),
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed",
                },
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        # Never leak internal details to clients; log the full traceback.
        logger.exception("Unhandled error on %s", request.url.path)
        return _error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="INTERNAL_ERROR",
            message="An unexpected error occurred.",
        )


def _code_for_status(status_code: int) -> str:
    codes = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
    }
    return codes.get(status_code, "HTTP_ERROR")
