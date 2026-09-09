"""Per-request context for auditing (V2.6).

Every HTTP request gets a stable correlation id that is:
  * carried on the response as ``X-Correlation-ID``,
  * stored on every audit log row written while the request is being handled.

A caller-supplied ``X-Correlation-ID`` header is honored (useful for tracing
client/device replays), otherwise a fresh UUID is generated. The request's
client IP is also captured so audit records can show where an action (or a
failed login / denied attempt) came from.
"""

from __future__ import annotations

import contextvars
import uuid
from typing import Optional

from starlette.types import ASGIApp, Receive, Scope, Send

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)
_client_ip: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "client_ip", default=None
)
_actor_user_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "actor_user_id", default=None
)
_actor_username: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "actor_username", default=None
)
_actor_role: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "actor_role", default=None
)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def current_correlation_id() -> str:
    return _correlation_id.get() or ""


def current_client_ip() -> Optional[str]:
    return _client_ip.get()


def current_actor_user_id() -> Optional[int]:
    return _actor_user_id.get()


def current_actor_username() -> Optional[str]:
    return _actor_username.get()


def current_actor_role() -> Optional[str]:
    return _actor_role.get()


def set_actor(*, user_id: Optional[int], username: Optional[str], role: Optional[str]) -> None:
    """Expose the resolved actor for the rest of the request."""
    _actor_user_id.set(user_id)
    _actor_username.set(username)
    _actor_role.set(role)


def reset() -> None:
    """Clear all context for this (finished) request."""
    _correlation_id.set("")
    _client_ip.set(None)
    _actor_user_id.set(None)
    _actor_username.set(None)
    _actor_role.set(None)


class RequestContextMiddleware:
    """Assign a correlation id + capture client IP for every HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from starlette.requests import Request

        request = Request(scope)

        supplied = request.headers.get("x-correlation-id", "").strip()
        correlation_id = supplied if supplied else new_correlation_id()
        if len(correlation_id) > 64:
            correlation_id = correlation_id[:64]

        _correlation_id.set(correlation_id)
        _client_ip.set(request.client.host if request.client else None)
        set_actor(user_id=None, username=None, role=None)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                headers = list(headers) + [
                    (b"x-correlation-id", correlation_id.encode("utf-8"))
                ]
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            reset()