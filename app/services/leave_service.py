"""Student leave management (V2.3).

Only APPROVED leaves affect attendance calculations. Creates are ADMIN-only and
start as PENDING; an ADMIN approves/rejects/cancels. Teachers may only view
leaves of students in their assigned classes/divisions (router enforces scope).

Rules:
  * Dates must fall within an active academic year when one exists.
  * A student may not have two APPROVED leaves with overlapping ranges; an
    overlap with an existing APPROVED leave is rejected with a clear message
    and must be resolved by an admin.
  * A leave overlapping a holiday is never double counted (holiday wins).
  * If a leave is approved AFTER an ABSENT was already recorded for those
    dates, the historical attendance record is NOT rewritten; reconciliation
    is flagged in documentation instead.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.student import Student
from app.models.student_leave import LeaveStatus, StudentLeave
from app.models.user import User
from app.services.effective_attendance_service import (
    _active_academic_year_ids,
)


def _http(status_code: int, detail: str):
    from fastapi import HTTPException

    return HTTPException(status_code=status_code, detail=detail)


def _validate_dates(start_date: date, end_date: date) -> None:
    if start_date > end_date:
        raise _http(400, "Leave start date must not be after its end date")


def _validate_year_bounds(db: Session, start_date: date, end_date: date) -> None:
    """Require leave dates to fall inside an active academic year.

    When no active academic year exists (legacy school setups) the check is
    skipped so existing attendance-only schools keep working.
    """
    year_ids = _active_academic_year_ids(db)
    if not year_ids:
        return
    from app.models.academic_year import AcademicYear

    years = (
        db.query(AcademicYear.start_date, AcademicYear.end_date)
        .filter(AcademicYear.id.in_(year_ids))
        .all()
    )
    inside = any(
        y_start <= start_date and end_date <= y_end for y_start, y_end in years
    )
    if not inside:
        raise _http(
            400,
            "Leave dates must fall within an active academic year",
        )


def _check_overlap(
    db: Session,
    student_id: int,
    start_date: date,
    end_date: date,
    exclude_leave_id: Optional[int] = None,
) -> None:
    query = db.query(StudentLeave.id).filter(
        StudentLeave.student_id == student_id,
        StudentLeave.status == LeaveStatus.APPROVED,
        ~(
            (StudentLeave.start_date > end_date)
            | (StudentLeave.end_date < start_date)
        ),
    )
    if exclude_leave_id is not None:
        query = query.filter(StudentLeave.id != exclude_leave_id)
    if query.first() is not None:
        raise _http(
            400,
            "Student already has an approved leave overlapping this date range",
        )


def get_leave(db: Session, leave_id: int) -> StudentLeave:
    leave = db.query(StudentLeave).filter(StudentLeave.id == leave_id).first()
    if leave is None:
        raise _http(404, "Student leave not found")
    return leave


def list_leaves(
    db: Session,
    *,
    student_id: Optional[int] = None,
    status: Optional[LeaveStatus] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> list[StudentLeave]:
    query = db.query(StudentLeave)
    if student_id is not None:
        query = query.filter(StudentLeave.student_id == student_id)
    if status is not None:
        query = query.filter(StudentLeave.status == status)
    if start_date is not None:
        query = query.filter(StudentLeave.end_date >= start_date)
    if end_date is not None:
        query = query.filter(StudentLeave.start_date <= end_date)
    return query.order_by(
        StudentLeave.start_date.desc(), StudentLeave.id.desc()
    ).all()


def create_leave(
    db: Session,
    *,
    student_id: int,
    start_date: date,
    end_date: date,
    leave_type: Optional[str] = None,
    reason: Optional[str] = None,
    created_by: User,
) -> StudentLeave:
    """Create a leave as PENDING. ADMIN-only (enforced in the router)."""
    _validate_dates(start_date, end_date)
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is None:
        raise _http(404, "Student not found")
    if not student.is_active:
        raise _http(400, "Cannot create a leave for an archived student")
    _validate_year_bounds(db, start_date, end_date)
    # Only reject overlap against APPROVED leaves; PENDING/REJECTED may overlap
    # and are resolved when approved.
    _check_overlap(db, student_id, start_date, end_date)

    leave = StudentLeave(
        student_id=student_id,
        start_date=start_date,
        end_date=end_date,
        leave_type=leave_type,
        reason=reason,
        status=LeaveStatus.PENDING,
        created_by=created_by.id,
    )
    db.add(leave)
    db.commit()
    db.refresh(leave)
    return leave


def approve_leave(db: Session, leave: StudentLeave, approver: User) -> StudentLeave:
    """Approve a leave (ADMIN-only in the router).

    Validates the student is still active and no APPROVED overlap exists at
    approval time. Historical ABSENT records are never touched.
    """
    if leave.student is None or not leave.student.is_active:
        raise _http(400, "Cannot approve a leave for an archived student")
    if leave.status == LeaveStatus.CANCELLED:
        raise _http(400, "A cancelled leave cannot be approved")
    _check_overlap(
        db, leave.student_id, leave.start_date, leave.end_date,
        exclude_leave_id=leave.id,
    )

    leave.status = LeaveStatus.APPROVED
    leave.approved_by = approver.id
    leave.approved_at = datetime.utcnow()
    db.commit()
    db.refresh(leave)
    return leave


def reject_leave(db: Session, leave: StudentLeave, actor: User) -> StudentLeave:
    del actor  # approval identity is only recorded on approve
    if leave.status == LeaveStatus.CANCELLED:
        raise _http(400, "A cancelled leave cannot be rejected")
    if leave.status == LeaveStatus.REJECTED:
        raise _http(400, "Leave is already rejected")
    leave.status = LeaveStatus.REJECTED
    db.commit()
    db.refresh(leave)
    return leave


def cancel_leave(db: Session, leave: StudentLeave, actor: User) -> StudentLeave:
    del actor
    if leave.status == LeaveStatus.CANCELLED:
        raise _http(400, "Leave is already cancelled")
    leave.status = LeaveStatus.CANCELLED
    db.commit()
    db.refresh(leave)
    return leave