"""configurable game catalog panel

Revision ID: 0019_game_catalog_panel
Revises: 0018_coupon_discount_below_100
Create Date: 2026-10-02
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0019_game_catalog_panel"
down_revision: str | None = "0018_coupon_discount_below_100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_catalog_panel_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(256), server_default=sa.text("'NEXTBUY'"), nullable=False),
        sa.Column("status_text", sa.String(160), server_default=sa.text("'Atendimento on-line'"), nullable=False),
        sa.Column("status_emoji", sa.String(128), server_default=sa.text("'🟢'"), nullable=False),
        sa.Column(
            "description",
            sa.Text(),
            server_default=sa.text(
                "'Robux, Gamepasses, itens e produtos dos seus jogos favoritos em um só lugar.\nClique em **Abrir catálogo** para escolher um jogo e depois o produto.'"
            ),
            nullable=False,
        ),
        sa.Column("color", sa.Integer(), server_default=sa.text("8072383"), nullable=False),
        sa.Column("image_url", sa.Text()),
        sa.Column("thumbnail_url", sa.Text()),
        sa.Column(
            "footer_text",
            sa.String(2048),
            server_default=sa.text("'{count} jogo(s) disponível(is) • NEXTBUY'"),
            nullable=False,
        ),
        sa.Column("open_button_label", sa.String(80), server_default=sa.text("'Abrir catálogo'"), nullable=False),
        sa.Column("open_button_emoji", sa.String(128), server_default=sa.text("'🛒'"), nullable=False),
        sa.Column("open_button_style", sa.String(16), server_default=sa.text("'success'"), nullable=False),
        sa.Column("catalog_title", sa.String(256), server_default=sa.text("'NEXTBUY • Escolha seu jogo'"), nullable=False),
        sa.Column("catalog_description", sa.Text(), server_default=sa.text("'Escolha um jogo para ver seus produtos.'"), nullable=False),
        sa.Column("catalog_status_text", sa.String(160), server_default=sa.text("'Atendimento: on-line.'"), nullable=False),
        sa.Column("catalog_footer_text", sa.String(2048), server_default=sa.text("'Jogos • Página 1/1'"), nullable=False),
        sa.Column("game_placeholder", sa.String(100), server_default=sa.text("'Selecione um jogo'"), nullable=False),
        sa.Column("selected_game_ids", sa.JSON(), nullable=False),
        sa.Column("published_channel_id", sa.BigInteger()),
        sa.Column("published_message_id", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_game_catalog_panel_configs_guild_id",
        "game_catalog_panel_configs",
        ["guild_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_game_catalog_panel_configs_guild_id",
        table_name="game_catalog_panel_configs",
    )
    op.drop_table("game_catalog_panel_configs")
