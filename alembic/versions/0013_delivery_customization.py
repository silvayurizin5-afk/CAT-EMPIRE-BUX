"""Configurable public delivery presentation.

Revision ID: 0013_delivery_custom
Revises: 0012_ai_store_checkout
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_delivery_custom"
down_revision: str | None = "0012_ai_store_checkout"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "store_panel_configs",
        sa.Column(
            "delivery_config",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("store_panel_configs", "delivery_config")
