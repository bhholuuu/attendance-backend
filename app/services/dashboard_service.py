"""Role-aware attendance dashboard aggregation (V2.1).

ADMIN: aggregates across all active classes/divisions/message batches.
TEACHER: aggregates only within the set of class/division pairs the teacher is
assigned to (see teacher_access_service). A teacher with zero assignments sees
empty/zero aggregates (never an error).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import false, func, or_
from sqlalchemy.orm import Session

from app.models.attendance import AttendanceSession, SessionStatus
from app.models.message import (
    MessageBatch,
    MessageBatchStatus,
    MessageDeliveryStatus,
    ParentMessage,
)
from app.models.student import Student
from app.models.user import User
from app.services import teacher_access_service


def _division_pairs(division_map: dict[int, set[int]]) -> list:
    """Flatten division_map into a list of (class_id, division_id) tuples."""
    return [
        (cid, div)
        for cid, divs in division_map.items()
        for div in divs
    ]


def _class_division_scope(column_class, column_division, division_map):
    """Build a SQL OR condition scoping to the allowed (class, division) pairs."""
    pairs = _division_pairs(division_map)
    if not pairs:
        return false()
    return or_(
        *[(column_class == cid) & (column_division.in_(divs))
          for cid, divs in division_map.items()]
    )


def get_attendance_summary(db: Session, user: User, target_date: date) -> dict:
    """Compute the role-aware attendance summary for a date (schema shape)."""
    is_admin = teacher_access_service.is_admin(user)
    division_map = (
        None
        if is_admin
        else teacher_access_service.get_allowed_divisions_by_class(db, user)
    )

    from app.services.effective_attendance_service import (
        approved_leave_student_ids,
        holiday_title_for,
        is_holiday,
    )

    # ---- holidays & students on approved leave for the date ----
    day_is_holiday = is_holiday(db, target_date)
    on_leave_ids = approved_leave_student_ids(db, target_date)
    if not is_admin:
        allowed_pairs = {
            (c, d) for c, divs in division_map.items() for d in divs
        }
        on_leave_ids = _on_leave_in_scope(
            db, on_leave_ids, allowed_pairs
        )
    on_leave_count = len(on_leave_ids)

    # ---- expected students (active, in scope, excluding on-leave) ----
    expected_q = db.query(func.count(Student.id)).filter(Student.is_active.is_(True))
    if not is_admin:
        expected_q = expected_q.filter(
            _class_division_scope(Student.class_id, Student.division_id, division_map)
        )
    if on_leave_ids:
        expected_q = expected_q.filter(~Student.id.in_(on_leave_ids))
    total_expected = expected_q.scalar() or 0

    # ---- completed sessions: present / absent sums ----
    session_q = db.query(
        func.coalesce(func.sum(AttendanceSession.present_count), 0),
        func.coalesce(func.sum(AttendanceSession.absent_count), 0),
    ).filter(
        AttendanceSession.attendance_date == target_date,
        AttendanceSession.status == SessionStatus.COMPLETED,
    )
    if not is_admin:
        session_q = session_q.filter(
            _class_division_scope(
                AttendanceSession.class_id,
                AttendanceSession.division_id,
                division_map,
            )
        )
    present, absent = session_q.one()
    present = int(present or 0)
    absent = int(absent or 0)
    marked = present + absent

    # ---- messaging ----
    pending_batches, failed_messages = _messaging_counts(
        db, target_date, is_admin, division_map
    )

    return {
        "total_expected": total_expected,
        "completed": marked,
        "pending": max(total_expected - marked, 0),
        "present": present,
        "absent": absent,
        "pending_batches": pending_batches,
        "failed_messages": failed_messages,
        "is_holiday": day_is_holiday,
        "holiday_title": holiday_title_for(db, target_date),
        "on_leave": on_leave_count,
    }


def _on_leave_in_scope(
    db: Session, on_leave_ids: set, allowed_pairs: set
) -> set:
    """Restrict on-leave student ids to the teacher's allowed (class, division)
    pairs."""
    if not on_leave_ids:
        return set()
    rows = (
        db.query(Student.id, Student.class_id, Student.division_id)
        .filter(Student.id.in_(on_leave_ids))
        .all()
    )
    return {
        sid for sid, cid, did in rows if (cid, did) in allowed_pairs
    }


def _messaging_counts(
    db: Session,
    target_date: date,
    is_admin: bool,
    division_map: dict[int, set[int]],
) -> tuple[int, int]:
    """Count pending batches and failed messages for a date within scope."""
    # Pending batches = batches for the date not yet fully completed.
    batch_q = db.query(func.count(MessageBatch.id)).filter(
        MessageBatch.attendance_date == target_date,
        MessageBatch.status.in_(
            [MessageBatchStatus.PENDING, MessageBatchStatus.PARTIAL_FAILED]
        ),
    )
    if not is_admin:
        batch_q = batch_q.filter(
            _class_division_scope(MessageBatch.class_id, MessageBatch.division_id, division_map)
        )
    pending_batches = batch_q.scalar() or 0

    # Failed messages for the date, restricted to in-scope batches.
    failed_q = (
        db.query(func.count(ParentMessage.id))
        .join(MessageBatch, ParentMessage.message_batch_id == MessageBatch.id)
        .filter(
            MessageBatch.attendance_date == target_date,
            ParentMessage.delivery_status == MessageDeliveryStatus.FAILED,
        )
    )
    if not is_admin:
        failed_q = failed_q.filter(
            _class_division_scope(MessageBatch.class_id, MessageBatch.division_id, division_map)
        )
    failed_messages = failed_q.scalar() or 0

    return int(pending_batches), int(failed_messages)
