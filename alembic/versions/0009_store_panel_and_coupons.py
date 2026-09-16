"""configurable store panel and coupons

Revision ID: 0009_store_panel_and_coupons
Revises: 0008_faq_buttons
Create Date: 2026-09-16
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0009_store_panel_and_coupons"
down_revision: str | None = "0008_faq_buttons"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "store_panel_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(256), server_default=sa.text("'NEXTBUY'"), nullable=False),
        sa.Column(
            "description",
            sa.Text(),
            server_default=sa.text("'Selecione um produto abaixo para ver os detalhes e comprar.'"),
            nullable=False,
        ),
        sa.Column("color", sa.Integer(), server_default=sa.text("2829617"), nullable=False),
        sa.Column("image_url", sa.Text()),
        sa.Column("thumbnail_url", sa.Text()),
        sa.Column(
            "footer_text",
            sa.String(2048),
            server_default=sa.text("'NEXTBUY • Loja'"),
            nullable=False,
        ),
        sa.Column(
            "product_placeholder",
            sa.String(100),
            server_default=sa.text("'Selecione um produto'"),
            nullable=False,
        ),
        sa.Column(
            "topup_label",
            sa.String(80),
            server_default=sa.text("'Adicionar créditos'"),
            nullable=False,
        ),
        sa.Column(
            "profile_label",
            sa.String(80),
            server_default=sa.text("'Meu perfil'"),
            nullable=False,
        ),
        sa.Column(
            "terms_label",
            sa.String(80),
            server_default=sa.text("'Termos'"),
            nullable=False,
        ),
        sa.Column("selected_product_ids", sa.JSON(), nullable=False),
        sa.Column("published_channel_id", sa.BigInteger()),
        sa.Column("published_message_id", sa.BigInteger()),
        *_timestamps(),
    )
    op.create_index(
        "ix_store_panel_configs_guild_id",
        "store_panel_configs",
        ["guild_id"],
        unique=True,
    )

    op.create_table(
        "store_coupons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("max_uses", sa.Integer()),
        sa.Column("uses", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("guild_id", "code", name="uq_store_coupon_guild_code"),
        sa.CheckConstraint(
            "discount_percent > 0 AND discount_percent <= 100",
            name="ck_store_coupon_discount_percent",
        ),
        sa.CheckConstraint(
            "max_uses IS NULL OR max_uses > 0",
            name="ck_store_coupon_max_uses",
        ),
        sa.CheckConstraint("uses >= 0", name="ck_store_coupon_uses_non_negative"),
    )
    op.create_index("ix_store_coupons_guild_id", "store_coupons", ["guild_id"])


def downgrade() -> None:
    op.drop_index("ix_store_coupons_guild_id", table_name="store_coupons")
    op.drop_table("store_coupons")
    op.drop_index("ix_store_panel_configs_guild_id", table_name="store_panel_configs")
    op.drop_table("store_panel_configs")
