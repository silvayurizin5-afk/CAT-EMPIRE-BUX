"""add persistent audit delivery outbox

Revision ID: 0006_audit_delivery_outbox
Revises: 0005_ticket_unique_cleanup
Create Date: 2026-09-15
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0006_audit_delivery_outbox"
down_revision: str | None = "0005_ticket_unique_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "audit_log_id",
            sa.Integer(),
            sa.ForeignKey("audit_logs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_audit_deliveries_audit_log_id",
        "audit_deliveries",
        ["audit_log_id"],
        unique=True,
    )
    op.create_index(
        "ix_audit_deliveries_claimed_at",
        "audit_deliveries",
        ["claimed_at"],
    )
    op.create_index(
        "ix_audit_deliveries_published_at",
        "audit_deliveries",
        ["published_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_deliveries_published_at", table_name="audit_deliveries")
    op.drop_index("ix_audit_deliveries_claimed_at", table_name="audit_deliveries")
    op.drop_index("ix_audit_deliveries_audit_log_id", table_name="audit_deliveries")
    op.drop_table("audit_deliveries")
