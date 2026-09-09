"""add academic_years, calendar_events and student_leaves (V2.3)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-07
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "academic_years",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_academic_year_name"),
    )
    op.create_index("ix_academic_years_id", "academic_years", ["id"])
    op.create_index("ix_academic_years_start_date", "academic_years", ["start_date"])

    op.create_table(
        "calendar_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "academic_year_id",
            sa.Integer(),
            sa.ForeignKey("academic_years.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "HOLIDAY",
                "SCHOOL_EVENT",
                "ACADEMIC_DAY",
                "OTHER",
                name="calendar_event_type",
            ),
            nullable=False,
        ),
        sa.Column(
            "holiday_type",
            sa.Enum(
                "PUBLIC_HOLIDAY",
                "SCHOOL_HOLIDAY",
                "VACATION",
                "EXAM_BREAK",
                "EMERGENCY_CLOSURE",
                "OTHER",
                name="holiday_type",
            ),
            nullable=True,
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("is_recurring", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_calendar_events_id", "calendar_events", ["id"])
    op.create_index(
        "ix_calendar_events_academic_year_id",
        "calendar_events",
        ["academic_year_id"],
    )
    op.create_index("ix_calendar_events_start_date", "calendar_events", ["start_date"])
    op.create_index("ix_calendar_events_end_date", "calendar_events", ["end_date"])

    op.create_table(
        "student_leaves",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("leave_type", sa.String(length=100), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column(
            "status",
            sa.Enum("PENDING", "APPROVED", "REJECTED", "CANCELLED",
                    name="student_leave_status"),
            nullable=False,
        ),
        sa.Column(
            "approved_by",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_student_leaves_id", "student_leaves", ["id"])
    op.create_index("ix_student_leaves_student_id", "student_leaves", ["student_id"])
    op.create_index("ix_student_leaves_start_date", "student_leaves", ["start_date"])
    op.create_index("ix_student_leaves_end_date", "student_leaves", ["end_date"])
    op.create_index("ix_student_leaves_status", "student_leaves", ["status"])


def downgrade() -> None:
    op.drop_index("ix_student_leaves_status", table_name="student_leaves")
    op.drop_index("ix_student_leaves_end_date", table_name="student_leaves")
    op.drop_index("ix_student_leaves_start_date", table_name="student_leaves")
    op.drop_index("ix_student_leaves_student_id", table_name="student_leaves")
    op.drop_index("ix_student_leaves_id", table_name="student_leaves")
    op.drop_table("student_leaves")

    op.drop_index("ix_calendar_events_end_date", table_name="calendar_events")
    op.drop_index("ix_calendar_events_start_date", table_name="calendar_events")
    op.drop_index(
        "ix_calendar_events_academic_year_id", table_name="calendar_events"
    )
    op.drop_index("ix_calendar_events_id", table_name="calendar_events")
    op.drop_table("calendar_events")
    # Drop the Postgres enums created for the calendar events table.
    op.execute("DROP TYPE IF EXISTS holiday_type")
    op.execute("DROP TYPE IF EXISTS calendar_event_type")

    op.drop_index("ix_academic_years_start_date", table_name="academic_years")
    op.drop_index("ix_academic_years_id", table_name="academic_years")
    op.drop_table("academic_years")
    op.execute("DROP TYPE IF EXISTS student_leave_status")