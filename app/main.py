import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.auth import router as auth_router
from app.api.classes import router as classes_router
from app.api.divisions import router as divisions_router
from app.api.students import router as students_router
from app.api.attendance import router as attendance_router
from app.api.messages import router as messages_router
from app.api.teacher_assignments import router as teacher_assignments_router
from app.api.dashboard import router as dashboard_router
from app.api.reports import router as reports_router
from app.api.academic_years import router as academic_years_router
from app.api.calendar import router as calendar_router
from app.api.student_leaves import router as student_leaves_router
from app.api.webhooks import router as webhooks_router
from app.api.audit_logs import router as audit_logs_router
from app.api.user_management import router as user_management_router
from app.api.backups import router as backups_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.rate_limit import RateLimitMiddleware
from app.core.request_context import RequestContextMiddleware

configure_logging()


class SecurityHeadersMiddleware:
    """Adds hardening response headers to every response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                security_headers = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    (b"x-xss-protection", b"0"),
                ]
                merged = list(headers) + security_headers
                message["headers"] = merged
            await send(message)

        await self.app(scope, receive, send_wrapper)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.APP_ENV == "development":
        # Development convenience: ensure the schema exists and the initial
        # ADMIN account is present so login works out of the box. Never runs
        # in testing/production environments.
        from app.db_seed import bootstrap

        bootstrap()
    elif settings.APP_ENV == "production":
        # Production convenience: create the very first ADMIN account only
        # when the operator explicitly supplies a first-run password. The
        # development default password is never used in production.
        password = os.environ.get("ATTENDANCE_ADMIN_BOOTSTRAP_PASSWORD") or ""
        if password.strip():
            from app.db_seed import bootstrap_production_admin

            bootstrap_production_admin(password)
    yield


def create_app() -> FastAPI:
    # Fail fast on insecure production configuration before serving traffic.
    settings.validate_production_config()

    docs_url = "/docs" if settings.docs_enabled else None
    redoc_url = "/redoc" if settings.docs_enabled else None

    app = FastAPI(
        title=settings.PROJECT_NAME,
        description="Backend API for School Attendance Management System",
        version=settings.VERSION,
        docs_url=docs_url,
        redoc_url=redoc_url,
        lifespan=lifespan,
    )

    # CORS: restrict allowed origins (never "*" with credentials in production).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Security hardening headers.
    app.add_middleware(SecurityHeadersMiddleware)

    # API rate limiting for auth/credential and messaging endpoints.
    app.add_middleware(RateLimitMiddleware)

    # V2.6: per-request correlation id (contextvars + response header) used by
    # every audit log row.
    app.add_middleware(RequestContextMiddleware)

    # Global, consistent JSON error handlers + structured logging.
    register_exception_handlers(app)

    # Routers.
    prefix = settings.API_V1_PREFIX
    app.include_router(health_router, prefix=prefix, tags=["Health"])
    app.include_router(auth_router, prefix=f"{prefix}/auth", tags=["Authentication"])
    app.include_router(classes_router, prefix=prefix, tags=["Classes"])
    app.include_router(divisions_router, prefix=prefix, tags=["Divisions"])
    app.include_router(students_router, prefix=prefix, tags=["Students"])
    app.include_router(attendance_router, prefix=prefix, tags=["Attendance"])
    app.include_router(messages_router, prefix=f"{prefix}/messages", tags=["Messages"])
    app.include_router(
        teacher_assignments_router, prefix=prefix, tags=["Teacher Assignments"]
    )
    app.include_router(dashboard_router, prefix=prefix, tags=["Dashboard"])
    app.include_router(reports_router, prefix=prefix, tags=["Reports"])
    app.include_router(academic_years_router, prefix=prefix, tags=["Academic Years"])
    app.include_router(calendar_router, prefix=prefix, tags=["Calendar"])
    app.include_router(student_leaves_router, prefix=prefix, tags=["Student Leaves"])
    app.include_router(
        webhooks_router, prefix=f"{prefix}/webhooks", tags=["Webhooks"]
    )
    app.include_router(audit_logs_router, prefix=prefix, tags=["Audit Logs"])
    app.include_router(
        user_management_router, prefix=prefix, tags=["User Management"]
    )
    app.include_router(backups_router, prefix=prefix, tags=["Backup & Recovery"])

    @app.get("/", tags=["Root"])
    async def root():
        return {
            "message": "School Attendance API",
            "version": settings.VERSION,
            "docs": "/docs" if settings.docs_enabled else None,
        }

    return app


app = create_app()
