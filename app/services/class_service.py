from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.school_class import Division, SchoolClass
from app.models.user import User

_duplicate_class = (
    "A class with this name already exists. Class names must be unique."
)
_duplicate_division = (
    "A division with this name already exists in this class."
)


def list_classes(db: Session, include_inactive: bool = False) -> list[SchoolClass]:
    query = db.query(SchoolClass)
    if not include_inactive:
        query = query.filter(SchoolClass.is_active.is_(True))
    return (
        query.order_by(SchoolClass.name.asc())
        .all()
    )


def get_class_or_404(db: Session, class_id: int) -> SchoolClass:
    school_class = (
        db.query(SchoolClass).filter(SchoolClass.id == class_id).first()
    )
    if school_class is None:
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")
    return school_class


def create_class(db: Session, name: str, creator: User) -> SchoolClass:
    existing = db.query(SchoolClass).filter(SchoolClass.name == name).first()
    if existing:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_duplicate_class,
        )

    school_class = SchoolClass(
        name=name,
        is_active=True,
        created_by=creator.id,
    )
    db.add(school_class)
    _commit(db, duplicate_detail=_duplicate_class)
    db.refresh(school_class)
    return school_class


def update_class(
    db: Session, school_class: SchoolClass, name: str
) -> SchoolClass:
    existing = (
        db.query(SchoolClass)
        .filter(SchoolClass.name == name, SchoolClass.id != school_class.id)
        .first()
    )
    if existing:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_duplicate_class,
        )

    school_class.name = name
    _commit(db, duplicate_detail=_duplicate_class)
    db.refresh(school_class)
    return school_class


def archive_class(db: Session, school_class: SchoolClass) -> SchoolClass:
    school_class.is_active = False
    # Soft-archive all associated divisions too.
    db.query(Division).filter(Division.class_id == school_class.id).update(
        {Division.is_active: False}
    )
    _commit(db)
    db.refresh(school_class)
    return school_class


def list_divisions(db: Session, class_id: int, include_inactive: bool = False) -> list[Division]:
    query = db.query(Division).filter(Division.class_id == class_id)
    if not include_inactive:
        query = query.filter(Division.is_active.is_(True))
    return query.order_by(Division.name.asc()).all()


def get_division_or_404(db: Session, division_id: int) -> Division:
    division = db.query(Division).filter(Division.id == division_id).first()
    if division is None:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Division not found"
        )
    return division


def create_division(
    db: Session, school_class: SchoolClass, name: str, creator: User
) -> Division:
    existing = (
        db.query(Division)
        .filter(Division.class_id == school_class.id, Division.name == name)
        .first()
    )
    if existing:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_duplicate_division,
        )

    division = Division(
        class_id=school_class.id,
        name=name,
        is_active=True,
        created_by=creator.id,
    )
    db.add(division)
    _commit(db, duplicate_detail=_duplicate_division)
    db.refresh(division)
    return division


def update_division(db: Session, division: Division, name: str) -> Division:
    existing = (
        db.query(Division)
        .filter(
            Division.class_id == division.class_id,
            Division.name == name,
            Division.id != division.id,
        )
        .first()
    )
    if existing:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_duplicate_division,
        )

    division.name = name
    _commit(db, duplicate_detail=_duplicate_division)
    db.refresh(division)
    return division


def archive_division(db: Session, division: Division) -> Division:
    division.is_active = False
    _commit(db)
    db.refresh(division)
    return division


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
