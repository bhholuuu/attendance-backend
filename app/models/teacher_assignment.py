from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base
from app.models.school_class import Division, SchoolClass
from app.models.user import User


class TeacherAssignment(Base):
    """A single TEACHER-to-(class[, division]) access assignment (V2.1).

    Semantics:
      * ``division_id is None``  => the teacher may access ALL active
        divisions of the assigned class.
      * ``division_id is set``   => the teacher may access only that specific
        division of the assigned class.

    Conflict rules (enforced by the service, and guarded here at the DB):
      * A teacher may have at most one assignment per (class, division), where
        division NULL means "all divisions of the class". The unique index
        ``uq_teacher_assignment_pair`` in PostgreSQL treats NULL division as
        distinct from a real division (NULLs are not equal), so the service
        layer is the authority for the "all divisions vs specific division"
        redundancy rules. See `teacher_assignment_service`.
    """

    __tablename__ = "teacher_assignments"
    __table_args__ = (
        # Prevent the exact same (teacher, class, division) assignment twice.
        # NOTE: because SQL treats NULLs as distinct in unique indexes, two
        # rows with NULL division_id are NOT considered duplicates here; the
        # service layer prevents duplicate "all divisions" rows explicitly.
        UniqueConstraint(
            "teacher_id",
            "class_id",
            "division_id",
            name="uq_teacher_assignment_triple",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    class_id: Mapped[int] = mapped_column(
        ForeignKey("classes.id"), nullable=False, index=True
    )
    # Optional: NULL means ALL active divisions of the class.
    division_id: Mapped[int] = mapped_column(
        ForeignKey("divisions.id"), nullable=True, index=True
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    teacher: Mapped[User] = relationship(foreign_keys=[teacher_id])
    school_class: Mapped[SchoolClass] = relationship(foreign_keys=[class_id])
    division: Mapped[Division] = relationship(foreign_keys=[division_id])
    creator: Mapped[User] = relationship(foreign_keys=[created_by])

    def __repr__(self) -> str:
        return (
            f"<TeacherAssignment(id={self.id}, teacher_id={self.teacher_id}, "
            f"class_id={self.class_id}, division_id={self.division_id})>"
        )
