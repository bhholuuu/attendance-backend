"""Shared V2.3 rules for holidays, student leave and effective attendance.

The "is this date a holiday / is this student on approved leave" decision is
used by attendance taking, the dashboard, reports/exports and messaging. Keeping
it in one module ensures the precedence stays consistent everywhere:

    HOLIDAY > APPROVED_LEAVE > PRESENT/ABSENT > NOT_RECORDED

Rules honored here:
  * Only calendar events of type HOLIDAY block attendance.
  * Holidays are only considered from ACTIVE academic years.
  * Only APPROVED student leaves affect calculations.
  * A holiday beats an approved leave (a leave overlapping a holiday is never
    double counted; the holiday wins).
  * Ranges are inclusive; multi-day events are one row, never per-day rows.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Set

from sqlalchemy.orm import Session

from app.models.attendance import (
    AttendanceStatus,
    EffectiveAttendanceStatus,
)
from app.models.calendar_event import CalendarEvent, CalendarEventType
from app.models.student_leave import LeaveStatus, StudentLeave


def _iter_dates(start_date: date, end_date: date):
    """Yield every date in the inclusive [start_date, end_date] range."""
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def _active_academic_year_ids(db: Session) -> List[int]:
    """Id list of currently active academic years (usually just one)."""
    from app.models.academic_year import AcademicYear

    rows = (
        db.query(AcademicYear.id)
        .filter(AcademicYear.is_active.is_(True))
        .all()
    )
    return [r[0] for r in rows]


def holiday_events_in_range(db: Session, start_date: date, end_date: date):
    """HOLIDAY CalendarEvents (active academic years) overlapping a range."""
    year_ids = _active_academic_year_ids(db)
    query = db.query(CalendarEvent).filter(
        CalendarEvent.event_type == CalendarEventType.HOLIDAY,
        CalendarEvent.start_date <= end_date,
        CalendarEvent.end_date >= start_date,
    )
    if year_ids:
        query = query.filter(CalendarEvent.academic_year_id.in_(year_ids))
    return query.all()


def holiday_dates_in_range(db: Session, start_date: date, end_date: date) -> Set[date]:
    """Set of every calendar date covered by a holiday in a range.

    Ranges are clamped to [start_date, end_date] so the result size is bounded
    by the report/range length (report ranges are capped at 365 days).
    """
    dates: Set[date] = set()
    for event in holiday_events_in_range(db, start_date, end_date):
        s = max(event.start_date, start_date)
        e = min(event.end_date, end_date)
        dates.update(_iter_dates(s, e))
    return dates


def is_holiday(db: Session, day: date) -> bool:
    """Whether ``day`` is a holiday in an active academic year."""
    year_ids = _active_academic_year_ids(db)
    query = db.query(CalendarEvent.id).filter(
        CalendarEvent.event_type == CalendarEventType.HOLIDAY,
        CalendarEvent.start_date <= day,
        CalendarEvent.end_date >= day,
    )
    if year_ids:
        query = query.filter(CalendarEvent.academic_year_id.in_(year_ids))
    return query.first() is not None


def holiday_title_for(db: Session, day: date) -> Optional[str]:
    """Title of a holiday covering ``day`` in an active academic year (if any)."""
    year_ids = _active_academic_year_ids(db)
    query = (
        db.query(CalendarEvent.title)
        .filter(
            CalendarEvent.event_type == CalendarEventType.HOLIDAY,
            CalendarEvent.start_date <= day,
            CalendarEvent.end_date >= day,
        )
        .order_by(CalendarEvent.start_date.desc())
    )
    if year_ids:
        query = query.filter(CalendarEvent.academic_year_id.in_(year_ids))
    row = query.first()
    return row[0] if row else None


def date_in_active_academic_year(db: Session, day: date) -> bool:
    """Whether ``day`` falls within any active academic year.

    When no academic year has been configured (legacy mode) the function
    returns True so existing school setups keep working without one.
    """
    from app.models.academic_year import AcademicYear

    if db.query(AcademicYear.id).first() is None:
        return True

    row = (
        db.query(AcademicYear.id)
        .filter(
            AcademicYear.is_active.is_(True),
            AcademicYear.start_date <= day,
            AcademicYear.end_date >= day,
        )
        .first()
    )
    return row is not None


def approved_leave_dates_by_student(
    db: Session,
    student_ids: Iterable[int],
    start_date: date,
    end_date: date,
) -> Dict[int, Set[date]]:
    """Map student id -> set of dates covered by APPROVED leave in a range."""
    ids = list(student_ids)
    result: Dict[int, Set[date]] = {}
    if not ids:
        return result
    rows = (
        db.query(StudentLeave.student_id, StudentLeave.start_date, StudentLeave.end_date)
        .filter(
            StudentLeave.student_id.in_(ids),
            StudentLeave.status == LeaveStatus.APPROVED,
            StudentLeave.start_date <= end_date,
            StudentLeave.end_date >= start_date,
        )
        .all()
    )
    for sid, s, e in rows:
        covered = result.setdefault(sid, set())
        s = max(s, start_date)
        e = min(e, end_date)
        covered.update(_iter_dates(s, e))
    return result


def approved_leave_student_ids(
    db: Session,
    day: date,
    student_ids: Optional[Iterable[int]] = None,
) -> Set[int]:
    """Student ids with APPROVED leave covering ``day`` (optionally scoped)."""
    query = db.query(StudentLeave.student_id).filter(
        StudentLeave.status == LeaveStatus.APPROVED,
        StudentLeave.start_date <= day,
        StudentLeave.end_date >= day,
    )
    if student_ids is not None:
        ids = list(student_ids)
        if not ids:
            return set()
        query = query.filter(StudentLeave.student_id.in_(ids))
    return {r[0] for r in query.all()}


def on_leave_student_ids(
    db: Session, class_id: int, division_id: int, day: date
) -> Set[int]:
    """Active students of a class/division with APPROVED leave on ``day``."""
    from app.models.student import Student

    rows = (
        db.query(Student.id)
        .join(StudentLeave, StudentLeave.student_id == Student.id)
        .filter(
            Student.class_id == class_id,
            Student.division_id == division_id,
            Student.is_active.is_(True),
            StudentLeave.status == LeaveStatus.APPROVED,
            StudentLeave.start_date <= day,
            StudentLeave.end_date >= day,
        )
        .all()
    )
    return {r[0] for r in rows}


def effective_status(
    recorded: Optional[AttendanceStatus],
    day_is_holiday: bool,
    on_leave: bool,
) -> EffectiveAttendanceStatus:
    """Apply the effective-status precedence for a single student/date."""
    if day_is_holiday:
        return EffectiveAttendanceStatus.HOLIDAY
    if on_leave:
        return EffectiveAttendanceStatus.APPROVED_LEAVE
    if recorded == AttendanceStatus.PRESENT:
        return EffectiveAttendanceStatus.PRESENT
    if recorded == AttendanceStatus.ABSENT:
        return EffectiveAttendanceStatus.ABSENT
    return EffectiveAttendanceStatus.NOT_RECORDED