"""persist Mercado Pago confirmation notifications

Revision ID: 0003_topup_notifications
Revises: 0002_product_stock
Create Date: 2026-09-15
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0003_topup_notifications"
down_revision: str | None = "0002_product_stock"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "topup_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "topup_id",
            sa.Uuid(),
            sa.ForeignKey("credit_topups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("topup_id", name="uq_topup_notification_topup"),
        sa.CheckConstraint("attempts >= 0", name="ck_topup_notification_attempts_non_negative"),
    )
    op.create_index(
        "ix_topup_notifications_pending",
        "topup_notifications",
        ["sent_at", "claimed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_topup_notifications_pending", table_name="topup_notifications")
    op.drop_table("topup_notifications")
