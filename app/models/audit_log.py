import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base


class AuditResult(str, enum.Enum):
    """World-readable audit outcomes (V2.6).

    SUCCESS  - the audited action completed.
    FAILED   - the audited action was attempted but did not complete.
    DENIED   - the action was blocked by an authorization/security rule.
    CONFLICT - the action reached the server but the server kept its own copy.
    """

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    DENIED = "DENIED"
    CONFLICT = "CONFLICT"


class AuditLog(Base):
    """Append-only, immutable audit log entry (V2.6).

    Design rules enforced by the API (never by the table alone):
      * No UPDATE or DELETE endpoint exists. Rows are written once and read
        only by admins.
      * ``details`` is a JSON-serializable payload that has already passed the
        audit service's privacy scrubber: passwords, hashes, tokens, API keys,
        database credentials and full phone numbers are never stored.
      * Only UTC timestamps are stored; clients format them into local time.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    # UTC moment the event occurred.
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # Stable id linking every record of one HTTP request.
    correlation_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    # Actor. Nullable because security events (failed logins, invalid tokens)
    # happen before a user is established.
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    actor_username: Mapped[str | None] = mapped_column(String(50), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Controlled action vocabulary (see app/services/audit_service.py:ACTIONS).
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # One of AuditResult values.
    result: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # Optional business entity this event is about (e.g. "student", "class").
    entity_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True, index=True
    )
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # Human-readable label for the UI (never raw PII; phones are masked).
    entity_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    # JSON payload with privacy-scrubbed, action-specific context.
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<AuditLog(id={self.id}, action={self.action!r}, "
            f"result={self.result!r}, at={self.timestamp.isoformat()})>"
        )