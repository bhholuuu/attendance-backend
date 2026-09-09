"""V2.7 database backup + restore metadata

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-08

V2.7 changes:
  - Create the ``backup_metadata`` table recording every backup job's lifecycle
    (type, status QUEUED/IN_PROGRESS/COMPLETED/FAILED, verification status,
    checksum, size, storage reference, retention).
  - Create the append-only ``restore_records`` table logging every restore
    operation (source backup, pre-restore emergency backup, result).
  - Indexes keep the backup dashboard and retention cleanup responsive.
The existing schema is untouched (no data-affecting statements), so this
migration is a strict superset of the previous state.
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backup_metadata",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("backup_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="QUEUED"),
        sa.Column(
            "verification_status",
            sa.String(length=32),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("migration_version", sa.String(length=64), nullable=True),
        sa.Column("storage_provider", sa.String(length=50), nullable=False),
        sa.Column("storage_ref", sa.String(length=512), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("checksum", sa.String(length=128), nullable=True),
        sa.Column("encrypted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("ix_backup_metadata_status", "backup_metadata", ["status"])
    op.create_index("ix_backup_metadata_type", "backup_metadata", ["backup_type"])
    op.create_index(
        "ix_backup_metadata_verification_status",
        "backup_metadata",
        ["verification_status"],
    )
    op.create_index("ix_backup_metadata_created_at", "backup_metadata", ["created_at"])
    op.create_index(
        "ix_backup_metadata_created_by_id", "backup_metadata", ["created_by_id"]
    )

    op.create_table(
        "restore_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "backup_id",
            sa.Integer(),
            sa.ForeignKey("backup_metadata.id"),
            nullable=False,
        ),
        sa.Column("source_migration_version", sa.String(length=64), nullable=True),
        sa.Column("pre_restore_backup_id", sa.Integer(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )

    op.create_index("ix_restore_records_backup_id", "restore_records", ["backup_id"])
    op.create_index("ix_restore_records_result", "restore_records", ["result"])
    op.create_index("ix_restore_records_started_at", "restore_records", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_restore_records_started_at", "restore_records")
    op.drop_index("ix_restore_records_result", "restore_records")
    op.drop_index("ix_restore_records_backup_id", "restore_records")
    op.drop_table("restore_records")

    op.drop_index("ix_backup_metadata_created_by_id", "backup_metadata")
    op.drop_index("ix_backup_metadata_created_at", "backup_metadata")
    op.drop_index("ix_backup_metadata_verification_status", "backup_metadata")
    op.drop_index("ix_backup_metadata_type", "backup_metadata")
    op.drop_index("ix_backup_metadata_status", "backup_metadata")
    op.drop_table("backup_metadata")
