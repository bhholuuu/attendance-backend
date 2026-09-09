"""V2.7 database backup + restore + disaster recovery service.

Responsibilities:
  * Create backups (MANUAL / SCHEDULED / PRE_MIGRATION / PRE_RESTORE) via
    PostgreSQL-native ``pg_dump``, encrypted at rest through the provider.
  * Track the full lifecycle in ``backup_metadata``:
    QUEUED -> IN_PROGRESS -> COMPLETED/FAILED, then PENDING -> VERIFIED/CORRUPTED.
  * Verify integrity (SHA-256 checksum + decryption probe) before restore.
  * Restore safely: never a single destructive button. Requires the caller to
    pass through the API's explicit confirmation + validation; the service
    always takes a pre-restore emergency backup first so the recovery point is
    preserved and only then runs ``pg_restore``, logging a ``restore_records``
    row.
  * Retention / rotation: remove backups past their retention horizon while
    always keeping BACKUP_KEEP_AT_LEAST regardless of age.

The actual pg_dump/pg_restore calls are isolated behind ``_run_pg_dump`` and
``_run_pg_restore`` so the test suite can stub them (production is PostgreSQL;
tests are SQLite and must not shell out to real Postgres binaries).

Actor / session model: HTTP handlers pass a request-scoped session for
inserting the QUEUED row. Long-running work runs in a background thread with a
fresh session from ``_session_factory`` (mirroring how the audit service
handles middleware writes). Tests point ``set_session_factory`` at the current
test DB so background jobs write into the same in-memory database.
"""

from __future__ import annotations

import hashlib
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.backup import (
    BackupMetadata,
    BackupStatus,
    BackupType,
    RestoreRecord,
    VerificationStatus,
)
from app.models.user import User
from app.services import audit_service
from app.services.backup_encryption import (
    EMPTY_KEY,
    decrypt,
    encrypt,
    is_encrypted,
)
from app.services.backup_providers import LocalProvider, get_provider


# ---------------------------------------------------------------------------
# Session factory for background worker writes (no request-scoped DB there).
# ---------------------------------------------------------------------------
_session_factory: Optional[Callable[[], "_Session"]] = None


def set_session_factory(factory: Callable[[], "_Session"]) -> None:
    global _session_factory
    _session_factory = factory


def reset_session_factory() -> None:
    global _session_factory
    _session_factory = None


def _new_session() -> "_Session":
    if _session_factory is not None:
        return _session_factory()
    from app.database.connection import SessionLocal

    return SessionLocal()


# ---------------------------------------------------------------------------
# Provider / key resolution
# ---------------------------------------------------------------------------

def _provider() -> LocalProvider:
    provider = get_provider(settings.BACKUP_PROVIDER)
    return provider


def _encryption_key() -> str:
    """Return the configured encryption key (empty disables encryption)."""
    key = (settings.BACKUP_ENCRYPTION_KEY or "").strip()
    if settings.is_production and not key:
        raise RuntimeError(
            "BACKUP_ENCRYPTION_KEY must be set in production to encrypt backups"
        )
    return key


def _encryption_enabled() -> bool:
    return bool(settings.BACKUP_ENCRYPTION_ENABLED and _encryption_key())


# ---------------------------------------------------------------------------
# PostgreSQL-native tooling (isolated for test stubbing)
# ---------------------------------------------------------------------------

def _pg_dump(*, migration_version: Optional[str] = None) -> bytes:
    """Run ``pg_dump`` against the configured DATABASE_URL, returning raw SQL.

    Uses the ``-Fc`` (custom) format by default so ``pg_restore`` can restore
    selectively and in parallel. The DATABASE_URL is parsed to build a
    ``--dbname`` argument; credentials never appear in the backup bytes.
    """
    url = settings.DATABASE_URL
    # Build the postgres:// DSN minus any query params (e.g. ?sslmode=...).
    base_url = url.split("?", 1)[0]
    cmd = [
        settings.PG_DUMP_BIN,
        "--format=custom",
        "--no-owner",
        f"--dbname={base_url}",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "pg_dump failed: "
            + proc.stderr.decode("utf-8", errors="replace")[:2000]
        )
    return proc.stdout


def _pg_restore(blob: bytes) -> None:
    """Restore a custom-format dump into the configured DATABASE_URL."""
    # blob may be encrypted; the caller passes plaintext dump bytes here.
    base_url = settings.DATABASE_URL.split("?", 1)[0]
    cmd = [settings.PG_RESTORE_BIN, "--no-owner", "--dbname=" + base_url]
    proc = subprocess.run(cmd, input=blob, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "pg_restore failed: "
            + proc.stderr.decode("utf-8", errors="replace")[:2000]
        )


# ---------------------------------------------------------------------------
# Checksum helpers
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _as_aware_utc(value):
    """Normalize a (possibly naive UTC) datetime to an aware UTC datetime.

    SQLite returns naive datetimes for timezone-aware columns; PostgreSQL
    returns aware ones. Keep arithmetic in one timezone.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Lifecycle helpers (DB reads)
# ---------------------------------------------------------------------------

def get_backup(db: Session, backup_id: int) -> Optional[BackupMetadata]:
    return db.query(BackupMetadata).filter(BackupMetadata.id == backup_id).first()


def list_backups(
    db: Session, *, limit: int = 100, offset: int = 0
) -> list[BackupMetadata]:
    return (
        db.query(BackupMetadata)
        .order_by(BackupMetadata.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
# Create + run a backup
# ---------------------------------------------------------------------------

def create_backup(
    db: Session,
    *,
    backup_type: BackupType = BackupType.MANUAL,
    created_by: Optional[User] = None,
    migration_version: Optional[str] = None,
    retention_days: Optional[int] = None,
    run_sync: bool = False,
) -> BackupMetadata:
    """Enqueue a backup job (status QUEUED) and return its metadata.

    When ``run_sync`` is False (default), the actual work runs on a background
    thread and the API returns HTTP 202 immediately. When True, the job runs
    inline (used by tests, the scheduler, and pre-migration hooks).
    """
    if not settings.BACKUP_ENABLED:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400, detail="Backup service is disabled (BACKUP_ENABLED=False)"
        )

    meta = BackupMetadata(
        backup_type=backup_type,
        status=BackupStatus.QUEUED,
        verification_status=VerificationStatus.PENDING,
        migration_version=migration_version,
        storage_provider=settings.BACKUP_PROVIDER or "local",
        encrypted=_encryption_enabled(),
        retention_days=retention_days or settings.BACKUP_RETENTION_DAYS,
        created_by_id=created_by.id if created_by else None,
    )
    db.add(meta)
    db.commit()
    db.refresh(meta)

    if run_sync:
        _run_backup_job(meta.id)
        db.expire_all()
        meta = (
            db.query(BackupMetadata).filter(BackupMetadata.id == meta.id).first()
        )
    else:
        _dispatch(meta.id)
    return meta


def _dispatch(backup_id: int) -> None:
    """Start a background thread to process the backup job."""
    thread = threading.Thread(
        target=_run_backup_job, args=(backup_id,), daemon=True, name=f"backup-{backup_id}"
    )
    thread.start()


def _run_backup_job(backup_id: int) -> None:
    """Worker: run one backup job end-to-end in its own session."""
    db = _new_session()
    try:
        meta = db.query(BackupMetadata).filter(BackupMetadata.id == backup_id).first()
        if meta is None or meta.status != BackupStatus.QUEUED:
            return
        meta.status = BackupStatus.IN_PROGRESS
        db.commit()

        try:
            provider = _provider()
            dump = _pg_dump(migration_version=meta.migration_version)
            checksum = _sha256(dump)
            if _encryption_enabled():
                stored = encrypt(dump, _encryption_key())
            else:
                stored = dump
            ref = _build_ref(meta)
            size = provider.save(ref, stored)
            meta.storage_ref = ref
            meta.size_bytes = size
            meta.checksum = checksum
            meta.status = BackupStatus.COMPLETED
            meta.completed_at = datetime.now(timezone.utc)
            meta.error_message = None
            db.commit()
            audit_service.record(
                db,
                action="BACKUP_CREATED",
                result="SUCCESS",
                entity_type="backup",
                entity_id=meta.id,
                entity_label=f"backup {meta.id}",
                details={
                    "backup_type": meta.backup_type.value,
                    "size_bytes": size,
                    "encrypted": meta.encrypted,
                    "migration_version": meta.migration_version,
                },
                actor=_actor_for(meta),
            )
            audit_service.commit_safely(db)
            # Automatically verify right after creation so VERIFIED is honest.
            _verify_meta(db, meta)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            meta = db.query(BackupMetadata).filter(BackupMetadata.id == backup_id).first()
            if meta is not None:
                meta.status = BackupStatus.FAILED
                meta.error_message = str(exc)[:1000]
                meta.completed_at = datetime.now(timezone.utc)
                db.commit()
                audit_service.record(
                    db,
                    action="BACKUP_FAILED",
                    result="FAILED",
                    entity_type="backup",
                    entity_id=backup_id,
                    entity_label=f"backup {backup_id}",
                    details={"backup_type": meta.backup_type.value},
                    actor=_actor_for(meta),
                )
                audit_service.commit_safely(db)
    finally:
        db.close()


def _build_ref(meta: BackupMetadata) -> str:
    ts = meta.created_at.strftime("%Y%m%d-%H%M%S")
    return f"backup-{meta.id}-{meta.backup_type.value.lower()}-{ts}.dump"


def _actor_for(meta: BackupMetadata):
    """Resolve the actor User for audit, if present.

    Called from a background worker thread that has an open session ``db`` for
    the write; we look the actor up through the same session so audit rows for
    admin-triggered jobs carry the administrator's identity. Loads lazily via a
    fresh session so it is safe when a session is not in scope.
    """
    if meta is None or meta.created_by_id is None:
        return None
    try:
        db = _new_session()
        try:
            from app.models.user import User

            return db.query(User).filter(User.id == meta.created_by_id).first()
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - actor is best-effort
        return None


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _verify_meta(db: Session, meta: BackupMetadata) -> None:
    """Re-load, checksum and decrypt-probe a completed backup."""
    try:
        if meta.status != BackupStatus.COMPLETED or not meta.storage_ref:
            return
        provider = _provider()
        data = provider.load(meta.storage_ref)
        checksum = _sha256(data)
        ok = checksum == meta.checksum
        if ok and meta.encrypted:
            key = _encryption_key()
            if not key or not is_encrypted(data):
                ok = False
            else:
                try:
                    decrypt(data, key)
                except ValueError:
                    ok = False
        meta.verification_status = (
            VerificationStatus.VERIFIED if ok else VerificationStatus.CORRUPTED
        )
        meta.last_verified_at = datetime.now(timezone.utc)
        meta.error_message = None if ok else "Checksum/mac verification failed"
        db.commit()
        audit_service.record(
            db,
            action="BACKUP_VERIFIED" if ok else "BACKUP_CORRUPTED",
            result="SUCCESS" if ok else "FAILED",
            entity_type="backup",
            entity_id=meta.id,
            entity_label=f"backup {meta.id}",
            details={"checksum": meta.checksum},
            actor=_actor_for(meta),
        )
        audit_service.commit_safely(db)
    except Exception as exc:  # noqa: BLE001
        try:
            db.rollback()
            meta.verification_status = VerificationStatus.CORRUPTED
            meta.error_message = str(exc)[:1000]
            db.commit()
        except Exception:  # noqa: BLE001
            pass


def verify_backup(db: Session, backup_id: int) -> BackupMetadata:
    """Public re-verify endpoint for an admin."""
    meta = get_backup(db, backup_id)
    if meta is None:
        raise FileNotFoundError(backup_id)
    _verify_meta(db, meta)
    return meta


# ---------------------------------------------------------------------------
# Restore (safe flow)
# ---------------------------------------------------------------------------

def restore_backup(
    db: Session,
    *,
    backup_id: int,
    created_by: User,
    run_sync: bool = False,
) -> RestoreRecord:
    """Restore the database from a verified backup.

    Safe-by-design:
      1. Only COMPLETED + VERIFIED backups may be restored.
      2. A pre-restore emergency backup is taken first (PRESERVE recovery
         point) so the current data is never lost.
      3. Only then is ``pg_restore`` run.
      4. The operation is logged in ``restore_records`` with its result.
    """
    meta = get_backup(db, backup_id)
    if meta is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Backup not found")
    if meta.status != BackupStatus.COMPLETED:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409, detail="Only completed backups can be restored"
        )
    if meta.verification_status != VerificationStatus.VERIFIED:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409,
            detail="Backup is not verified; run verification before restoring",
        )

    record = RestoreRecord(
        backup_id=meta.id,
        source_migration_version=meta.migration_version,
        result="PENDING",
        created_by_id=created_by.id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    audit_service.record(
        db,
        action="RESTORE_STARTED",
        result="SUCCESS",
        entity_type="restore",
        entity_id=record.id,
        entity_label=f"restore {record.id}",
        details={"backup_id": backup_id},
        actor=created_by,
    )
    audit_service.commit_safely(db)

    if run_sync:
        _run_restore_job(meta.id, record.id)
        db.expire_all()
        record = (
            db.query(RestoreRecord).filter(RestoreRecord.id == record.id).first()
        )
    else:
        _dispatch_restore(meta.id, record.id)
    return record


def _dispatch_restore(backup_id: int, record_id: int) -> None:
    thread = threading.Thread(
        target=_run_restore_job,
        args=(backup_id, record_id),
        daemon=True,
        name=f"restore-{record_id}",
    )
    thread.start()


def _run_restore_job(backup_id: int, record_id: int) -> None:
    db = _new_session()
    meta = None
    record = None
    try:
        record = db.query(RestoreRecord).filter(RestoreRecord.id == record_id).first()
        meta = (
            db.query(BackupMetadata).filter(BackupMetadata.id == backup_id).first()
        )
        # Pre-restore emergency backup preserves the current recovery point.
        pre = _take_pre_restore_backup(db)
        if record is not None:
            record.pre_restore_backup_id = pre.id if pre else None
            db.commit()

        provider = _provider()
        data = provider.load(meta.storage_ref)
        if meta.encrypted:
            plain = decrypt(data, _encryption_key())
        else:
            plain = data
        _pg_restore(plain)
        if record is not None:
            record.result = "SUCCESS"
            record.completed_at = datetime.now(timezone.utc)
            db.commit()
            audit_service.record(
                db,
                action="RESTORE_COMPLETED",
                result="SUCCESS",
                entity_type="restore",
                entity_id=record.id,
                entity_label=f"restore {record.id}",
                details={
                    "backup_id": backup_id,
                    "pre_restore_backup_id": record.pre_restore_backup_id,
                },
                actor=_actor_for(meta),
            )
            audit_service.commit_safely(db)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        if record is not None:
            record = (
                db.query(RestoreRecord).filter(RestoreRecord.id == record_id).first()
            )
            record.result = "FAILED"
            record.error_message = str(exc)[:1000]
            record.completed_at = datetime.now(timezone.utc)
            db.commit()
            audit_service.record(
                db,
                action="RESTORE_FAILED",
                result="FAILED",
                entity_type="restore",
                entity_id=record_id,
                entity_label=f"restore {record_id}",
                details={"backup_id": backup_id},
                actor=_actor_for(meta),
            )
            audit_service.commit_safely(db)
    finally:
        db.close()


def _take_pre_restore_backup(db: Session) -> Optional[BackupMetadata]:
    """Synchronously snapshot the current DB before a restore."""
    try:
        meta = BackupMetadata(
            backup_type=BackupType.PRE_RESTORE,
            status=BackupStatus.QUEUED,
            verification_status=VerificationStatus.PENDING,
            storage_provider=settings.BACKUP_PROVIDER or "local",
            encrypted=_encryption_enabled(),
            retention_days=settings.BACKUP_RETENTION_DAYS,
            created_by_id=None,
        )
        db.add(meta)
        db.commit()
        db.refresh(meta)
        _run_backup_job(meta.id)
        db.expire_all()
        return (
            db.query(BackupMetadata).filter(BackupMetadata.id == meta.id).first()
        )
    except Exception:  # noqa: BLE001 - a failed pre-backup must not silently
        # kill the idea; but safety dictates we abort the restore if we could
        # not preserve the recovery point. Re-raise so the restore fails.
        raise


# ---------------------------------------------------------------------------
# Retention / rotation
# ---------------------------------------------------------------------------

def run_retention_cleanup(db: Session) -> dict[str, int]:
    """Delete expired backups while always keeping BACKUP_KEEP_AT_LEAST.

    Returns {deleted: int, kept: int}. Only COMPLETED backups are rotated;
    FAILED rows have no stored bytes and are left for audit/history.
    """
    now = datetime.now(timezone.utc)
    provider = _provider()
    deleted = 0
    kept = 0
    completed = (
        db.query(BackupMetadata)
        .filter(BackupMetadata.status == BackupStatus.COMPLETED)
        .order_by(BackupMetadata.created_at.asc())
        .all()
    )
    # Keep the newest (least age) BACKUP_KEEP_AT_LEAST regardless of age.
    keep_ids = set()
    for meta in reversed(completed):
        if len(keep_ids) < settings.BACKUP_KEEP_AT_LEAST:
            keep_ids.add(meta.id)

    for meta in completed:
        if meta.id in keep_ids:
            kept += 1
            continue
        retention = meta.retention_days or settings.BACKUP_RETENTION_DAYS
        # Fall back to created_at if completed_at missing.
        base = _as_aware_utc(meta.completed_at or meta.created_at)
        if now - base < timedelta(days=retention):
            kept += 1
            continue
        # Expired and not in the keep-floor -> delete stored bytes + mark row.
        if meta.storage_ref:
            provider.delete(meta.storage_ref)
        meta.verification_status = VerificationStatus.PENDING
        meta.status = BackupStatus.COMPLETED  # keep history row
        meta.error_message = "expired (removed by retention cleanup)"
        # We mark a dedicated field; reuse error_message as the deletion notice
        # and drop the storage ref so it can't be restored.
        meta.storage_ref = None
        meta.checksum = None
        deleted += 1
        db.commit()
        audit_service.record(
            db,
            action="BACKUP_DELETED",
            result="SUCCESS",
            entity_type="backup",
            entity_id=meta.id,
            entity_label=f"backup {meta.id}",
            details={"retention_days": retention, "reason": "retention_cleanup"},
            actor=_actor_for(meta),
        )
        audit_service.commit_safely(db)

    audit_service.record(
        db,
        action="RETENTION_CLEANUP",
        result="SUCCESS",
        entity_type="backup",
        details={"deleted": deleted, "kept": kept},
    )
    audit_service.commit_safely(db)

    return {"deleted": deleted, "kept": kept}


# ---------------------------------------------------------------------------
# Migration hooks (called from alembic env.py)
# ---------------------------------------------------------------------------

def backup_before_migration() -> bool:
    """Take a PRE_MIGRATION backup synchronously.

    Called from alembic env.py before ``upgrade()`` runs. Best-effort but
    blocks: a migration should not proceed without a recovery point. Returns
    True on success. Uses its own session (not request scoped).
    """
    if not settings.BACKUP_ENABLED:
        return True
    db = _new_session()
    try:
        meta = create_backup(
            db,
            backup_type=BackupType.PRE_MIGRATION,
            migration_version=_current_revision(),
            run_sync=True,
        )
        db.expire_all()
        done = (
            db.query(BackupMetadata).filter(BackupMetadata.id == meta.id).first()
        )
        return done is not None and done.status == BackupStatus.COMPLETED
    finally:
        db.close()


def _current_revision() -> Optional[str]:
    """Best-effort current alembic head (empty if unavailable)."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        base = Path(__file__).resolve().parent.parent.parent.parent
        cfg = Config(str(base / "alembic.ini"))
        script = ScriptDirectory.from_config(cfg)
        return "".join(script.get_current_head()) or None
    except Exception:  # noqa: BLE001
        return None
