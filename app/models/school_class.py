from datetime import datetime
from typing import List

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


class SchoolClass(Base):
    __tablename__ = "classes"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
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

    creator: Mapped["User"] = relationship(foreign_keys=[created_by])
    divisions: Mapped[List["Division"]] = relationship(
        back_populates="school_class",
        cascade="all, delete-orphan",
    )
    students: Mapped[List["Student"]] = relationship(
        back_populates="school_class",
        foreign_keys="Student.class_id",
    )

    def __repr__(self) -> str:
        return f"<SchoolClass(id={self.id}, name={self.name!r})>"


class Division(Base):
    __tablename__ = "divisions"
    __table_args__ = (
        UniqueConstraint("class_id", "name", name="uq_division_class_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    class_id: Mapped[int] = mapped_column(
        ForeignKey("classes.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
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
        back_populates="divisions",
        foreign_keys=[class_id],
    )
    creator: Mapped["User"] = relationship(foreign_keys=[created_by])
    students: Mapped[List["Student"]] = relationship(
        back_populates="division",
        foreign_keys="Student.division_id",
    )

    def __repr__(self) -> str:
        return f"<Division(id={self.id}, name={self.name!r}, class_id={self.class_id})>"
