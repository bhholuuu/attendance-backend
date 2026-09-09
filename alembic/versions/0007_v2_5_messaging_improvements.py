"""V2.5 messaging improvements

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-08

V2.5 changes:
  - Add message_type, attendance_record_id, idempotency_key, next_retry_at,
    cancelled_at, cancelled_reason to parent_messages.
  - Add skipped_count to message_batches.
  - Add unique constraint on (attendance_record_id, message_type) for
    idempotency (prevents duplicate logical notifications across batches).
  - Add CANCELLED to message_delivery_status enum.
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- parent_messages: new columns ---
    op.add_column(
        "parent_messages",
        sa.Column(
            "message_type",
            sa.String(length=50),
            nullable=False,
            server_default="ABSENCE_NOTIFICATION",
        ),
    )
    op.add_column(
        "parent_messages",
        sa.Column(
            "attendance_record_id",
            sa.Integer(),
            sa.ForeignKey("student_attendance.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "parent_messages",
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "parent_messages",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "parent_messages",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "parent_messages",
        sa.Column("cancelled_reason", sa.String(length=500), nullable=True),
    )

    # --- parent_messages: indexes ---
    op.create_index(
        "ix_parent_messages_message_type",
        "parent_messages",
        ["message_type"],
    )
    op.create_index(
        "ix_parent_messages_idempotency_key",
        "parent_messages",
        ["idempotency_key"],
    )
    op.create_index(
        "ix_parent_messages_attendance_record_id",
        "parent_messages",
        ["attendance_record_id"],
    )

    # --- parent_messages: idempotency unique constraint ---
    # Only enforced when attendance_record_id IS NOT NULL (NULLs are distinct
    # in unique constraints per SQL standard).
    op.create_unique_constraint(
        "uq_parent_message_attendance_type",
        "parent_messages",
        ["attendance_record_id", "message_type"],
        postgresql_where=sa.text("attendance_record_id IS NOT NULL"),
    )

    # --- message_batches: new column ---
    op.add_column(
        "message_batches",
        sa.Column(
            "skipped_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    # --- message_delivery_status: add CANCELLED ---
    # PostgreSQL enum add.
    op.execute(
        "ALTER TYPE message_delivery_status ADD VALUE IF NOT EXISTS 'CANCELLED'"
    )


def downgrade() -> None:
    # Drop columns (order matters for FK dependencies).
    op.drop_constraint(
        "uq_parent_message_attendance_type",
        "parent_messages",
        type_="unique",
    )
    op.drop_index("ix_parent_messages_attendance_record_id", "parent_messages")
    op.drop_index("ix_parent_messages_idempotency_key", "parent_messages")
    op.drop_index("ix_parent_messages_message_type", "parent_messages")
    op.drop_column("parent_messages", "cancelled_reason")
    op.drop_column("parent_messages", "cancelled_at")
    op.drop_column("parent_messages", "next_retry_at")
    op.drop_column("parent_messages", "idempotency_key")
    op.drop_column("parent_messages", "attendance_record_id")
    op.drop_column("parent_messages", "message_type")

    op.drop_column("message_batches", "skipped_count")

    # Note: PostgreSQL does not support removing a value from an enum type.
    # The CANCELLED value remains in the enum but is unused after downgrade.
