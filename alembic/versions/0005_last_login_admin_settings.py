"""add users.last_login_at and admin_settings (database foundation)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-07
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Track the last successful login time on each user (auth support).
    op.add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Admin settings key/value store (non-secret administrative config).
    op.create_table(
        "admin_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("key", name="uq_admin_settings_key"),
    )
    op.create_index("ix_admin_settings_id", "admin_settings", ["id"])
    op.create_index("ix_admin_settings_key", "admin_settings", ["key"])


def downgrade() -> None:
    op.drop_index("ix_admin_settings_key", table_name="admin_settings")
    op.drop_index("ix_admin_settings_id", table_name="admin_settings")
    op.drop_table("admin_settings")
    op.drop_column("users", "last_login_at")
