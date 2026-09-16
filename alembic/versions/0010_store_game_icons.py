"""store game icons for calculator embeds

Revision ID: 0010_store_game_icons
Revises: 0009_store_panel_and_coupons
Create Date: 2026-09-16
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0010_store_game_icons"
down_revision: str | None = "0009_store_panel_and_coupons"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "store_panel_configs",
        sa.Column(
            "game_icons",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("store_panel_configs", "game_icons")
