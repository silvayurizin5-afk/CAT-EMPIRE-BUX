"""Add public terms panel customization.

Revision ID: 0017_terms_public_panel
Revises: 0016_user_economy
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_terms_public_panel"
down_revision: str | None = "0016_user_economy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "terms_documents",
        sa.Column("summary", sa.String(length=100), nullable=False, server_default=""),
    )
    op.add_column("terms_documents", sa.Column("image_url", sa.Text(), nullable=True))
    op.add_column("terms_documents", sa.Column("link_url", sa.Text(), nullable=True))

    op.add_column(
        "guild_configs",
        sa.Column(
            "terms_panel_title",
            sa.String(length=160),
            nullable=False,
            server_default="Termos e Políticas da NEXTBUY",
        ),
    )
    op.add_column(
        "guild_configs",
        sa.Column(
            "terms_panel_description",
            sa.Text(),
            nullable=False,
            server_default=(
                "Antes de realizar qualquer compra, leia atentamente nossos termos "
                "para entender entregas, garantias, reembolsos, suporte e responsabilidades."
            ),
        ),
    )
    op.add_column(
        "guild_configs",
        sa.Column(
            "terms_panel_emoji",
            sa.String(length=128),
            nullable=False,
            server_default="📜",
        ),
    )
    op.add_column("guild_configs", sa.Column("terms_panel_banner_url", sa.Text(), nullable=True))
    op.add_column(
        "guild_configs",
        sa.Column("terms_panel_thumbnail_url", sa.Text(), nullable=True),
    )
    op.add_column("guild_configs", sa.Column("terms_channel_id", sa.BigInteger(), nullable=True))
    op.add_column("guild_configs", sa.Column("terms_message_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("guild_configs", "terms_message_id")
    op.drop_column("guild_configs", "terms_channel_id")
    op.drop_column("guild_configs", "terms_panel_thumbnail_url")
    op.drop_column("guild_configs", "terms_panel_banner_url")
    op.drop_column("guild_configs", "terms_panel_emoji")
    op.drop_column("guild_configs", "terms_panel_description")
    op.drop_column("guild_configs", "terms_panel_title")

    op.drop_column("terms_documents", "link_url")
    op.drop_column("terms_documents", "image_url")
    op.drop_column("terms_documents", "summary")
