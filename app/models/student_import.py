from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


class StudentImport(Base):
    """Audit record of a bulk student import (PART 9).

    Stores safe metadata only: who imported, when, the file name and the
    preview token (a SHA-256 of the uploaded content). The uploaded file bytes
    themselves are never persisted.
    """

    __tablename__ = "student_imports"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    imported_by: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    preview_token: Mapped[str] = mapped_column(String(64), nullable=False)
    allow_partial: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    total_records: Mapped[int] = mapped_column(Integer, nullable=False)
    imported_records: Mapped[int] = mapped_column(Integer, nullable=False)
    invalid_records: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    importer: Mapped["User"] = relationship(foreign_keys=[imported_by])

    def __repr__(self) -> str:
        return (
            f"<StudentImport(id={self.id}, file_name={self.file_name!r}, "
            f"imported={self.imported_records}/{self.total_records})>"
        )