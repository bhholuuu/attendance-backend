import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


class CalendarEventType(str, enum.Enum):
    """Types of events supported by the academic calendar."""

    HOLIDAY = "HOLIDAY"
    SCHOOL_EVENT = "SCHOOL_EVENT"
    ACADEMIC_DAY = "ACADEMIC_DAY"
    OTHER = "OTHER"


class HolidayType(str, enum.Enum):
    """Optional sub-type detail for HOLIDAY calendar events."""

    PUBLIC_HOLIDAY = "PUBLIC_HOLIDAY"
    SCHOOL_HOLIDAY = "SCHOOL_HOLIDAY"
    VACATION = "VACATION"
    EXAM_BREAK = "EXAM_BREAK"
    EMERGENCY_CLOSURE = "EMERGENCY_CLOSURE"
    OTHER = "OTHER"


class CalendarEvent(Base):
    """A single event in the academic calendar.

    Ranges are stored as inclusive start/end dates (one row per range, never
    one row per day). Only events of type HOLIDAY block attendance; overlapping
    active holidays within the same academic year are rejected at the service
    layer.
    """

    __tablename__ = "calendar_events"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    academic_year_id: Mapped[int] = mapped_column(
        ForeignKey("academic_years.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[CalendarEventType] = mapped_column(
        Enum(CalendarEventType, name="calendar_event_type"),
        nullable=False,
        default=CalendarEventType.HOLIDAY,
    )
    holiday_type: Mapped[HolidayType] = mapped_column(
        Enum(HolidayType, name="holiday_type"),
        nullable=True,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(500), nullable=True)
    is_recurring: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
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

    academic_year: Mapped["AcademicYear"] = relationship(
        foreign_keys=[academic_year_id],
    )
    creator: Mapped["User"] = relationship(foreign_keys=[created_by])

    def __repr__(self) -> str:
        return (
            f"<CalendarEvent(id={self.id}, title={self.title!r}, "
            f"{self.start_date}..{self.end_date})>"
        )