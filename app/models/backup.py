"""V2.7 backup metadata model.

Tracks every database backup and restore operation performed against the
PostgreSQL production database. The actual backup bytes are produced by
PostgreSQL-native tooling (``pg_dump``/``pg_restore``) and stored through a
configurable storage provider; this table records *metadata* about each run so
admins can see the full lifecycle (QUEUED -> IN_PROGRESS -> COMPLETED/FAILED,
then VERIFIED/CORRUPTED) and so retention/rotation can be applied safely.

Design rules:
  * This is metadata only. Backup files themselves always live in the storage
    provider (never inside the database), so a corrupt DB can still expose the
    recovery points needed to restore it.
  * Each backup records a SHA-256 checksum so integrity can be verified without
    the DB trusting itself.
  * ``storage_ref`` is the provider-relative reference to the backup object
    (a file path for the local provider). It never contains credentials.
  * ``created_by_id`` records which admin triggered the backup (None for
    fully automatic/scheduled backups). Restores are always admin-initiated.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base


class BackupType(str, enum.Enum):
    """Why a backup was created.

    MANUAL         - an admin clicked "Back up now" in the dashboard.
    SCHEDULED      - created by the retention/schedule service automatically.
    PRE_MIGRATION  - created automatically before an Alembic migration applies.
    PRE_RESTORE    - created automatically right before a restore, to preserve
                     the current (recovery) point before it is overwritten.
    """

    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"
    PRE_MIGRATION = "PRE_MIGRATION"
    PRE_RESTORE = "PRE_RESTORE"


class BackupStatus(str, enum.Enum):
    """Lifecycle of a backup job.

    QUEUED      - accepted by the API (HTTP 202); worker has not started.
    IN_PROGRESS - worker is running pg_dump / encrypting / uploading.
    COMPLETED   - backup file produced, encrypted, stored and checksummed.
    FAILED      - the job failed at any stage; check error_message.
    """

    QUEUED = "QUEUED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class VerificationStatus(str, enum.Enum):
    """Integrity verification outcome for a finished backup.

    PENDING    - not yet verified (or verification not yet run).
    VERIFIED   - checksum matches; the backup is a trustworthy recovery point.
    CORRUPTED  - checksum mismatch or file unreadable; do NOT restore from it.
    """

    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    CORRUPTED = "CORRUPTED"


class BackupMetadata(Base):
    """Metadata row describing one backup job / recovery point."""

    __tablename__ = "backup_metadata"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    backup_type: Mapped[BackupType] = mapped_column(
        Enum(BackupType, name="backup_type"), nullable=False, index=True
    )
    status: Mapped[BackupStatus] = mapped_column(
        Enum(BackupStatus, name="backup_status"),
        nullable=False,
        default=BackupStatus.QUEUED,
        index=True,
    )
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, name="backup_verification_status"),
        nullable=False,
        default=VerificationStatus.PENDING,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The Alembic migration version this backup was taken against (e.g. "0008").
    migration_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    # Provider-relative reference to the stored backup object (never a secret).
    storage_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # How long (days) to keep this backup before retention cleanup removes it.
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Actor that initiated the operation. None for fully automatic jobs.
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<BackupMetadata(id={self.id}, type={self.backup_type.value}, "
            f"status={self.status.value}, verif={self.verification_status.value})>"
        )


class RestoreRecord(Base):
    """Append-only log of restore operations (V2.7).

    Every restore is logged here with its outcome. Restores are the most
    destructive operation in the system, so each one keeps a reference to the
    pre-restore emergency backup that preserves the recovery point, the
    administrator who approved it, and the final result.
    """

    __tablename__ = "restore_records"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    backup_id: Mapped[int] = mapped_column(
        ForeignKey("backup_metadata.id"), nullable=False, index=True
    )
    source_migration_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Reference to the Pre-Restore emergency backup taken before recovery.
    pre_restore_backup_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING", index=True
    )  # PENDING | SUCCESS | FAILED
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )

    def __repr__(self) -> str:
        return (
            f"<RestoreRecord(id={self.id}, backup_id={self.backup_id}, "
            f"result={self.result})>"
        )
