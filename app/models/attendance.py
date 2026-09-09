import enum
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


class SessionStatus(str, enum.Enum):
    """Lifecycle status of an attendance session."""

    DRAFT = "DRAFT"
    COMPLETED = "COMPLETED"


class AttendanceStatus(str, enum.Enum):
    """Per-student attendance status within a session."""

    PRESENT = "PRESENT"
    ABSENT = "ABSENT"


class EffectiveAttendanceStatus(str, enum.Enum):
    """Per-day attendance status after applying holidays and approved leave.

    NOT persisted. Computed on demand by the reports/dashboard/messaging logic
    using the precedence: HOLIDAY > APPROVED_LEAVE > PRESENT/ABSENT >
    NOT_RECORDED. Days with no attendance session are NOT_RECORDED and are
    never treated as ABSENT.
    """

    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    APPROVED_LEAVE = "APPROVED_LEAVE"
    HOLIDAY = "HOLIDAY"
    NOT_RECORDED = "NOT_RECORDED"


class AttendanceSession(Base):
    __tablename__ = "attendance_sessions"
    __table_args__ = (
        # Only one attendance session may exist for a given class + division +
        # date. Enforced at the database level to prevent duplicate sessions.
        UniqueConstraint(
            "class_id",
            "division_id",
            "attendance_date",
            name="uq_attendance_class_division_date",
        ),
        # Offline-sync idempotency: a client may only ever create a given
        # session once (V2.4). NULLs (web-created sessions) are distinct, so
        # existing rows are untouched.
        UniqueConstraint(
            "client_session_id",
            name="uq_attendance_client_session_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    # Stable client-generated UUID identifying this exact session on a device.
    # NULL for sessions created through the web UI.
    client_session_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True, index=True
    )
    class_id: Mapped[int] = mapped_column(
        ForeignKey("classes.id"), nullable=False, index=True
    )
    division_id: Mapped[int] = mapped_column(
        ForeignKey("divisions.id"), nullable=False, index=True
    )
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    taken_by: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, name="attendance_session_status"),
        nullable=False,
        default=SessionStatus.COMPLETED,
    )
    # Snapshot counts. These intentionally store the number of students at the
    # time the session was taken, independent of later student archiving.
    total_students: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    present_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    absent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    school_class: Mapped["SchoolClass"] = relationship(
        foreign_keys=[class_id],
    )
    division: Mapped["Division"] = relationship(
        foreign_keys=[division_id],
    )
    taker: Mapped["User"] = relationship(foreign_keys=[taken_by])
    records: Mapped[List["StudentAttendance"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return (
            f"<AttendanceSession(id={self.id}, date={self.attendance_date}, "
            f"status={self.status.value})>"
        )


class StudentAttendance(Base):
    __tablename__ = "student_attendance"
    __table_args__ = (
        # A student may have exactly one attendance record within a session.
        UniqueConstraint(
            "attendance_session_id",
            "student_id",
            name="uq_student_attendance_session_student",
        ),
        # Offline-sync idempotency per record (V2.4): a client may only ever
        # apply a given record once. NULLs (web-created records) are distinct.
        UniqueConstraint(
            "client_record_id",
            name="uq_student_attendance_client_record_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    # Stable client-generated UUID identifying this exact record on a device.
    # NULL for records created through the web UI.
    client_record_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    attendance_session_id: Mapped[int] = mapped_column(
        ForeignKey("attendance_sessions.id"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id"), nullable=False, index=True
    )
    attendance_status: Mapped[AttendanceStatus] = mapped_column(
        Enum(AttendanceStatus, name="student_attendance_status"),
        nullable=False,
    )
    remarks: Mapped[str] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # Historical records are preserved even if the student is later archived;
    # the soft archive never deletes the row, and the relationship is not
    # configured to cascade-delete.
    session: Mapped["AttendanceSession"] = relationship(
        back_populates="records",
        foreign_keys=[attendance_session_id],
    )
    student: Mapped["Student"] = relationship(foreign_keys=[student_id])

    def __repr__(self) -> str:
        return (
            f"<StudentAttendance(id={self.id}, student_id={self.student_id}, "
            f"status={self.attendance_status.value})>"
        )
