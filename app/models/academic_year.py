from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


class AcademicYear(Base):
    """An academic year definition (e.g. "2026-2027").

    Attendance dates, calendar events and student leaves are validated against
    the active academic year when one exists. Only one active academic year is
    expected at a time; overlapping active years are rejected at the service
    layer (the DB keeps a plain boolean so historical deactivation keeps data
    intact).
    """

    __tablename__ = "academic_years"
    __table_args__ = (
        UniqueConstraint("name", name="uq_academic_year_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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

    creator: Mapped["User"] = relationship(foreign_keys=[created_by])

    def __repr__(self) -> str:
        return f"<AcademicYear(id={self.id}, name={self.name!r})>"