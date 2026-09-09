import re
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.school_class import Division, SchoolClass
from app.models.student import Student
from app.models.user import User

_duplicate_roll = (
    "A student with this roll number already exists in this class and division."
)


def _natural_sort_key(roll: str):
    """Natural sort key for roll numbers.

    Roll numbers are stored as free-form strings because some schools use
    non-numeric values (e.g. "A1", "B-12"). This key sorts by the leading
    numeric part (so 2 precedes 10) and then lexically by the remainder,
    giving a sensible order such as: 1, 2, 3, 10, A1, B-2.
    """
    match = re.match(r"^(\d+)", roll)
    if match:
        return (0, int(match.group(1)), roll.lower())
    return (1, 0, roll.lower())


def get_student_or_404(db: Session, student_id: int) -> Student:
    from fastapi import HTTPException, status

    student = db.query(Student).filter(Student.id == student_id).first()
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Student not found"
        )
    return student


def _validate_relations(
    db: Session, class_id: int, division_id: int, allow_inactive: bool = False
) -> SchoolClass:
    """Validate that the class exists and the division belongs to it.

    Returns the validated active SchoolClass. Raises an HTTPException with an
    appropriate status code when the class/division is missing, archived, or
    when the division does not belong to the class.
    """
    from fastapi import HTTPException, status

    school_class = (
        db.query(SchoolClass).filter(SchoolClass.id == class_id).first()
    )
    if school_class is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Class not found"
        )
    if not school_class.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot use an archived class",
        )

    division = db.query(Division).filter(Division.id == division_id).first()
    if division is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Division not found"
        )
    if division.class_id != class_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Division does not belong to the specified class",
        )
    if not division.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot use an archived division",
        )
    return school_class


def _roll_conflict(
    db: Session,
    class_id: int,
    division_id: int,
    roll_number: str,
    exclude_student_id: Optional[int] = None,
) -> bool:
    """True if an active student already uses the roll in the same class/division."""
    query = db.query(Student).filter(
        Student.class_id == class_id,
        Student.division_id == division_id,
        Student.roll_number == roll_number,
        Student.is_active.is_(True),
    )
    if exclude_student_id is not None:
        query = query.filter(Student.id != exclude_student_id)
    return query.first() is not None


def create_student(
    db: Session,
    *,
    class_id: int,
    division_id: int,
    name: str,
    roll_number: str,
    parent_name: str,
    parent_whatsapp_number: str,
    creator: User,
) -> Student:
    _validate_relations(db, class_id, division_id)
    if _roll_conflict(db, class_id, division_id, roll_number):
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_duplicate_roll
        )

    student = Student(
        class_id=class_id,
        division_id=division_id,
        name=name,
        roll_number=roll_number,
        parent_name=parent_name,
        parent_whatsapp_number=parent_whatsapp_number,
        is_active=True,
        created_by=creator.id,
    )
    db.add(student)
    _commit(db, duplicate_detail=_duplicate_roll)
    db.refresh(student)
    return student


def list_students(
    db: Session,
    *,
    class_id: Optional[int] = None,
    division_id: Optional[int] = None,
    search: Optional[str] = None,
    active_only: bool = True,
    division_map: Optional[dict[int, set[int]]] = None,
) -> list[Student]:
    query = db.query(Student)

    if class_id is not None:
        query = query.filter(Student.class_id == class_id)
    if division_id is not None:
        query = query.filter(Student.division_id == division_id)

    # Teacher-scope filter: pair-based (class_id, division_id) access.
    if division_map is not None:
        if not division_map:
            return []
        pair_conditions = [
            (Student.class_id == cid) & (Student.division_id.in_(divs))
            for cid, divs in division_map.items()
        ]
        query = query.filter(or_(*pair_conditions))

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(Student.name.ilike(term), Student.roll_number.ilike(term))
        )

    if active_only:
        query = query.filter(Student.is_active.is_(True))

    students = query.all()
    # Natural sort primarily by roll number (see _natural_sort_key), then by
    # name as a stable tiebreaker.
    students.sort(key=lambda s: (_natural_sort_key(s.roll_number), s.name))
    return students


def update_student(
    db: Session,
    student: Student,
    *,
    name: Optional[str] = None,
    roll_number: Optional[str] = None,
    parent_name: Optional[str] = None,
    parent_whatsapp_number: Optional[str] = None,
    class_id: Optional[int] = None,
    division_id: Optional[int] = None,
) -> Student:
    new_class_id = class_id if class_id is not None else student.class_id
    new_division_id = (
        division_id if division_id is not None else student.division_id
    )
    # If either the class or division is changing, re-validate the pair so a
    # student can never be moved into a division that does not belong to the
    # selected class.
    if class_id is not None or division_id is not None:
        _validate_relations(db, new_class_id, new_division_id)

    new_roll = roll_number if roll_number is not None else student.roll_number
    if _roll_conflict(
        db,
        new_class_id,
        new_division_id,
        new_roll,
        exclude_student_id=student.id,
    ):
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_duplicate_roll
        )

    if name is not None:
        student.name = name
    if roll_number is not None:
        student.roll_number = roll_number
    if parent_name is not None:
        student.parent_name = parent_name
    if parent_whatsapp_number is not None:
        student.parent_whatsapp_number = parent_whatsapp_number
    if class_id is not None:
        student.class_id = class_id
    if division_id is not None:
        student.division_id = division_id

    _commit(db, duplicate_detail=_duplicate_roll)
    db.refresh(student)
    return student


def archive_student(db: Session, student: Student) -> Student:
    student.is_active = False
    _commit(db)
    db.refresh(student)
    return student


def restore_student(db: Session, student: Student) -> Student:
    from fastapi import HTTPException, status

    # Parent class and division must be active to restore into them.
    _validate_relations(db, student.class_id, student.division_id)

    # Restoring must not collide with an existing active student's roll number
    # in the same class/division.
    if _roll_conflict(
        db,
        student.class_id,
        student.division_id,
        student.roll_number,
        exclude_student_id=student.id,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cannot restore: another active student already uses this "
                "roll number in this class and division."
            ),
        )

    student.is_active = True
    _commit(db)
    db.refresh(student)
    return student


def _commit(db: Session, duplicate_detail: str = "Duplicate record") -> None:
    from fastapi import HTTPException, status

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=duplicate_detail,
        )
