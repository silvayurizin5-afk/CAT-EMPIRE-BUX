"""Remove redundant ai_configs unique constraint drift.

Revision ID: 0020_remove_ai_config_constraint
Revises: 0019_game_catalog_panel
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020_remove_ai_config_constraint"
down_revision: str | None = "0019_game_catalog_panel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Canonical uniqueness remains enforced by ix_ai_configs_guild_id.
    op.execute(
        "ALTER TABLE ai_configs "
        "DROP CONSTRAINT IF EXISTS uq_ai_configs_guild_id"
    )


def downgrade() -> None:
    # The removed constraint was out-of-band schema drift, not canonical schema.
    pass