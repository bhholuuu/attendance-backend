"""add teacher_assignments table (V2.1)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teacher_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "teacher_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "class_id", sa.Integer(), sa.ForeignKey("classes.id"), nullable=False
        ),
        sa.Column(
            "division_id",
            sa.Integer(),
            sa.ForeignKey("divisions.id"),
            nullable=True,
        ),
        sa.Column(
            "created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "teacher_id",
            "class_id",
            "division_id",
            name="uq_teacher_assignment_triple",
        ),
    )
    op.create_index("ix_teacher_assignments_id", "teacher_assignments", ["id"])
    op.create_index(
        "ix_teacher_assignments_teacher_id",
        "teacher_assignments",
        ["teacher_id"],
    )
    op.create_index(
        "ix_teacher_assignments_class_id", "teacher_assignments", ["class_id"]
    )
    op.create_index(
        "ix_teacher_assignments_division_id",
        "teacher_assignments",
        ["division_id"],
    )
    op.create_index(
        "ix_teacher_assignments_created_by",
        "teacher_assignments",
        ["created_by"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_teacher_assignments_created_by", table_name="teacher_assignments"
    )
    op.drop_index(
        "ix_teacher_assignments_division_id", table_name="teacher_assignments"
    )
    op.drop_index(
        "ix_teacher_assignments_class_id", table_name="teacher_assignments"
    )
    op.drop_index(
        "ix_teacher_assignments_teacher_id", table_name="teacher_assignments"
    )
    op.drop_index("ix_teacher_assignments_id", table_name="teacher_assignments")
    op.drop_table("teacher_assignments")
