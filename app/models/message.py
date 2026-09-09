import enum
import hashlib
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base
from app.models.attendance import AttendanceStatus


class MessageType(str, enum.Enum):
    """Type of parent notification message.

    Initially only ABSENCE_NOTIFICATION is supported. The field is extensible
    so future message types (e.g. LATE_ARRIVAL, BEHAVIOUR, GENERAL) can be
    added without schema changes.
    """

    ABSENCE_NOTIFICATION = "ABSENCE_NOTIFICATION"


class MessageBatchStatus(str, enum.Enum):
    """Lifecycle status of a message batch.

    PENDING      - created, messages not yet processed (or awaiting retry).
    PROCESSING   - a send/retry operation is actively in progress.
    COMPLETED    - every message in the batch was sent successfully.
    PARTIAL_FAILED - some messages sent, some failed and may be retried.
    """

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    PARTIAL_FAILED = "PARTIAL_FAILED"


class MessageDeliveryStatus(str, enum.Enum):
    """Per-parent delivery status of an individual message.

    Statuses progress monotonically and are never downgraded:
      PENDING -> PROCESSING -> SENT -> DELIVERED -> READ
    FAILURE is allowed from PENDING/PROCESSING/SENT (e.g. the provider reports
    the send failed or a delivery webhook reports a permanent failure).
    CANCELLED is set when the underlying attendance record is corrected before
    the message is sent (e.g. ABSENT changed to PRESENT).

    SENT means the WhatsApp API accepted the message (returns a message id).
    DELIVERED/READ are reported asynchronously by WhatsApp webhooks.
    """

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    READ = "READ"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# Monotonic rank used to enforce "never downgrade" on delivery webhook updates.
# FAILED and CANCELLED are deliberately not part of the progression so they
# can be set from any of the earlier states.
DELIVERY_STATUS_RANK = {
    MessageDeliveryStatus.PENDING: 0,
    MessageDeliveryStatus.PROCESSING: 1,
    MessageDeliveryStatus.SENT: 2,
    MessageDeliveryStatus.DELIVERED: 3,
    MessageDeliveryStatus.READ: 4,
}


# Default error message used when a student has no usable parent WhatsApp
# number at batch creation time. We snapshot the failure rather than silently
# dropping the student so nothing is silently ignored.
PARENT_WHATSAPP_UNAVAILABLE = "Parent WhatsApp number unavailable or invalid."


def generate_idempotency_key(attendance_record_id: int, message_type: str) -> str:
    """Generate a stable idempotency key from the attendance record and type.

    This ensures that the same attendance record + message type combination
    can only produce one logical message, preventing duplicates across batches.
    """
    raw = f"{attendance_record_id}:{message_type}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"idem-{digest[:40]}"


class MessageBatch(Base):
    """A batch of parent notification messages tied to one attendance session."""

    __tablename__ = "message_batches"
    __table_args__ = (
        # Only one message batch may exist per attendance session. Enforced at
        # the database level to prevent duplicate batches.
        UniqueConstraint(
            "attendance_session_id",
            name="uq_message_batch_attendance_session",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    attendance_session_id: Mapped[int] = mapped_column(
        ForeignKey("attendance_sessions.id"), nullable=False, unique=True, index=True
    )
    class_id: Mapped[int] = mapped_column(
        ForeignKey("classes.id"), nullable=False, index=True
    )
    division_id: Mapped[int] = mapped_column(
        ForeignKey("divisions.id"), nullable=False, index=True
    )
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[MessageBatchStatus] = mapped_column(
        Enum(MessageBatchStatus, name="message_batch_status"),
        nullable=False,
        default=MessageBatchStatus.PENDING,
    )
    # Snapshot summary counts, independent of later record mutations.
    total_messages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    messages: Mapped[List["ParentMessage"]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    school_class: Mapped["SchoolClass"] = relationship(
        foreign_keys=[class_id],
    )
    division: Mapped["Division"] = relationship(
        foreign_keys=[division_id],
    )

    def __repr__(self) -> str:
        return (
            f"<MessageBatch(id={self.id}, "
            f"attendance_session_id={self.attendance_session_id}, "
            f"status={self.status.value})>"
        )


class ParentMessage(Base):
    """An individual parent notification for a single student.

    Parent name, WhatsApp number, attendance status and message content are
    snapshotted at creation time so historical records never depend on later
    changes to the student's profile.

    V2.5 additions:
      - message_type: extensible notification type (ABSENCE_NOTIFICATION).
      - attendance_record_id: FK to the source attendance record for tracking
        and idempotency.
      - idempotency_key: stable key derived from attendance_record_id +
        message_type, enforced by a unique constraint to prevent duplicate
        logical notifications across batches.
      - next_retry_at: scheduled time for the next retry attempt.
      - cancelled_at / cancelled_reason: set when the notification is
        suppressed due to attendance correction (e.g. ABSENT -> PRESENT).
    """

    __tablename__ = "parent_messages"
    __table_args__ = (
        # A student may have exactly one message record within a batch.
        UniqueConstraint(
            "message_batch_id",
            "student_id",
            name="uq_parent_message_batch_student",
        ),
        # V2.5 idempotency: the same attendance record + message type can
        # only produce one logical notification across all batches.
        UniqueConstraint(
            "attendance_record_id",
            "message_type",
            name="uq_parent_message_attendance_type",
            # NULLs are distinct in unique constraints; only enforced when
            # both columns are non-NULL (i.e. when an attendance record is
            # linked).
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    message_batch_id: Mapped[int] = mapped_column(
        ForeignKey("message_batches.id"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id"), nullable=False, index=True
    )
    # V2.5: link to the source attendance record for tracking and idempotency.
    attendance_record_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("student_attendance.id"), nullable=True, index=True
    )
    # V2.5: extensible message type (ABSENCE_NOTIFICATION initially).
    message_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default=MessageType.ABSENCE_NOTIFICATION.value,
        index=True,
    )
    # V2.5: stable idempotency key derived from attendance_record_id + message_type.
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
    )
    # Snapshotted parent details at message creation time.
    parent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    parent_whatsapp_number: Mapped[str] = mapped_column(String(20), nullable=False)
    # Snapshotted attendance status for this student.
    attendance_status: Mapped[AttendanceStatus] = mapped_column(
        Enum(AttendanceStatus, name="message_attendance_status"),
        nullable=False,
    )
    # Generated, self-contained notification content (one student only).
    message_content: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_status: Mapped[MessageDeliveryStatus] = mapped_column(
        Enum(MessageDeliveryStatus, name="message_delivery_status"),
        nullable=False,
        default=MessageDeliveryStatus.PENDING,
    )
    # Provider internals kept intentionally limited and safe.
    provider_message_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    provider_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # V2.5: scheduled time for the next retry attempt.
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # V2.5: set when the notification is cancelled due to attendance correction.
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    batch: Mapped[MessageBatch] = relationship(
        back_populates="messages",
        foreign_keys=[message_batch_id],
    )

    def __repr__(self) -> str:
        return (
            f"<ParentMessage(id={self.id}, student_id={self.student_id}, "
            f"status={self.delivery_status.value})>"
        )
