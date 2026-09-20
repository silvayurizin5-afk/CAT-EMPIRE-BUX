"""Add per-guild economy adjustments.

Revision ID: 0016_user_economy
Revises: 0015_support_tickets
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_user_economy"
down_revision: str | None = "0015_support_tickets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_economy_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "spent_adjustment",
            sa.Numeric(14, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "robux_adjustment",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "orders_adjustment",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "guild_id",
            "user_id",
            name="uq_user_economy_adjustments_guild_user",
        ),
    )
    op.create_index(
        "ix_user_economy_adjustments_guild_id",
        "user_economy_adjustments",
        ["guild_id"],
    )
    op.create_index(
        "ix_user_economy_adjustments_user_id",
        "user_economy_adjustments",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_economy_adjustments_user_id",
        table_name="user_economy_adjustments",
    )
    op.drop_index(
        "ix_user_economy_adjustments_guild_id",
        table_name="user_economy_adjustments",
    )
    op.drop_table("user_economy_adjustments")
