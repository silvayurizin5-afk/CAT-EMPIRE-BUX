"""direct Stripe payments for orders

Revision ID: 0011_order_payments
Revises: 0010_store_game_icons
Create Date: 2026-09-16
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0011_order_payments"
down_revision: str | None = "0010_store_game_icons"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "order_payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id",
            sa.Uuid(),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), server_default="stripe", nullable=False),
        sa.Column("status", sa.String(24), server_default="creating", nullable=False),
        sa.Column("checkout_session_id", sa.String(160), unique=True),
        sa.Column("payment_intent_id", sa.String(160), unique=True),
        sa.Column("checkout_url", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_order_payments_order_id", "order_payments", ["order_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_order_payments_order_id", table_name="order_payments")
    op.drop_table("order_payments")
