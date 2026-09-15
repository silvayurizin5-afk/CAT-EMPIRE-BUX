"""remove redundant ticket settings unique constraint

Revision ID: 0005_ticket_unique_cleanup
Revises: 0004_ticket_settings
Create Date: 2026-09-15
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0005_ticket_unique_cleanup"
down_revision: str | None = "0004_ticket_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_ticket_settings_guild_id", "ticket_settings", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_ticket_settings_guild_id",
        "ticket_settings",
        ["guild_id"],
    )
