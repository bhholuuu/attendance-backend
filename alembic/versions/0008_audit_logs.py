"""V2.6 audit logs

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-08

V2.6 changes:
  - Create the append-only ``audit_logs`` table.
  - Indexes on timestamp, actor_user_id, action, entity_type, entity_id and
    result so the admin audit dashboard stays responsive at large volumes.
  - The existing schema is untouched (no data-affecting statements), so this
    migration is a strict superset of the previous state.
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "correlation_id",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("actor_username", sa.String(length=50), nullable=True),
        sa.Column("actor_role", sa.String(length=20), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column(
            "result",
            sa.String(length=20),
            nullable=False,
            server_default="SUCCESS",
        ),
        sa.Column("entity_type", sa.String(length=50), nullable=True),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("entity_label", sa.String(length=255), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
    )

    op.create_index("ix_audit_logs_timestamp", "audit_logs", ["timestamp"])
    op.create_index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_entity_type", "audit_logs", ["entity_type"])
    op.create_index("ix_audit_logs_entity_id", "audit_logs", ["entity_id"])
    op.create_index("ix_audit_logs_result", "audit_logs", ["result"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_result", "audit_logs")
    op.drop_index("ix_audit_logs_entity_id", "audit_logs")
    op.drop_index("ix_audit_logs_entity_type", "audit_logs")
    op.drop_index("ix_audit_logs_action", "audit_logs")
    op.drop_index("ix_audit_logs_actor_user_id", "audit_logs")
    op.drop_index("ix_audit_logs_timestamp", "audit_logs")
    op.drop_table("audit_logs")