import enum
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


class LeaveStatus(str, enum.Enum):
    """Lifecycle status of a student leave request."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class StudentLeave(Base):
    """A student leave (excused absence) over an inclusive date range.

    Only APPROVED leaves affect attendance calculations (effective statuses,
    dashboard counts, reports). PENDING/REJECTED/CANCELLED leaves have no
    effect. Overlapping APPROVED leaves for the same student are rejected at
    the service layer; duplicate overlapping approvals must be resolved by an
    admin.
    """

    __tablename__ = "student_leaves"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id"), nullable=False, index=True
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    leave_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    status: Mapped[LeaveStatus] = mapped_column(
        Enum(LeaveStatus, name="student_leave_status"),
        nullable=False,
        default=LeaveStatus.PENDING,
        index=True,
    )
    approved_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    student: Mapped["Student"] = relationship(foreign_keys=[student_id])
    approver: Mapped["User"] = relationship(foreign_keys=[approved_by])
    creator: Mapped["User"] = relationship(foreign_keys=[created_by])

    def __repr__(self) -> str:
        return (
            f"<StudentLeave(id={self.id}, student_id={self.student_id}, "
            f"{self.start_date}..{self.end_date}, status={self.status.value})>"
        )