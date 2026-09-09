from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


class Student(Base):
    __tablename__ = "students"

    # Roll numbers must be unique among *active* students within the same
    # class/division. A partial unique index enforces this at the database
    # level while still allowing archived students (is_active=false) to keep
    # their historical roll number, and permitting the same roll number to
    # exist in a different division.
    __table_args__ = (
        Index(
            "uq_active_student_class_division_roll",
            "class_id",
            "division_id",
            "roll_number",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    class_id: Mapped[int] = mapped_column(
        ForeignKey("classes.id"), nullable=False, index=True
    )
    division_id: Mapped[int] = mapped_column(
        ForeignKey("divisions.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    roll_number: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    parent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    parent_whatsapp_number: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    school_class: Mapped["SchoolClass"] = relationship(
        foreign_keys=[class_id],
    )
    division: Mapped["Division"] = relationship(
        foreign_keys=[division_id],
    )
    creator: Mapped["User"] = relationship(foreign_keys=[created_by])

    def __repr__(self) -> str:
        return (
            f"<Student(id={self.id}, name={self.name!r}, "
            f"roll_number={self.roll_number!r})>"
        )
