"""Assignment creation/removal logic and V2.1 conflict rules.

Precedence rule (documented, enforced here):
  * A teacher has at most one "ALL divisions" scope per class (division_id NULL).
  * Once a teacher has an ALL scope for a class, adding a specific division
    assignment for that same class is REDUNDANT and is rejected with a clear
    message telling the admin to remove the specific assignment first.
  * While a teacher holds specific-division assignments for a class but no ALL
    scope, adding an ALL scope is CONFLICTING (it would silently grant the rest
    of the divisions) and is rejected until existing specifics are removed.
  * You can always add a specific division that is not yet assigned, provided no
    ALL scope exists for the class.
"""

from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.school_class import Division, SchoolClass
from app.models.teacher_assignment import TeacherAssignment
from app.models.user import User, UserRole


def validate_teacher_assignment(
    db: Session,
    teacher_id: int,
    class_id: int,
    division_id: Optional[int],
) -> None:
    """Validate the inputs of a proposed assignment and raise on violation.

    Raises 404 when the teacher/class/division do not exist and 400/409 for the
    precedence/redundancy rules. Does not write anything.
    """
    teacher = db.query(User).filter(User.id == teacher_id).first()
    if teacher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Teacher not found"
        )
    if teacher.role != UserRole.TEACHER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Assignments can only be created for users with the TEACHER role",
        )
    if not teacher.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot assign an inactive teacher",
        )

    school_class = db.query(SchoolClass).filter(SchoolClass.id == class_id).first()
    if school_class is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Class not found"
        )
    if not school_class.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot assign to an inactive class",
        )

    division: Optional[Division] = None
    if division_id is not None:
        division = (
            db.query(Division).filter(Division.id == division_id).first()
        )
        if division is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Division not found"
            )
        if division.class_id != class_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Division does not belong to the selected class",
            )
        if not division.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot assign to an inactive division",
            )

    _validate_conflicts(db, teacher_id, class_id, division_id)


def _validate_conflicts(
    db: Session,
    teacher_id: int,
    class_id: int,
    division_id: Optional[int],
) -> None:
    all_scope_exists = (
        db.query(TeacherAssignment)
        .filter(
            TeacherAssignment.teacher_id == teacher_id,
            TeacherAssignment.class_id == class_id,
            TeacherAssignment.division_id.is_(None),
        )
        .first()
        is not None
    )

    if all_scope_exists:
        if division_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This teacher already has access to ALL divisions of this class. "
                    "A specific division assignment would be redundant; remove the "
                    "all-divisions assignment first or leave division unset."
                ),
            )
        # Adding a duplicate ALL scope is prevented at the DB unique index, but we
        # catch it early for a friendly message.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This teacher is already assigned to all divisions of this class.",
        )

    if division_id is None:
        # Adding an ALL scope while specific scopes exist would conflict.
        specific_exists = (
            db.query(TeacherAssignment)
            .filter(
                TeacherAssignment.teacher_id == teacher_id,
                TeacherAssignment.class_id == class_id,
                TeacherAssignment.division_id.isnot(None),
            )
            .first()
            is not None
        )
        if specific_exists:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This teacher already has specific-division assignments for "
                    "this class. Remove them first before assigning all divisions."
                ),
            )
        # Need a division_id for the specific branch below; handled by caller.
        return

    # Specific-division add: reject an exact duplicate early (DB index is the
    # authoritative guard; this gives a friendly message).
    duplicate = (
        db.query(TeacherAssignment)
        .filter(
            TeacherAssignment.teacher_id == teacher_id,
            TeacherAssignment.class_id == class_id,
            TeacherAssignment.division_id == division_id,
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This division is already assigned to the teacher.",
        )


def create_assignment(
    db: Session, teacher_id: int, class_id: int, division_id: Optional[int], creator: User
) -> TeacherAssignment:
    validate_teacher_assignment(db, teacher_id, class_id, division_id)
    assignment = TeacherAssignment(
        teacher_id=teacher_id,
        class_id=class_id,
        division_id=division_id,
        created_by=creator.id,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment


def delete_assignment(db: Session, assignment_id: int) -> None:
    assignment = (
        db.query(TeacherAssignment)
        .filter(TeacherAssignment.id == assignment_id)
        .first()
    )
    if assignment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found"
        )
    db.delete(assignment)
    db.commit()
