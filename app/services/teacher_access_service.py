"""Centralized teacher access authorization for V2.1.

Every route that serves data to a TEACHER user MUST consult this service so the
"can this teacher see / touch this class / division / session / batch?" decision
lives in exactly one place. ADMINS are always granted access and always see
everything; teacher assignment rows are the only source of teacher access.

Assignment semantics (see ``TeacherAssignment`` model):
  * division_id None  => all active divisions of the class (auto-gains new
    divisions as they are created).
  * division_id set   => only that specific division; newly added divisions of
    the same class are NOT automatically visible.

Rule: an absent/unassigned teacher has access to nothing.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.attendance import AttendanceSession
from app.models.message import MessageBatch
from app.models.school_class import Division
from app.models.student import Student
from app.models.teacher_assignment import TeacherAssignment
from app.models.user import User, UserRole


from app.services import audit_service


def is_admin(user: User) -> bool:
    """True when the user may bypass all assignment-based access rules."""
    return user.role == UserRole.ADMIN


def _403(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def get_allowed_class_ids(db: Session, teacher: User) -> set[int]:
    """Set of class ids a teacher may access (admin => all via caller check)."""
    if is_admin(teacher):
        raise ValueError("Admins are not scoped by assignments; call the unfiltered path.")
    rows = (
        db.query(TeacherAssignment.class_id)
        .filter(TeacherAssignment.teacher_id == teacher.id)
        .all()
    )
    return {r[0] for r in rows}


def _all_divisions_assignment_exists(
    db: Session, teacher_id: int, class_id: int
) -> bool:
    return (
        db.query(TeacherAssignment)
        .filter(
            TeacherAssignment.teacher_id == teacher_id,
            TeacherAssignment.class_id == class_id,
            TeacherAssignment.division_id.is_(None),
        )
        .first()
        is not None
    )


def get_allowed_division_ids(
    db: Session, teacher: User, class_id: Optional[int] = None
) -> set[int]:
    """Set of division ids a teacher may access (union across assignments).

    When a teacher holds an "all divisions" assignment for a class, every
    ACTIVE division of that class is accessible. When they hold only specific
    division assignments, only those exact division ids are accessible.
    """
    if is_admin(teacher):
        raise ValueError("Admins are not scoped by assignments.")

    query = db.query(TeacherAssignment).filter(
        TeacherAssignment.teacher_id == teacher.id
    )
    if class_id is not None:
        query = query.filter(TeacherAssignment.class_id == class_id)
    assignments = query.all()

    allowed: set[int] = set()
    # Group of "all divisions" classes, to expand to active divisions.
    all_divisions_class_ids: set[int] = set()
    for a in assignments:
        if a.division_id is None:
            all_divisions_class_ids.add(a.class_id)
        else:
            allowed.add(a.division_id)

    if not all_divisions_class_ids:
        return allowed

    active_divisions = (
        db.query(Division.id)
        .filter(
            Division.class_id.in_(all_divisions_class_ids),
            Division.is_active.is_(True),
        )
        .all()
    )
    allowed.update(d[0] for d in active_divisions)
    return allowed


def can_access_class(db: Session, user: User, class_id: int) -> bool:
    """Whether ``user`` may access a class (and all of its non-division data)."""
    if is_admin(user):
        return True
    return (
        db.query(TeacherAssignment)
        .filter(
            TeacherAssignment.teacher_id == user.id,
            TeacherAssignment.class_id == class_id,
        )
        .first()
        is not None
    )


def can_access_division(
    db: Session, user: User, class_id: int, division_id: int
) -> bool:
    """Whether ``user`` may access a specific class/division pair."""
    if is_admin(user):
        return True
    if not can_access_class(db, user, class_id):
        return False
    # An "all divisions" assignment grants the whole class.
    if _all_divisions_assignment_exists(db, user.id, class_id):
        return True
    return (
        db.query(TeacherAssignment)
        .filter(
            TeacherAssignment.teacher_id == user.id,
            TeacherAssignment.class_id == class_id,
            TeacherAssignment.division_id == division_id,
        )
        .first()
        is not None
    )


def can_access_attendance_session(
    db: Session, user: User, session: AttendanceSession
) -> bool:
    return can_access_division(db, user, session.class_id, session.division_id)


def can_access_message_batch(
    db: Session, user: User, batch: MessageBatch
) -> bool:
    return can_access_division(db, user, batch.class_id, batch.division_id)


# ---------------------------------------------------------------------------
# Require-style helpers for router 403 enforcement.
# ---------------------------------------------------------------------------


def require_access_class(db: Session, user: User, class_id: int) -> None:
    """Raise 403 unless the user may access the class (admin passes always)."""
    if not can_access_class(db, user, class_id):
        audit_service.record(
            db,
            action="ACCESS_DENIED",
            result="DENIED",
            entity_type="class",
            entity_id=class_id,
            details={
                "username": user.username,
                "scope_required": f"class:{class_id}",
                "reason": "unassigned_class",
            },
            actor=user,
        )
        audit_service.commit_safely(db)
        raise _403("You do not have access to this class")


def require_access_division(
    db: Session, user: User, class_id: int, division_id: int
) -> None:
    """Raise 403 unless the user may access the class/division pair."""
    if not can_access_division(db, user, class_id, division_id):
        audit_service.record(
            db,
            action="ACCESS_DENIED",
            result="DENIED",
            entity_type="division",
            entity_id=division_id,
            details={
                "username": user.username,
                "scope_required": f"class:{class_id}/division:{division_id}",
                "reason": "unassigned_division",
            },
            actor=user,
        )
        audit_service.commit_safely(db)
        raise _403("You do not have access to this division")


# ---------------------------------------------------------------------------
# Query-scope helpers: produce SQL filters for the "list" endpoints.
# ---------------------------------------------------------------------------


def get_allowed_divisions_by_class(
    db: Session, teacher: User
) -> dict[int, set[int]]:
    """Map every accessible class id -> the set of accessible division ids.

    Classes with an "all divisions" assignment map to their full set of active
    divisions; classes with only specific-division assignments map to exactly
    those division ids. Classes with no divisions are still included as an empty
    set (the teacher has class-level access even if the class currently has no
    active divisions). Admins should not call this (see is_admin).
    """
    assignments = (
        db.query(TeacherAssignment)
        .filter(TeacherAssignment.teacher_id == teacher.id)
        .all()
    )

    result: dict[int, set[int]] = {}
    all_divisions_classes: set[int] = set()
    for a in assignments:
        result.setdefault(a.class_id, set())
        if a.division_id is None:
            all_divisions_classes.add(a.class_id)
        elif a.division_id is not None:
            result[a.class_id].add(a.division_id)

    if all_divisions_classes:
        rows = (
            db.query(Division.class_id, Division.id)
            .filter(
                Division.class_id.in_(all_divisions_classes),
                Division.is_active.is_(True),
            )
            .all()
        )
        for class_id, division_id in rows:
            result.setdefault(class_id, set()).add(division_id)

    return result


def accessible_student_ids(
    db: Session, teacher: User, division_map: dict[int, set[int]]
) -> Optional[set[int]]:
    """Return the set of student ids a teacher may access.

    A student is visible if the teacher can access that student's class and that
    specific division. Returns None for admins (meaning "no filter"). Used by
    the student list/detail routes to hard-hide unassigned data.
    """
    if is_admin(teacher):
        return None

    if not division_map:
        return set()

    classes = list(division_map.keys())
    # Build a set of allowed (class_id, division_id) tuples, then filter.
    allowed_pairs = {
        (c, d) for c, divs in division_map.items() for d in divs
    }

    rows = (
        db.query(Student.id, Student.class_id, Student.division_id)
        .filter(Student.class_id.in_(classes))
        .all()
    )
    return {
        sid
        for sid, cid, did in rows
        if (cid, did) in allowed_pairs
    }
