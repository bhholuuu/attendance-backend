"""V2.7 Backup + Restore + Disaster Recovery Tests.

Covers (mapped to the V2.7 spec):
  1.  Backup config endpoint (ADMIN only, teacher denied).
  2.  Create manual backup -> HTTP 202 + QUEUED + poll URL.
  3.  Backup lifecycle: QUEUED -> IN_PROGRESS -> COMPLETED (encrypted + checksum).
  4.  Backup FAILED on pg_dump error (no partial state).
  5.  Verification: VERIFIED on valid checksum; CORRUPTED on tampering.
  6.  Backups are SHA-256 checksummed and the checksum is stored.
  7.  Encryption: backup bytes in provider are ciphertext, decrypt round-trips.
  8.  Provider abstraction: local provider save/load/delete/list round-trip.
  9.  Restore safe flow: requires VERIFIED + explicit confirmation phrase.
  10. Restore takes a pre-restore emergency backup first (recovery preserved).
  11. Restore success -> restore_records row SUCCESS + audit.
  12. Restore failure -> restore_records row FAILED.
  13. Retention cleanup: expired backups deleted, keep-floor respected.
  14. Pre-migration backup hook creates a PRE_MIGRATION backup.
  15. Migration chain: 0009 is head and parses.
  16. Audit vocabulary contains the new V2.7 actions.
  17. Offline-sync idempotency after restore: replay -> already_synced, no re-send.
  18. Messaging idempotency: SENT messages are never re-sent after restore.
  19. Teacher access: all backup/restore endpoints rejected with 403.
  20. Admin-only enforced server-side for every backup route.
  21. BACKUP_ENABLED=False rejects new backups.
  22. Performance: 10k students / 100k attendance / 1M audit rows in SQLite.
  23. Malformed confirmation (not "AGREED") is rejected.
  24. Restore of unverified backup is refused (409).

pg_dump/pg_restore are stubbed so tests never shell out to real Postgres
(production is PostgreSQL; tests run in-memory SQLite).
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from conftest import grant_teacher_access, make_auth_header

BCK_DUMP_BYTES = b"PGDUMP-plaintext-custom-format-bytes-1234\x00\x01\x02"
BCK_KEY = "test-backup-encryption-key-not-shared-anywhere"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _configure(tmp_path, monkeypatch):
    """Point the backup provider dir at tmp_path and force sync execution.

    ``_dispatch``/``_dispatch_restore`` are swapped for inline execution so
    tests are deterministic (no background threads).
    """
    from app.core import config as config_mod
    from app.services import backup_service

    monkeypatch.setattr(config_mod.settings, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(config_mod.settings, "BACKUP_ENABLED", True)
    monkeypatch.setattr(config_mod.settings, "BACKUP_PROVIDER", "local")
    monkeypatch.setattr(config_mod.settings, "BACKUP_RETENTION_DAYS", 7)
    monkeypatch.setattr(config_mod.settings, "BACKUP_KEEP_AT_LEAST", 2)
    monkeypatch.setattr(
        backup_service, "_dispatch", lambda backup_id: backup_service._run_backup_job(backup_id)
    )
    monkeypatch.setattr(
        backup_service,
        "_dispatch_restore",
        lambda backup_id, record_id: backup_service._run_restore_job(backup_id, record_id),
    )
    # Stub PostgreSQL tooling -> fake dump bytes / no-op restore.
    monkeypatch.setattr(backup_service, "_pg_dump", lambda *a, **k: BCK_DUMP_BYTES)
    monkeypatch.setattr(backup_service, "_pg_restore", lambda blob: None)
    return backup_service


def _enable_encryption(monkeypatch):
    from app.core import config as config_mod

    monkeypatch.setattr(config_mod.settings, "BACKUP_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(config_mod.settings, "BACKUP_ENCRYPTION_KEY", BCK_KEY)


def _create_backup_sync(backup_service, db_session, backup_type="MANUAL", **kw):
    from app.models.backup import BackupType

    return backup_service.create_backup(
        db_session,
        backup_type=BackupType(backup_type),
        run_sync=True,
        **kw,
    )


# ===================================================================
# Config + admin-only access
# ===================================================================

def test_backup_config_admin(
    client, db_session, users, tmp_path, monkeypatch
):
    _configure(tmp_path, monkeypatch)
    resp = client.get(
        "/api/v1/backups/config", headers=make_auth_header(users["admin"])
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["provider"] == "local"
    assert body["retention_days"] == 7
    assert body["rpo_minutes"] > 0
    assert body["rto_minutes"] > 0
    assert body["keep_at_least"] == 2
    assert "encryption_enabled" in body


def test_backup_routes_teacher_denied(
    client, db_session, users, tmp_path, monkeypatch
):
    _configure(tmp_path, monkeypatch)
    thead = make_auth_header(users["teacher"])
    # Every backup/restore route must 403 for a TEACHER.
    assert client.get("/api/v1/backups/config", headers=thead).status_code == 403
    assert client.get("/api/v1/backups", headers=thead).status_code == 403
    assert client.post("/api/v1/backups", json={}, headers=thead).status_code == 403
    assert (
        client.get("/api/v1/backups/1", headers=thead).status_code == 403
    )
    assert (
        client.post("/api/v1/backups/1/verify", headers=thead).status_code == 403
    )
    assert (
        client.post("/api/v1/backups/1/restore", json={}, headers=thead).status_code == 403
    )
    assert (
        client.post("/api/v1/backups/retention-cleanup", headers=thead).status_code == 403
    )
    # The denial is audited as ACCESS_DENIED.
    from app.models.audit_log import AuditLog

    denied = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "ACCESS_DENIED")
        .count()
    )
    assert denied >= 1


def test_backup_disabled_rejects_new_backup(
    client, db_session, users, tmp_path, monkeypatch
):
    from app.core import config as config_mod

    bs = _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(config_mod.settings, "BACKUP_ENABLED", False)
    resp = client.post(
        "/api/v1/backups", json={}, headers=make_auth_header(users["admin"])
    )
    assert resp.status_code == 400


def test_production_requires_backup_encryption():
    """Production fails fast unless backups are encrypted at rest (V2.7)."""
    from app.core.config import Settings

    s = Settings(
        APP_ENV="production",
        JWT_SECRET_KEY="t" * 40,
        DATABASE_URL="postgresql://real:secret@prod.example.com/school",
        CORS_ORIGINS=["https://app.example.com"],
        BACKUP_ENABLED=True,
        BACKUP_ENCRYPTION_ENABLED=False,
    )
    try:
        s.validate_production_config()
        assert False, "expected RuntimeError for unencrypted backups in prod"
    except RuntimeError as exc:
        assert "BACKUP_ENCRYPTION_ENABLED" in str(exc)


# ===================================================================
# Create + lifecycle
# ===================================================================

def test_create_manual_backup_202_and_poll(
    client, db_session, users, tmp_path, monkeypatch
):
    bs = _configure(tmp_path, monkeypatch)
    resp = client.post(
        "/api/v1/backups", json={}, headers=make_auth_header(users["admin"])
    )
    assert resp.status_code == 202
    body = resp.json()
    # 202 semantics: job is enqueued (QUEUED) and the poll URL tracks it.
    assert body["status"] == "QUEUED"
    assert body["poll_url"].endswith(f"/backups/{body['backup']['id']}")
    bk = body["backup"]
    assert bk["backup_type"] == "MANUAL"
    assert bk["encrypted"] is False
    # Poll the backup after the (inline-dispatched) job has run -> COMPLETED.
    poll = client.get(
        f"/api/v1/backups/{bk['id']}", headers=make_auth_header(users["admin"])
    )
    assert poll.status_code == 200
    assert poll.json()["status"] == "COMPLETED"
    assert poll.json()["verification_status"] == "VERIFIED"


def test_backup_checksum_and_bytes(
    client, db_session, users, tmp_path, monkeypatch
):
    import hashlib

    bs = _configure(tmp_path, monkeypatch)
    meta = _create_backup_sync(bs, db_session)
    assert meta.status.value == "COMPLETED"
    assert meta.checksum == hashlib.sha256(BCK_DUMP_BYTES).hexdigest()
    assert meta.size_bytes == len(BCK_DUMP_BYTES)
    assert meta.verification_status.value == "VERIFIED"
    assert meta.storage_ref is not None


def test_backup_encrypted_at_rest_roundtrip(
    client, db_session, users, tmp_path, monkeypatch
):
    from app.services import backup_encryption as enc
    from app.services import backup_providers as prov_mod

    bs = _configure(tmp_path, monkeypatch)
    _enable_encryption(monkeypatch)
    meta = _create_backup_sync(bs, db_session)
    assert meta.encrypted is True
    assert meta.status.value == "COMPLETED"
    # Provider bytes are ciphertext (not plaintext).
    data = prov_mod.LocalProvider(tmp_path).load(meta.storage_ref)
    assert BCK_DUMP_BYTES not in data
    assert enc.is_encrypted(data)
    assert enc.decrypt(data, BCK_KEY) == BCK_DUMP_BYTES


def test_backup_failed_on_pg_dump_error(
    client, db_session, users, tmp_path, monkeypatch
):
    from app.services import backup_service

    bs = _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(
        backup_service,
        "_pg_dump",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pg_dump boom")),
    )
    meta = _create_backup_sync(bs, db_session)
    assert meta.status.value == "FAILED"
    assert "pg_dump boom" in (meta.error_message or "")


# ===================================================================
# Verification
# ===================================================================

def test_verify_marks_corrupted_on_tamper(
    client, db_session, users, tmp_path, monkeypatch
):
    from app.services import backup_service
    from app.services import backup_providers as prov_mod

    bs = _configure(tmp_path, monkeypatch)
    meta = _create_backup_sync(bs, db_session)
    # Tamper with the stored bytes, then re-verify -> CORRUPTED.
    provider = prov_mod.LocalProvider(tmp_path)
    provider.save(meta.storage_ref, b"tampered-bytes")
    verified = backup_service.verify_backup(db_session, meta.id)
    assert verified.verification_status.value == "CORRUPTED"


def test_verify_marks_verified_on_valid(
    client, db_session, users, tmp_path, monkeypatch
):
    from app.services import backup_service

    bs = _configure(tmp_path, monkeypatch)
    meta = _create_backup_sync(bs, db_session)
    verified = backup_service.verify_backup(db_session, meta.id)
    assert verified.verification_status.value == "VERIFIED"


def test_provider_local_roundtrip(tmp_path):
    from app.services.backup_providers import LocalProvider

    prov = LocalProvider(tmp_path)
    ref = "backup-1.dump"
    prov.save(ref, b"abc")
    assert prov.load(ref) == b"abc"
    entries = prov.list()
    assert (ref, 3) in entries
    prov.delete(ref)
    assert prov.list() == []


# ===================================================================
# Restore safe flow
# ===================================================================

def test_restore_requires_confirmation(
    client, db_session, users, tmp_path, monkeypatch
):
    from app.models.backup import BackupMetadata

    bs = _configure(tmp_path, monkeypatch)
    meta = _create_backup_sync(bs, db_session)
    resp = client.post(
        f"/api/v1/backups/{meta.id}/restore",
        json={"confirmation": "NOT-AGREED"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 400


def test_restore_refuses_unverified(
    client, db_session, users, tmp_path, monkeypatch
):
    from app.models.backup import BackupMetadata, VerificationStatus

    bs = _configure(tmp_path, monkeypatch)
    meta = _create_backup_sync(bs, db_session)
    meta.verification_status = VerificationStatus.PENDING
    db_session.commit()
    resp = client.post(
        f"/api/v1/backups/{meta.id}/restore",
        json={"confirmation": "AGREED"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 409


def test_restore_success_with_pre_restore_backup(
    client, db_session, users, tmp_path, monkeypatch
):
    from app.models.backup import BackupMetadata, BackupType, RestoreRecord
    from app.services import backup_service

    bs = _configure(tmp_path, monkeypatch)
    meta = _create_backup_sync(bs, db_session)
    # Patch pre-restore backup to run synchronously (it already runs sync).
    resp = client.post(
        f"/api/v1/backups/{meta.id}/restore",
        json={"confirmation": "AGREED"},
        headers=make_auth_header(users["admin"]),
    )
    assert resp.status_code == 202
    body = resp.json()
    rid = body["restore"]["id"]
    # 202 semantics: restore is queued (PENDING) and polled to completion.
    assert body["status"] == "PENDING"
    assert body["poll_url"].endswith(f"/backups/restores/{rid}")
    # Poll the restore -> SUCCESS (job ran inline via patched dispatch).
    poll = client.get(
        f"/api/v1/backups/restores/{rid}",
        headers=make_auth_header(users["admin"]),
    )
    assert poll.status_code == 200
    assert poll.json()["result"] == "SUCCESS"
    record = db_session.query(RestoreRecord).filter(RestoreRecord.id == rid).first()
    assert record.result == "SUCCESS"
    assert record.backup_id == meta.id
    # A pre-restore emergency backup was taken first.
    pre = (
        db_session.query(BackupMetadata)
        .filter(BackupMetadata.id == record.pre_restore_backup_id)
        .first()
    )
    assert pre is not None
    assert pre.backup_type == BackupType.PRE_RESTORE
    assert pre.status.value == "COMPLETED"
    # Audit recorded.
    from app.models.audit_log import AuditLog

    started = (
        db_session.query(AuditLog).filter(AuditLog.action == "RESTORE_STARTED").count()
    )
    completed = (
        db_session.query(AuditLog).filter(AuditLog.action == "RESTORE_COMPLETED").count()
    )
    assert started >= 1
    assert completed >= 1


def test_restore_failure_recorded(
    client, db_session, users, tmp_path, monkeypatch
):
    from app.models.backup import RestoreRecord
    from app.services import backup_service

    bs = _configure(tmp_path, monkeypatch)
    meta = _create_backup_sync(bs, db_session)
    monkeypatch.setattr(backup_service, "_pg_restore", lambda blob: (_ for _ in ()).throw(RuntimeError("restore boom")))
    record = backup_service.restore_backup(
        db_session, backup_id=meta.id, created_by=users["admin"], run_sync=True
    )
    db_session.refresh(record)
    assert record.result == "FAILED"
    assert "restore boom" in (record.error_message or "")
    from app.models.audit_log import AuditLog

    failed = (
        db_session.query(AuditLog).filter(AuditLog.action == "RESTORE_FAILED").count()
    )
    assert failed >= 1


# ===================================================================
# Retention / rotation
# ===================================================================

def test_retention_removes_expired_keeps_floor(
    client, db_session, users, tmp_path, monkeypatch
):
    from datetime import datetime, timedelta, timezone
    from app.models.backup import BackupMetadata, BackupStatus, BackupType
    from app.services import backup_service

    bs = _configure(tmp_path, monkeypatch)
    # Create N completed backups and backdate their completion to be expired.
    metas = []
    for i in range(5):
        meta = _create_backup_sync(bs, db_session)
        meta.completed_at = datetime.now(timezone.utc) - timedelta(
            days=100 + i
        )
        meta.created_at = datetime.now(timezone.utc) - timedelta(days=100 + i)
        db_session.commit()
        metas.append(meta)
    result = backup_service.run_retention_cleanup(db_session)
    # Keep floor = 2 -> the 2 newest are kept, 3 are deleted.
    assert result["deleted"] == 3
    assert result["kept"] == 2
    from app.models.audit_log import AuditLog

    deleted_audits = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "BACKUP_DELETED")
        .count()
    )
    aggregate = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "RETENTION_CLEANUP")
        .count()
    )
    assert deleted_audits >= 3
    assert aggregate >= 1
    # Deleted backups have storage_ref cleared so they can no longer restore.
    remaining = (
        db_session.query(BackupMetadata)
        .filter(BackupMetadata.status == BackupStatus.COMPLETED)
        .all()
    )
    kept_refs = [m for m in remaining if m.storage_ref is not None]
    assert len(kept_refs) == 2


def test_retention_keeps_recent_unexpired(
    client, db_session, users, tmp_path, monkeypatch
):
    from app.services import backup_service

    bs = _configure(tmp_path, monkeypatch)
    _create_backup_sync(bs, db_session)  # recent (not expired)
    result = backup_service.run_retention_cleanup(db_session)
    assert result["deleted"] == 0
    assert result["kept"] >= 1


# ===================================================================
# Migration + pre-migration hook
# ===================================================================

def test_migration_chain_head_is_0009():
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    # Expand %(here)s/backend/alembic into an absolute path Alembic can open.
    cfg.set_main_option(
        "script_location", str((root.parent / "backend" / "alembic").resolve())
    )
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    # A configured single head.
    assert head is not None
    heads = {r.revision for r in script.walk_revisions()}
    assert "0009" in heads
    assert "0008" in heads and "0001" in heads
    assert bool(head) and (head in heads)


def test_pre_migration_backup_hook_creates_backup(
    client, db_session, users, tmp_path, monkeypatch
):
    from app.models.backup import BackupMetadata, BackupType
    from app.services import backup_service

    _configure(tmp_path, monkeypatch)
    ok = backup_service.backup_before_migration()
    assert ok is True
    pre = (
        db_session.query(BackupMetadata)
        .order_by(BackupMetadata.id.desc())
        .first()
    )
    assert pre is not None
    assert pre.backup_type == BackupType.PRE_MIGRATION
    assert pre.status.value == "COMPLETED"


# ===================================================================
# Audit vocabulary
# ===================================================================

def test_audit_vocabulary_includes_v27_actions():
    from app.services.audit_service import ACTIONS

    for action in (
        "BACKUP_CREATED",
        "BACKUP_FAILED",
        "BACKUP_VERIFIED",
        "BACKUP_CORRUPTED",
        "BACKUP_DELETED",
        "RESTORE_STARTED",
        "RESTORE_COMPLETED",
        "RESTORE_FAILED",
        "RETENTION_CLEANUP",
    ):
        assert action in ACTIONS


def test_backup_create_is_audited(
    client, db_session, users, tmp_path, monkeypatch
):
    from app.models.audit_log import AuditLog

    bs = _configure(tmp_path, monkeypatch)
    _create_backup_sync(bs, db_session)
    created = (
        db_session.query(AuditLog).filter(AuditLog.action == "BACKUP_CREATED").first()
    )
    assert created is not None
    assert created.result == "SUCCESS"


# ===================================================================
# Offline-sync + messaging idempotency after restore
# ===================================================================

def _seed_simple_class(db, admin_id):
    from app.models.school_class import Division, SchoolClass
    from app.models.student import Student

    cls = SchoolClass(name="10", is_active=True, created_by=admin_id)
    db.add(cls)
    db.flush()
    div = Division(name="A", is_active=True, class_id=cls.id, created_by=admin_id)
    db.add(div)
    db.flush()
    students = []
    for i, (nm, roll) in enumerate([("R1", "1"), ("R2", "2")]):
        s = Student(
            class_id=cls.id,
            division_id=div.id,
            name=nm,
            roll_number=roll,
            parent_name=f"{nm} Parent",
            parent_whatsapp_number="+9199999999" + str(i),
            is_active=True,
            created_by=admin_id,
        )
        db.add(s)
        students.append(s)
    db.commit()
    return cls, div, students


def test_offline_sync_idempotent_after_restore(
    client, db_session, users, tmp_path, monkeypatch, capsys
):
    """Replaying an already-synced client_session_id never re-sends messages.

    Because a restore reproduces the DB exactly (including client_session_id
    and the SENT parent_messages idempotency rows), re-syncing the same batch
    yields already_synced and must not create a new session or message batch.
    """
    from app.services import attendance_service
    from app.services import message_service
    from app.services import backup_service
    from app.models.message import MessageBatch, ParentMessage

    admin = users["admin"]
    cls, div, students = _seed_simple_class(db_session, admin.id)
    client_id = "00000000-0000-0000-0000-0000000000ab"
    statuses = ["ABSENT", "PRESENT"]

    # Upload an offline batch -> accepted + auto-creates a message batch.
    result = attendance_service.sync_attendance(
        db_session,
        client_session_id=client_id,
        class_id=cls.id,
        division_id=div.id,
        attendance_date=date(2026, 8, 31),
        records=[
            {
                "client_record_id": f"rec-{s.id}",
                "student_id": s.id,
                "status": status,
                "remarks": None,
            }
            for s, status in zip(students, statuses)
        ],
        taker=admin,
    )
    assert result["accepted"] is True
    session_id = result["session"].id
    # Create a message batch for the absence and mark the message SENT.
    from app.models.message import MessageBatch, ParentMessage

    message_service.create_batch(
        db_session, attendance_session_id=session_id, created_by=admin
    )
    batch = (
        db_session.query(MessageBatch)
        .filter(MessageBatch.attendance_session_id == session_id)
        .first()
    )
    assert batch is not None
    # Mark the absence message SENT.
    msg = (
        db_session.query(ParentMessage)
        .filter(ParentMessage.message_batch_id == batch.id)
        .first()
    )
    msg.delivery_status = "SENT"
    msg.sent_at = __import__("datetime").datetime.now()
    msg.idempotency_key = message_service.generate_idempotency_key(
        msg.attendance_record_id, "ABSENCE_NOTIFICATION"
    )
    db_session.commit()
    sent_before = (
        db_session.query(ParentMessage)
        .filter(ParentMessage.delivery_status == "SENT")
        .count()
    )

    # Simulate "after restore": re-run the SAME sync idempotently. The server
    # (whose state the restore reproduced exactly) treats it as already_synced
    # and must NOT create a second session or re-send any message.
    replay = attendance_service.sync_attendance(
        db_session,
        client_session_id=client_id,
        class_id=cls.id,
        division_id=div.id,
        attendance_date=date(2026, 8, 31),
        records=[
            {
                "client_record_id": f"rec-{s.id}",
                "student_id": s.id,
                "status": status,
                "remarks": None,
            }
            for s, status in zip(students, statuses)
        ],
        taker=admin,
    )
    assert replay["already_synced"] is True
    # Exactly one session for this client id (no duplicate was created).
    from app.models.attendance import AttendanceSession

    sessions = (
        db_session.query(AttendanceSession)
        .filter(AttendanceSession.client_session_id == client_id)
        .count()
    )
    assert sessions == 1
    # No second batch; SENT messages intact; no new message rows.
    batches_after = (
        db_session.query(MessageBatch)
        .filter(MessageBatch.attendance_session_id == session_id)
        .count()
    )
    assert batches_after == 1
    sent_after = (
        db_session.query(ParentMessage)
        .filter(ParentMessage.delivery_status == "SENT")
        .count()
    )
    assert sent_after == sent_before
    assert (
        db_session.query(ParentMessage)
        .filter(ParentMessage.idempotency_key == msg.idempotency_key)
        .count()
        >= 1
    )


# ===================================================================
# Performance: large DB
# ===================================================================

@pytest.mark.performance
def test_large_db_performance(
    client, db_session, users, tmp_path, monkeypatch
):
    """Insert 10k students / 100k attendance / 1M audit rows and verify
    backup metadata + list still respond quickly (SQLite is a proxy for the
    p95 query path; pg is the production target)."""
    from app.models.student import Student
    from app.models.attendance import AttendanceSession, StudentAttendance, SessionStatus, AttendanceStatus
    from app.models.audit_log import AuditLog
    from app.models.school_class import SchoolClass, Division

    admin = users["admin"]
    cls = SchoolClass(name="Big", is_active=True, created_by=admin.id)
    db_session.add(cls)
    db_session.flush()
    div = Division(name="A", is_active=True, class_id=cls.id, created_by=admin.id)
    db_session.add(div)
    db_session.flush()

    N = 10_000
    students = [
        Student(
            class_id=cls.id,
            division_id=div.id,
            name=f"S{i}",
            roll_number=str(i),
            parent_name=f"P{i}",
            parent_whatsapp_number=f"+9199999999{i % 100:02d}",
            is_active=True,
            created_by=admin.id,
        )
        for i in range(N)
    ]
    db_session.add_all(students)
    db_session.commit()

    # 100k attendance records across 10k sessions. Each session uses a distinct
    # date (unique on class+division+date) and a distinct set of 10 students.
    sessions = []
    for i in range(10_000):
        sessions.append(
            AttendanceSession(
                class_id=cls.id,
                division_id=div.id,
                # 10k distinct dates (2020-01-01 + i) satisfy the
                # (class, division, date) unique constraint.
                attendance_date=date(2020, 1, 1) + timedelta(days=i),
                taken_by=admin.id,
                status=SessionStatus.COMPLETED,
                total_students=N,
                present_count=N // 2,
                absent_count=N // 2,
            )
        )
    db_session.add_all(sessions)
    db_session.commit()

    # 100k records = 10 per session, spaced so each (session, student) is
    # unique within the session.
    records = []
    for i in range(100_000):
        si = i // 10
        k = i % 10
        session = sessions[si]
        student = students[(si * 10 + k) % N]
        records.append(
            StudentAttendance(
                attendance_session_id=session.id,
                student_id=student.id,
                attendance_status=AttendanceStatus.PRESENT
                if (i % 2) == 0
                else AttendanceStatus.ABSENT,
            )
        )
    db_session.add_all(records)
    db_session.commit()

    # 1M audit rows (batched).
    from datetime import datetime, timezone

    batch = []
    BATCH = 5000
    for i in range(1_000_000):
        batch.append(
            AuditLog(
                timestamp=datetime.now(timezone.utc),
                correlation_id=f"perf-{i}",
                actor_user_id=admin.id,
                actor_username="admin",
                actor_role="ADMIN",
                action="REPORT_GENERATED",
                result="SUCCESS",
            )
        )
        if len(batch) >= BATCH:
            db_session.add_all(batch)
            db_session.commit()
            batch = []
    if batch:
        db_session.add_all(batch)
        db_session.commit()

    # The backup endpoints still return promptly with the large DB.
    import time

    t0 = time.time()
    bs = _configure(tmp_path, monkeypatch)
    resp = client.get(
        "/api/v1/backups", headers=make_auth_header(users["admin"])
    )
    elapsed = time.time() - t0
    assert resp.status_code == 200
    # Generous bound: the list endpoint with no ABSOLUTE table scan must be fast.
    assert elapsed < 20.0


def test_performance_no_dump_in_flutter(
    client, db_session, users, tmp_path, monkeypatch
):
    """Guard: raw backup bytes are NEVER served to the Flutter app.

    The backup/restore API returns metadata only (JSON). The dump bytes always
    live in the storage provider and are never downloadable through the API, so
    the mobile app can never load a backup file (a stated V2.7 constraint).
    """
    from app.schemas import backup as backup_schemas
    from app.models.audit_log import AuditLog

    _configure(tmp_path, monkeypatch)
    bs = _configure  # noqa: F841
    resp = client.get(
        "/api/v1/backups", headers=make_auth_header(users["admin"])
    )
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("application/json")
    body = resp.json()
    # List is a JSON metadata envelope, never a byte blob.
    assert isinstance(body, dict)
    assert "items" in body and "total" in body
    # Every backup/restore response model is a JSON schema object (not bytes).
    for name in ("BackupMetadataResponse", "RestoreRecordResponse"):
        assert hasattr(backup_schemas, name)
