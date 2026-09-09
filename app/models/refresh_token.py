import hashlib
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


def hash_token(token: str) -> str:
    """Return a SHA-256 digest of a refresh token for safe storage.

    Storing a digest (rather than the raw token) means a database leak does not
    expose usable refresh tokens.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class RefreshToken(Base):
    """Server-side record of an issued refresh token.

    The JWT itself remains the source of truth for claims, but this row lets us
    revoke tokens (logout / rotation) and bound them to a specific user.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(  # noqa: F821
        back_populates="refresh_tokens"
    )

    @property
    def is_expired(self) -> bool:
        return self.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc)
