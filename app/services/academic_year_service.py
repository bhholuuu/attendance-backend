"""Academic year management (V2.3)."""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.academic_year import AcademicYear
from app.models.user import User


def _http(status_code: int, detail: str):
    from fastapi import HTTPException

    return HTTPException(status_code=status_code, detail=detail)


def _validate_year_range(start_date: date, end_date: date) -> None:
    if start_date > end_date:
        raise _http(400, "Academic year start date must not be after its end date")


def _overlap_condition(start_date: date, end_date: date):
    """Rows whose [start_date, end_date] overlaps [start, end] (inclusive)."""
    return ~(
        (AcademicYear.start_date > end_date) | (AcademicYear.end_date < start_date)
    )


def get_academic_year(db: Session, year_id: int) -> AcademicYear:
    year = db.query(AcademicYear).filter(AcademicYear.id == year_id).first()
    if year is None:
        raise _http(404, "Academic year not found")
    return year


def list_academic_years(db: Session) -> list[AcademicYear]:
    return (
        db.query(AcademicYear)
        .order_by(AcademicYear.start_date.desc())
        .all()
    )


def create_academic_year(
    db: Session,
    *,
    name: str,
    start_date: date,
    end_date: date,
    is_active: bool = True,
    creator: User,
) -> AcademicYear:
    """Create an academic year, rejecting overlapping active years.

    Overlapping ACTIVE years are rejected (only one active range at a time).
    An inactive year may overlap anything (it is historical/planned data).
    """
    _validate_year_range(start_date, end_date)

    if is_active:
        existing = (
            db.query(AcademicYear.id)
            .filter(
                AcademicYear.is_active.is_(True),
                _overlap_condition(start_date, end_date),
            )
            .first()
        )
        if existing is not None:
            raise _http(
                400,
                "The new academic year overlaps an existing active academic year",
            )

    name = (name or "").strip()
    if not name:
        raise _http(400, "Academic year name is required")

    year = AcademicYear(
        name=name,
        start_date=start_date,
        end_date=end_date,
        is_active=is_active,
        created_by=creator.id,
    )
    db.add(year)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _http(
            409, "An academic year with this name already exists"
        )
    db.refresh(year)
    return year


def update_academic_year(
    db: Session,
    year: AcademicYear,
    *,
    name: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    is_active: Optional[bool] = None,
) -> AcademicYear:
    """Update an academic year; the resulting active range must not overlap
    another active year (the year itself is excluded from the check)."""
    new_start = start_date if start_date is not None else year.start_date
    new_end = end_date if end_date is not None else year.end_date
    new_active = is_active if is_active is not None else year.is_active

    _validate_year_range(new_start, new_end)

    if new_active:
        existing = (
            db.query(AcademicYear.id)
            .filter(
                AcademicYear.id != year.id,
                AcademicYear.is_active.is_(True),
                _overlap_condition(new_start, new_end),
            )
            .first()
        )
        if existing is not None:
            raise _http(
                400,
                "The updated academic year overlaps an existing active academic year",
            )

    if name is not None:
        year.name = (name or "").strip() or year.name
    if start_date is not None:
        year.start_date = start_date
    if end_date is not None:
        year.end_date = end_date
    if is_active is not None:
        year.is_active = is_active

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _http(409, "An academic year with this name already exists")
    db.refresh(year)
    return year


def get_active_academic_year(db: Session) -> Optional[AcademicYear]:
    """The single currently active academic year (usually only one)."""
    return (
        db.query(AcademicYear)
        .filter(AcademicYear.is_active.is_(True))
        .order_by(AcademicYear.start_date.desc())
        .first()
    )