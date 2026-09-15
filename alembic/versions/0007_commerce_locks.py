"""add commerce locks for payment incidents

Revision ID: 0007_commerce_locks
Revises: 0006_audit_delivery_outbox
Create Date: 2026-09-15
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0007_commerce_locks"
down_revision: str | None = "0006_audit_delivery_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "commerce_locks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_topup_id",
            sa.Uuid(),
            sa.ForeignKey("credit_topups.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider_status", sa.String(length=32), nullable=True),
        sa.Column("provider_status_detail", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "locked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_discord_id", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("guild_id", "user_id", name="uq_commerce_lock_guild_user"),
    )
    op.create_index("ix_commerce_locks_guild_id", "commerce_locks", ["guild_id"])
    op.create_index("ix_commerce_locks_user_id", "commerce_locks", ["user_id"])
    op.create_index(
        "ix_commerce_locks_guild_active",
        "commerce_locks",
        ["guild_id", "active"],
    )


def downgrade() -> None:
    op.drop_index("ix_commerce_locks_guild_active", table_name="commerce_locks")
    op.drop_index("ix_commerce_locks_user_id", table_name="commerce_locks")
    op.drop_index("ix_commerce_locks_guild_id", table_name="commerce_locks")
    op.drop_table("commerce_locks")
