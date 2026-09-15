"""add configurable ticket messages

Revision ID: 0004_ticket_settings
Revises: 0003_topup_notifications
Create Date: 2026-09-15
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004_ticket_settings"
down_revision: str | None = "0003_topup_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DEFAULT_TITLE = "Pedido {order}"
DEFAULT_INSTRUCTIONS = (
    "{customer}, seu pedido `{order}` foi confirmado. "
    "A equipe vai continuar o atendimento por aqui."
)


def upgrade() -> None:
    op.create_table(
        "ticket_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "title_template",
            sa.String(length=160),
            server_default=DEFAULT_TITLE,
            nullable=False,
        ),
        sa.Column(
            "instruction_template",
            sa.Text(),
            server_default=DEFAULT_INSTRUCTIONS,
            nullable=False,
        ),
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
        sa.UniqueConstraint("guild_id", name="uq_ticket_settings_guild_id"),
    )
    op.create_index("ix_ticket_settings_guild_id", "ticket_settings", ["guild_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_ticket_settings_guild_id", table_name="ticket_settings")
    op.drop_table("ticket_settings")
