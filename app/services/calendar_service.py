"""Academic calendar / holiday management (V2.3).

Calendar events are school-wide (attached to an academic year). Only events of
type HOLIDAY block attendance. Mutations are ADMIN-only (enforced in the
router); teachers read the same calendar for visibility.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.models.academic_year import AcademicYear
from app.models.calendar_event import (
    CalendarEvent,
    CalendarEventType,
    HolidayType,
)
from app.models.user import User
from app.services.academic_year_service import get_academic_year


def _http(status_code: int, detail: str):
    from fastapi import HTTPException

    return HTTPException(status_code=status_code, detail=detail)


def _validate_dates(start_date: date, end_date: date) -> None:
    if start_date > end_date:
        raise _http(400, "Event start date must not be after its end date")


def get_event(db: Session, event_id: int) -> CalendarEvent:
    event = db.query(CalendarEvent).filter(CalendarEvent.id == event_id).first()
    if event is None:
        raise _http(404, "Calendar event not found")
    return event


def list_events(
    db: Session,
    *,
    academic_year_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> list[CalendarEvent]:
    query = db.query(CalendarEvent)
    if academic_year_id is not None:
        query = query.filter(CalendarEvent.academic_year_id == academic_year_id)
    if start_date is not None:
        query = query.filter(CalendarEvent.end_date >= start_date)
    if end_date is not None:
        query = query.filter(CalendarEvent.start_date <= end_date)
    return query.order_by(
        CalendarEvent.start_date.asc(), CalendarEvent.id.asc()
    ).all()


def _overlap_condition(start_date: date, end_date: date):
    return ~(
        (CalendarEvent.start_date > end_date)
        | (CalendarEvent.end_date < start_date)
    )


def _validate_holiday_overlap(
    db: Session,
    year_id: int,
    start_date: date,
    end_date: date,
    exclude_event_id: Optional[int] = None,
) -> None:
    """Reject overlapping active HOLIDAY events within the same academic year."""
    query = (
        db.query(CalendarEvent.id)
        .filter(
            CalendarEvent.academic_year_id == year_id,
            CalendarEvent.event_type == CalendarEventType.HOLIDAY,
            _overlap_condition(start_date, end_date),
        )
    )
    if exclude_event_id is not None:
        query = query.filter(CalendarEvent.id != exclude_event_id)
    if query.first() is not None:
        raise _http(
            400,
            "This holiday overlaps an existing holiday in the same academic year",
        )


def _validate_within_year(
    year: AcademicYear, start_date: date, end_date: date
) -> None:
    if start_date < year.start_date or end_date > year.end_date:
        raise _http(
            400,
            "Calendar events must fall within their academic year's date range",
        )


def _validate_event_type(
    event_type: CalendarEventType, holiday_type: Optional[HolidayType]
) -> None:
    if event_type == CalendarEventType.HOLIDAY and holiday_type is None:
        raise _http(400, "holiday_type is required for a HOLIDAY event")
    if event_type != CalendarEventType.HOLIDAY and holiday_type is not None:
        raise _http(400, "holiday_type may only be set for HOLIDAY events")


def create_event(
    db: Session,
    *,
    academic_year_id: int,
    title: str,
    event_type: CalendarEventType,
    start_date: date,
    end_date: date,
    holiday_type: Optional[HolidayType] = None,
    description: Optional[str] = None,
    is_recurring: bool = False,
    creator: User,
) -> CalendarEvent:
    _validate_dates(start_date, end_date)
    year = get_academic_year(db, academic_year_id)
    _validate_within_year(year, start_date, end_date)
    _validate_event_type(event_type, holiday_type)
    if event_type == CalendarEventType.HOLIDAY:
        _validate_holiday_overlap(db, year.id, start_date, end_date)

    title = (title or "").strip()
    if not title:
        raise _http(400, "Event title is required")

    event = CalendarEvent(
        academic_year_id=year.id,
        title=title,
        event_type=event_type,
        holiday_type=holiday_type,
        start_date=start_date,
        end_date=end_date,
        description=description,
        is_recurring=is_recurring,
        created_by=creator.id,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def update_event(
    db: Session,
    event: CalendarEvent,
    *,
    title: Optional[str] = None,
    event_type: Optional[CalendarEventType] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    holiday_type: Optional[HolidayType] = None,
    description: Optional[str] = None,
    is_recurring: Optional[bool] = None,
) -> CalendarEvent:
    new_start = start_date if start_date is not None else event.start_date
    new_end = end_date if end_date is not None else event.end_date
    new_type = event_type if event_type is not None else event.event_type
    new_holiday_type = (
        holiday_type
        if holiday_type is not None
        else event.holiday_type
    )

    _validate_dates(new_start, new_end)
    year = get_academic_year(db, event.academic_year_id)
    _validate_within_year(year, new_start, new_end)
    _validate_event_type(new_type, new_holiday_type)
    if new_type == CalendarEventType.HOLIDAY:
        _validate_holiday_overlap(
            db, year.id, new_start, new_end, exclude_event_id=event.id
        )

    if title is not None:
        event.title = (title or "").strip() or event.title
    if event_type is not None:
        event.event_type = event_type
    if start_date is not None:
        event.start_date = start_date
    if end_date is not None:
        event.end_date = end_date
    if holiday_type is not None:
        event.holiday_type = holiday_type
    if description is not None:
        event.description = description
    if is_recurring is not None:
        event.is_recurring = is_recurring

    db.commit()
    db.refresh(event)
    return event


def delete_event(db: Session, event: CalendarEvent) -> None:
    db.delete(event)
    db.commit()