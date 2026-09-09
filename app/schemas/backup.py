"""V2.7 backup & restore API schemas (admin-only)."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.backup import BackupStatus, BackupType, VerificationStatus


class BackupMetadataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    backup_type: BackupType
    status: BackupStatus
    verification_status: VerificationStatus
    created_at: datetime
    completed_at: Optional[datetime] = None
    migration_version: Optional[str] = None
    storage_provider: str
    size_bytes: Optional[int] = None
    checksum: Optional[str] = None
    encrypted: bool
    retention_days: Optional[int] = None
    error_message: Optional[str] = None
    created_by_id: Optional[int] = None
    last_verified_at: Optional[datetime] = None


class BackupListResponse(BaseModel):
    items: list[BackupMetadataResponse]
    total: int


class BackupCreateRequest(BaseModel):
    # Optional override for default retention (days). No other fields: a
    # manual backup is simply "the database, now".
    retention_days: Optional[int] = None


class BackupCreatedResponse(BaseModel):
    """Returned with HTTP 202 Accepted for an enqueued backup job."""

    backup: BackupMetadataResponse
    status: str = "QUEUED"
    message: str = (
        "Backup job queued. Poll GET /backups/{id} until status is "
        "COMPLETED or FAILED."
    )
    poll_url: str


class BackupVerifyResponse(BaseModel):
    backup: BackupMetadataResponse


class RestoreRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    backup_id: int
    source_migration_version: Optional[str] = None
    pre_restore_backup_id: Optional[int] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    result: str
    error_message: Optional[str] = None
    created_by_id: Optional[int] = None


class RestoreStartRequest(BaseModel):
    """Explicit confirmation payload for a restore.

    A restore is the most destructive operation in the system. The client must
    supply ``confirmation`` equal to the exact phrase AGREED to proceed. This
    is server-side enforcement, never a UI convenience.
    """

    confirmation: str

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "confirmation": "AGREED",
                "reason": "Planned rollback after bad release",
            }
        }
    )


class RestoreStartResponse(BaseModel):
    restore: RestoreRecordResponse
    status: str = "PENDING"
    message: str = (
        "Restore queued. A pre-restore backup was taken first to preserve the "
        "recovery point. Poll GET /backups/restores/{id} until result is "
        "SUCCESS or FAILED."
    )
    poll_url: str


class RetentionCleanupResponse(BaseModel):
    deleted: int
    kept: int


class BackupConfigResponse(BaseModel):
    enabled: bool
    provider: str
    retention_days: int
    rpo_minutes: int
    rto_minutes: int
    keep_at_least: int
    scheduled_enabled: bool
    scheduled_cron: str
    encryption_enabled: bool
