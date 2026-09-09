"""add student_imports audit table

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "student_imports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "imported_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("preview_token", sa.String(length=64), nullable=False),
        sa.Column("allow_partial", sa.Boolean(), nullable=False),
        sa.Column("total_records", sa.Integer(), nullable=False),
        sa.Column("imported_records", sa.Integer(), nullable=False),
        sa.Column("invalid_records", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_student_imports_id", "student_imports", ["id"])
    op.create_index(
        "ix_student_imports_imported_by", "student_imports", ["imported_by"]
    )


def downgrade() -> None:
    op.drop_index("ix_student_imports_imported_by", table_name="student_imports")
    op.drop_index("ix_student_imports_id", table_name="student_imports")
    op.drop_table("student_imports")