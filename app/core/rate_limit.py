import time
from collections import defaultdict, deque
from typing import Callable, Deque, Dict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings
from app.services import audit_service

# In-memory store: client_key -> deque of (timestamp, ...)
# NOTE: This is a single-process, in-memory limiter suitable for development
# and small single-instance deployments. For horizontally-scaled production
# deployments use a shared store (e.g. Redis) via a full rate-limiting library.
_window_times: Dict[str, Deque[float]] = defaultdict(deque)


def _client_key(request: Request) -> str:
    """Best-effort client identifier (IP address)."""
    return request.client.host if request.client else "unknown"


def _is_rate_limited(key: str, limit: int, window_seconds: int) -> bool:
    now = time.monotonic()
    q = _window_times[key]
    # Drop entries outside the window.
    cutoff = now - window_seconds
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def _limit_for(path: str, method: str = "") -> int:
    """Recompute the applied limit for an audited request."""
    if method == "POST" and path == "/api/v1/auth/login":
        return settings.LOGIN_RATE_LIMIT_LIMIT
    if method == "POST" and path == "/api/v1/messages/batches":
        return settings.SEND_RATE_LIMIT_LIMIT
    if method == "POST" and path.startswith("/api/v1/messages/batches") and path.endswith("/send"):
        return settings.RETRY_RATE_LIMIT_LIMIT
    return 0


def _window_for(path: str, method: str = "") -> int:
    if method == "POST" and path == "/api/v1/auth/login":
        return settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS
    if method == "POST" and path == "/api/v1/messages/batches":
        return settings.SEND_RATE_LIMIT_WINDOW_SECONDS
    if method == "POST" and path.startswith("/api/v1/messages/batches") and path.endswith("/send"):
        return settings.RETRY_RATE_LIMIT_WINDOW_SECONDS
    return 0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Applies configurable per-path rate limits keyed by client IP."""

    def __init__(
        self,
        app: Callable,
        *,
        login_point: str = "/api/v1/auth/login",
        send_point: str = "/api/v1/messages/batches",
        retry_point: str = "/api/v1/messages/batches",
        retry_suffix: str = "/send",
    ) -> None:
        self._login_point = login_point
        self._send_point = send_point
        self._retry_point = retry_point
        self._retry_suffix = retry_suffix
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        if not settings.RATE_LIMIT_ENABLED:
            return await call_next(request)

        key = _client_key(request)
        limited = False
        retry_after = settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS

        path = request.url.path
        if request.method == "POST" and path == self._login_point:
            limited = _is_rate_limited(
                key,
                settings.LOGIN_RATE_LIMIT_LIMIT,
                settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS,
            )
            retry_after = settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS
        elif request.method == "POST" and path == self._send_point:
            limited = _is_rate_limited(
                key,
                settings.SEND_RATE_LIMIT_LIMIT,
                settings.SEND_RATE_LIMIT_WINDOW_SECONDS,
            )
            retry_after = settings.SEND_RATE_LIMIT_WINDOW_SECONDS
        elif request.method == "POST" and (
            path.startswith(self._retry_point) and path.endswith(self._retry_suffix)
        ):
            limited = _is_rate_limited(
                key,
                settings.RETRY_RATE_LIMIT_LIMIT,
                settings.RETRY_RATE_LIMIT_WINDOW_SECONDS,
            )
            retry_after = settings.RETRY_RATE_LIMIT_WINDOW_SECONDS

        if limited:
            # V2.6: security monitoring — every rate-limit hit is audited
            # (denied access) even though the request never reaches a route.
            audit_service.record_security_event(
                action="RATE_LIMITED",
                result="DENIED",
                entity_type="client",
                entity_label=key,
                details={
                    "path": path,
                    "method": request.method,
                    "limit": _limit_for(path, request.method),
                    "window_seconds": _window_for(path, request.method),
                },
                ip_address=key if key != "unknown" else None,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests. Please try again shortly."
                },
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)
