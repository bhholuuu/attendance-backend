"""add offline-sync client ids and unique constraints (V2.4)

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-08
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Client-generated idempotency keys for attendance sessions and records.
    # Both are nullable: web-created rows have no client id, and the unique
    # constraints treat NULLs as distinct, so existing rows are preserved.
    op.add_column(
        "attendance_sessions",
        sa.Column("client_session_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "student_attendance",
        sa.Column("client_record_id", sa.String(length=36), nullable=True),
    )

    # Unique constraints at the database level so duplicate offline syncs (from
    # any device, retry, or crash-and-replay) cannot create duplicate rows.
    # PostgreSQL: plain UNIQUE constraint (NULLs are distinct).
    # SQLite: emulated with a unique index.
    op.create_unique_constraint(
        "uq_attendance_client_session_id", "attendance_sessions", ["client_session_id"]
    )
    op.create_unique_constraint(
        "uq_student_attendance_client_record_id",
        "student_attendance",
        ["client_record_id"],
    )
    op.create_index(
        "ix_attendance_sessions_client_session_id",
        "attendance_sessions",
        ["client_session_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_attendance_sessions_client_session_id", table_name="attendance_sessions"
    )
    op.drop_constraint(
        "uq_student_attendance_client_record_id",
        "student_attendance",
        type_="unique",
    )
    op.drop_constraint(
        "uq_attendance_client_session_id", "attendance_sessions", type_="unique"
    )
    op.drop_column("student_attendance", "client_record_id")
    op.drop_column("attendance_sessions", "client_session_id")