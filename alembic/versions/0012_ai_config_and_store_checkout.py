"""AI channel configuration and configurable store checkout.

Revision ID: 0012_ai_store_checkout
Revises: 0011_order_payments
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_ai_store_checkout"
down_revision: str | None = "0011_order_payments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("allowed_channel_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("support_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("suggestions_channel_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "provider_order",
            sa.JSON(),
            nullable=False,
            server_default=sa.text(
                "'[\"openai\",\"anthropic\",\"gemini\",\"xai\",\"mistral\",\"groq\",\"openrouter\"]'::json"
            ),
        ),
        sa.Column("assistant_name", sa.String(length=80), nullable=False, server_default="NEXTBUY"),
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
        sa.UniqueConstraint("guild_id", name="uq_ai_configs_guild_id"),
    )
    op.create_index("ix_ai_configs_guild_id", "ai_configs", ["guild_id"], unique=True)

    op.add_column(
        "store_panel_configs",
        sa.Column(
            "product_count_label",
            sa.String(length=100),
            nullable=False,
            server_default="Produtos disponíveis",
        ),
    )
    op.add_column(
        "store_panel_configs",
        sa.Column(
            "checkout_title_template",
            sa.String(length=256),
            nullable=False,
            server_default="{emoji} {product}",
        ),
    )
    op.add_column(
        "store_panel_configs",
        sa.Column(
            "checkout_description",
            sa.Text(),
            nullable=False,
            server_default="Confira os detalhes antes de continuar com a compra.",
        ),
    )
    op.add_column(
        "store_panel_configs",
        sa.Column(
            "buy_button_label",
            sa.String(length=80),
            nullable=False,
            server_default="Comprar",
        ),
    )
    op.add_column(
        "store_panel_configs",
        sa.Column(
            "coupon_button_label",
            sa.String(length=80),
            nullable=False,
            server_default="Adicionar cupom",
        ),
    )


def downgrade() -> None:
    op.drop_column("store_panel_configs", "coupon_button_label")
    op.drop_column("store_panel_configs", "buy_button_label")
    op.drop_column("store_panel_configs", "checkout_description")
    op.drop_column("store_panel_configs", "checkout_title_template")
    op.drop_column("store_panel_configs", "product_count_label")

    op.drop_index("ix_ai_configs_guild_id", table_name="ai_configs")
    op.drop_table("ai_configs")
