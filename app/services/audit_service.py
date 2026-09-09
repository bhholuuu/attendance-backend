"""Central V2.6 audit service.

All audit log writes for the whole application must go through this module so
that:
  * the controlled action vocabulary is enforced in exactly one place,
  * the resulting rows are always privacy-scrubbed (part 27),
  * results are honest (SUCCESS / FAILED / DENIED / CONFLICT match what really
    happened — part 14/15),
  * middleware-driven events (rate limiting) can write without a request-scoped
    DB session (conftest overrides the session factory under test).

The audit table is append-only. There is deliberately no update/delete path.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.request_context import (
    current_actor_role,
    current_actor_username,
    current_actor_user_id,
    current_client_ip,
    current_correlation_id,
    new_correlation_id,
)
from app.models.audit_log import AuditLog
from app.models.user import User

# ---------------------------------------------------------------------------
# Controlled action vocabulary. New actions MUST be added here so the vocab is
# auditable, grep-able and reusable by the admin UI.
# ---------------------------------------------------------------------------
ACTIONS = (
    # Authentication & security
    "AUTH_LOGIN_SUCCESS",
    "AUTH_LOGIN_FAILED",
    "AUTH_LOGOUT",
    "AUTH_TOKEN_REFRESH_FAILED",
    "AUTH_INVALID_TOKEN",
    "AUTH_ACCOUNT_DISABLED",
    "RATE_LIMITED",
    "ACCESS_DENIED",
    # User management
    "USER_CREATED",
    "USER_UPDATED",
    "USER_DISABLED",
    "USER_ENABLED",
    "PASSWORD_CHANGED",
    # Classes / divisions
    "CLASS_CREATED",
    "CLASS_UPDATED",
    "CLASS_ARCHIVED",
    "DIVISION_CREATED",
    "DIVISION_UPDATED",
    "DIVISION_ARCHIVED",
    # Students
    "STUDENT_CREATED",
    "STUDENT_UPDATED",
    "STUDENT_ARCHIVED",
    "STUDENT_RESTORED",
    "STUDENT_IMPORT_COMMITTED",
    # Teacher assignments
    "TEACHER_ASSIGNMENT_CREATED",
    "TEACHER_ASSIGNMENT_DELETED",
    # Attendance
    "ATTENDANCE_SAVED",
    "ATTENDANCE_UPDATED",
    "ATTENDANCE_SYNCED",
    "ATTENDANCE_SYNC_REPLAYED",
    "ATTENDANCE_CONFLICT",
    # Leave / academic calendar
    "LEAVE_CREATED",
    "LEAVE_APPROVED",
    "LEAVE_REJECTED",
    "LEAVE_CANCELLED",
    "CALENDAR_EVENT_CREATED",
    "CALENDAR_EVENT_UPDATED",
    "CALENDAR_EVENT_DELETED",
    "ACADEMIC_YEAR_CREATED",
    "ACADEMIC_YEAR_UPDATED",
    # Messaging
    "MESSAGE_BATCH_CREATED",
    "MESSAGE_BATCH_SENT",
    "MESSAGE_BATCH_RETRIED",
    "MESSAGE_RETRIED",
    # Reports
    "REPORT_GENERATED",
    # V2.7: Backup + restore + disaster recovery
    "BACKUP_CREATED",
    "BACKUP_FAILED",
    "BACKUP_VERIFIED",
    "BACKUP_CORRUPTED",
    "BACKUP_DELETED",
    "RESTORE_STARTED",
    "RESTORE_COMPLETED",
    "RESTORE_FAILED",
    "RETENTION_CLEANUP",
)

_RESULT_VALUES = ("SUCCESS", "FAILED", "DENIED", "CONFLICT")

_SENSITIVE_KEY = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|access[_-]?key|"
    r"credential|database[_-]?url|dsn)",
    re.IGNORECASE,
)

# Session factory used only by middleware paths that lack a request-scoped DB.
# Defaults to ``SessionLocal``; tests may override it to the test DB.
_session_factory: Optional[Callable[[], _Session]] = None


def set_session_factory(factory: Callable[[], _Session]) -> None:
    global _session_factory
    _session_factory = factory


def reset_session_factory() -> None:
    global _session_factory
    _session_factory = None


def _new_session() -> _Session:
    if _session_factory is not None:
        return _session_factory()
    from app.database.connection import SessionLocal

    return SessionLocal()


# ---------------------------------------------------------------------------
# Privacy helpers
# ---------------------------------------------------------------------------

def mask_phone(number: Optional[str]) -> str:
    """Mask a phone/WhatsApp number for audit storage.

    ``+919876543210`` -> ``+91********10``. Short or malformed values are fully
    masked so no recipient is ever recoverable from an audit row.
    """
    value = (number or "").strip()
    if not value:
        return ""
    if len(value) < 6:
        return "*" * len(value)
    return value[:3] + ("*" * (len(value) - 5)) + value[-2:]


def _scrub_dict(dict_: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact any value whose KEY looks like a secret."""
    out: dict[str, Any] = {}
    for key, value in dict_.items():
        if _SENSITIVE_KEY.search(str(key)):
            out[key] = "<redacted>"
            continue
        if isinstance(value, dict):
            out[key] = _scrub_dict(value)
        elif isinstance(value, list):
            out[key] = [
                _scrub_dict(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            out[key] = value
    return out


def _dump_details(details: Optional[dict[str, Any]]) -> Optional[str]:
    if details is None:
        return None
    cleaned = _scrub_dict(dict(details))
    return json.dumps(cleaned, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# Actor resolution
# ---------------------------------------------------------------------------

def _actor_fields(actor: Optional[User]) -> dict[str, Any]:
    if actor is not None:
        return {
            "actor_user_id": actor.id,
            "actor_username": actor.username,
            "actor_role": actor.role.value,
        }
    return {
        "actor_user_id": current_actor_user_id(),
        "actor_username": current_actor_username(),
        "actor_role": current_actor_role(),
    }


# ---------------------------------------------------------------------------
# Low-level recording
# ---------------------------------------------------------------------------

def record(
    db: Session,
    *,
    action: str,
    result: str = "SUCCESS",
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    entity_label: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    actor: Optional[User] = None,
    ip_address: Optional[str] = None,
    correlation_id: Optional[str] = None,
    commit: bool = False,
) -> AuditLog:
    """Append one audit row. Never mutates an existing row.

    ``action`` must be in the controlled vocabulary and ``result`` one of the
    four supported outcomes (asserted for programmatic misuse).
    """
    if action not in ACTIONS:
        raise ValueError(f"Unknown audit action: {action}")
    if result not in _RESULT_VALUES:
        raise ValueError(f"Invalid audit result: {result}")

    actor_fields = _actor_fields(actor)
    safe_label = entity_label
    if safe_label is not None and len(safe_label) > 255:
        safe_label = safe_label[:255]

    log = AuditLog(
        timestamp=datetime.now(timezone.utc),
        correlation_id=correlation_id or current_correlation_id()
        or new_correlation_id(),
        actor_user_id=actor_fields["actor_user_id"],
        actor_username=actor_fields["actor_username"],
        actor_role=actor_fields["actor_role"],
        action=action,
        result=result,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_label=safe_label,
        ip_address=ip_address or current_client_ip(),
        details=_dump_details(details),
    )
    db.add(log)
    if commit:
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
    return log


def _commit_safely(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()


commit_safely = _commit_safely


# ---------------------------------------------------------------------------
# Transaction context manager — honest SUCCESS / FAILED / DENIED
# ---------------------------------------------------------------------------

@contextmanager
def audit_txn(
    db: Session,
    *,
    action: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    entity_label: Optional[str] = None,
    actor: Optional[User] = None,
    success_details: Optional[Any] = None,
    denied_details: Optional[dict[str, Any]] = None,
    failed_details: Optional[dict[str, Any]] = None,
):
    """Record an action with the outcome that actually happened.

    * Body completes -> SUCCESS (with success_details).
    * HTTPException 401/403 -> DENIED (privilege/scope blocked).
    * HTTPException (other) -> FAILED (business rejection).
    * Any other exception -> FAILED, then re-raised.

    ``success_details`` may be a callable receiving the value assigned to
    ``holder["result"]`` (the with-statement returns this holder):
        with audit_txn(db, action="STUDENT_CREATED", ...) as holder:
            student = service.create_student(...)
            holder["result"] = student
    """
    holder: dict[str, Any] = {"result": None}
    try:
        yield holder
    except HTTPException as exc:
        is_denied = exc.status_code in (401, 403)
        outcome = "DENIED" if is_denied else "FAILED"
        entity_id_use = entity_id if entity_id is not None else holder.get("entity_id")
        entity_label_use = entity_label if entity_label is not None else holder.get("entity_label")
        if is_denied:
            if entity_label_use is None and actor is not None:
                entity_label_use = f"user {actor.username}"
            record(
                db,
                action=action,
                result=outcome,
                entity_type=entity_type,
                entity_id=entity_id_use,
                entity_label=entity_label_use,
                details=denied_details or {},
                actor=actor,
            )
        else:
            record(
                db,
                action=action,
                result=outcome,
                entity_type=entity_type,
                entity_id=entity_id_use,
                entity_label=entity_label_use,
                details=failed_details or {},
                actor=actor,
            )
        _commit_safely(db)
        raise
    except Exception:
        record(
            db,
            action=action,
            result="FAILED",
            entity_type=entity_type,
            entity_id=entity_id if entity_id is not None else holder.get("entity_id"),
            entity_label=entity_label if entity_label is not None else holder.get("entity_label"),
            details=failed_details or {},
            actor=actor,
        )
        _commit_safely(db)
        raise
    else:
        details = success_details
        if callable(success_details):
            details = success_details(holder["result"])
        record(
            db,
            action=action,
            result="SUCCESS",
            entity_type=entity_type,
            entity_id=entity_id if entity_id is not None else holder.get("entity_id"),
            entity_label=entity_label if entity_label is not None else holder.get("entity_label"),
            details=details,
            actor=actor,
        )
        _commit_safely(db)


def record_security_event(
    *,
    action: str,
    result: str = "DENIED",
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    entity_label: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    actor: Optional[User] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Record an event from middleware/dependency code that has no session.

    Opens a session through the configured factory (overridable in tests),
    writes the row and commits. Best-effort by design: audit is observability
    and a failing audit write must never break the request path.
    """
    session = _new_session()
    try:
        try:
            record(
                session,
                action=action,
                result=result,
                entity_type=entity_type,
                entity_id=entity_id,
                entity_label=entity_label,
                details=details,
                actor=actor,
                ip_address=ip_address,
            )
            session.commit()
        except Exception:
            session.rollback()
    finally:
        session.close()