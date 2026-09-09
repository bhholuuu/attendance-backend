from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base


class AdminSetting(Base):
    """Key-value store for administrative configuration.

    Stores non-secret administrative settings such as school contact numbers,
    messaging configuration flags, and school information. Passwords and
    sensitive credentials must never be stored here.
    """

    __tablename__ = "admin_settings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    key: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    def __repr__(self) -> str:
        return f"<AdminSetting(key={self.key!r})>"
