"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("full_name", sa.String(length=100), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("phone_number", sa.String(length=20), nullable=True),
        sa.Column(
            "role",
            sa.Enum("ADMIN", "TEACHER", name="user_role"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "classes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_classes_id", "classes", ["id"])
    op.create_index("ix_classes_name", "classes", ["name"], unique=True)

    op.create_table(
        "divisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("classes.id"), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("class_id", "name", name="uq_division_class_name"),
    )
    op.create_index("ix_divisions_id", "divisions", ["id"])
    op.create_index("ix_divisions_class_id", "divisions", ["class_id"])

    op.create_table(
        "students",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("classes.id"), nullable=False),
        sa.Column("division_id", sa.Integer(), sa.ForeignKey("divisions.id"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("roll_number", sa.String(length=30), nullable=False),
        sa.Column("parent_name", sa.String(length=100), nullable=False),
        sa.Column("parent_whatsapp_number", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_students_id", "students", ["id"])
    op.create_index("ix_students_class_id", "students", ["class_id"])
    op.create_index("ix_students_division_id", "students", ["division_id"])
    op.create_index("ix_students_name", "students", ["name"])
    op.create_index("ix_students_roll_number", "students", ["roll_number"])
    op.create_index(
        "uq_active_student_class_division_roll",
        "students",
        ["class_id", "division_id", "roll_number"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )

    op.create_table(
        "attendance_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("classes.id"), nullable=False),
        sa.Column("division_id", sa.Integer(), sa.ForeignKey("divisions.id"), nullable=False),
        sa.Column("attendance_date", sa.Date(), nullable=False),
        sa.Column("taken_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "COMPLETED", name="attendance_session_status"),
            nullable=False,
        ),
        sa.Column("total_students", sa.Integer(), nullable=False),
        sa.Column("present_count", sa.Integer(), nullable=False),
        sa.Column("absent_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "class_id", "division_id", "attendance_date",
            name="uq_attendance_class_division_date",
        ),
    )
    op.create_index("ix_attendance_sessions_id", "attendance_sessions", ["id"])
    op.create_index("ix_attendance_sessions_class_id", "attendance_sessions", ["class_id"])
    op.create_index("ix_attendance_sessions_division_id", "attendance_sessions", ["division_id"])
    op.create_index("ix_attendance_sessions_attendance_date", "attendance_sessions", ["attendance_date"])
    op.create_index("ix_attendance_sessions_taken_by", "attendance_sessions", ["taken_by"])

    op.create_table(
        "student_attendance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "attendance_session_id",
            sa.Integer(),
            sa.ForeignKey("attendance_sessions.id"),
            nullable=False,
        ),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id"), nullable=False),
        sa.Column(
            "attendance_status",
            sa.Enum("PRESENT", "ABSENT", name="student_attendance_status"),
            nullable=False,
        ),
        sa.Column("remarks", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "attendance_session_id", "student_id",
            name="uq_student_attendance_session_student",
        ),
    )
    op.create_index("ix_student_attendance_id", "student_attendance", ["id"])
    op.create_index(
        "ix_student_attendance_attendance_session_id",
        "student_attendance",
        ["attendance_session_id"],
    )
    op.create_index("ix_student_attendance_student_id", "student_attendance", ["student_id"])

    op.create_table(
        "message_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "attendance_session_id",
            sa.Integer(),
            sa.ForeignKey("attendance_sessions.id"),
            nullable=False,
        ),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("classes.id"), nullable=False),
        sa.Column("division_id", sa.Integer(), sa.ForeignKey("divisions.id"), nullable=False),
        sa.Column("attendance_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "PROCESSING", "COMPLETED", "PARTIAL_FAILED",
                    name="message_batch_status"),
            nullable=False,
        ),
        sa.Column("total_messages", sa.Integer(), nullable=False),
        sa.Column("sent_count", sa.Integer(), nullable=False),
        sa.Column("pending_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "attendance_session_id", name="uq_message_batch_attendance_session"
        ),
    )
    op.create_index("ix_message_batches_id", "message_batches", ["id"])
    op.create_index(
        "ix_message_batches_attendance_session_id",
        "message_batches",
        ["attendance_session_id"],
        unique=True,
    )
    op.create_index("ix_message_batches_class_id", "message_batches", ["class_id"])
    op.create_index("ix_message_batches_division_id", "message_batches", ["division_id"])
    op.create_index("ix_message_batches_attendance_date", "message_batches", ["attendance_date"])
    op.create_index("ix_message_batches_created_by", "message_batches", ["created_by"])

    op.create_table(
        "parent_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_batch_id", sa.Integer(), sa.ForeignKey("message_batches.id"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id"), nullable=False),
        sa.Column("parent_name", sa.String(length=100), nullable=False),
        sa.Column("parent_whatsapp_number", sa.String(length=20), nullable=False),
        sa.Column(
            "attendance_status",
            sa.Enum("PRESENT", "ABSENT", name="message_attendance_status"),
            nullable=False,
        ),
        sa.Column("message_content", sa.Text(), nullable=False),
        sa.Column(
            "delivery_status",
            sa.Enum("PENDING", "PROCESSING", "SENT", "FAILED",
                    "DELIVERED", "READ",
                    name="message_delivery_status"),
            nullable=False,
        ),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("provider_response", sa.Text(), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "message_batch_id", "student_id", name="uq_parent_message_batch_student"
        ),
    )
    op.create_index("ix_parent_messages_id", "parent_messages", ["id"])
    op.create_index("ix_parent_messages_message_batch_id", "parent_messages", ["message_batch_id"])
    op.create_index("ix_parent_messages_student_id", "parent_messages", ["student_id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_refresh_tokens_id", "refresh_tokens", ["id"])
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("refresh_tokens")
    op.drop_table("parent_messages")
    op.drop_table("message_batches")
    op.drop_table("student_attendance")
    op.drop_table("attendance_sessions")
    op.drop_table("students")
    op.drop_table("divisions")
    op.drop_table("classes")
    op.drop_table("users")
