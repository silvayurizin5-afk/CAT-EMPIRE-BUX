"""initial NEXTBUY schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-15
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column("total_spent", sa.Numeric(14, 2), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_users_discord_user_id", "users", ["discord_user_id"], unique=True)

    op.create_table(
        "guild_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("admin_role_id", sa.BigInteger()),
        sa.Column("support_role_id", sa.BigInteger()),
        sa.Column("delivery_role_id", sa.BigInteger()),
        sa.Column("customer_role_id", sa.BigInteger()),
        sa.Column("ticket_category_id", sa.BigInteger()),
        sa.Column("transcript_channel_id", sa.BigInteger()),
        sa.Column("deliveries_channel_id", sa.BigInteger()),
        sa.Column("feedback_channel_id", sa.BigInteger()),
        sa.Column("calculator_channel_id", sa.BigInteger()),
        sa.Column("faq_channel_id", sa.BigInteger()),
        sa.Column("leaderboard_channel_id", sa.BigInteger()),
        sa.Column("leaderboard_message_id", sa.BigInteger()),
        sa.Column("logs_channel_id", sa.BigInteger()),
        sa.Column("feedback_emoji", sa.String(128), server_default=sa.text("'🐱'"), nullable=False),
        sa.Column("feedback_reminder_minutes", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("feedback_dm_cooldown_hours", sa.Integer(), server_default=sa.text("24"), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_guild_configs_guild_id", "guild_configs", ["guild_id"], unique=True)

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(140), nullable=False),
        sa.Column("product_type", sa.String(24), nullable=False),
        sa.Column("game_name", sa.String(120)),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("price_credits", sa.Numeric(14, 2)),
        sa.Column("image_url", sa.Text()),
        sa.Column("emoji", sa.String(128)),
        sa.Column("delivery_mode", sa.String(32), server_default=sa.text("'manual'"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("guild_id", "slug", name="uq_products_guild_slug"),
        sa.CheckConstraint("price_credits IS NULL OR price_credits >= 0", name="ck_product_price_non_negative"),
    )
    op.create_index("ix_products_guild_id", "products", ["guild_id"])
    op.create_index("ix_products_product_type", "products", ["product_type"])
    op.create_index("ix_products_game_name", "products", ["game_name"])
    op.create_index("ix_products_active", "products", ["active"])

    op.create_table(
        "robux_rates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("price_per_robux", sa.Numeric(18, 6), nullable=False),
        sa.Column("delivery_label", sa.String(120)),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("guild_id", "code", name="uq_robux_rate_guild_code"),
        sa.CheckConstraint("price_per_robux > 0", name="ck_robux_rate_positive"),
    )
    op.create_index("ix_robux_rates_guild_id", "robux_rates", ["guild_id"])

    op.create_table(
        "wallets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("balance", sa.Numeric(14, 2), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("user_id", name="uq_wallets_user_id"),
        sa.CheckConstraint("balance >= 0", name="ck_wallet_balance_non_negative"),
    )

    op.create_table(
        "wallet_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(14, 2), nullable=False),
        sa.Column("reference", sa.String(160), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("amount <> 0", name="ck_wallet_tx_amount_non_zero"),
    )
    op.create_index("ix_wallet_transactions_user_id", "wallet_transactions", ["user_id"])
    op.create_index("ix_wallet_transactions_kind", "wallet_transactions", ["kind"])
    op.create_index("ix_wallet_transactions_reference", "wallet_transactions", ["reference"], unique=True)

    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(24), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("total_credits", sa.Numeric(14, 2), nullable=False),
        sa.Column("ticket_channel_id", sa.BigInteger()),
        sa.Column("delivery_message_id", sa.BigInteger()),
        sa.Column("notes", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint("total_credits >= 0", name="ck_order_total_non_negative"),
    )
    op.create_index("ix_orders_guild_id", "orders", ["guild_id"])
    op.create_index("ix_orders_user_id", "orders", ["user_id"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_ticket_channel_id", "orders", ["ticket_channel_id"])
    op.create_index("ix_orders_guild_user_created", "orders", ["guild_id", "user_id", "created_at"])

    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="SET NULL")),
        sa.Column("name_snapshot", sa.String(160), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("image_url_snapshot", sa.Text()),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_order_item_quantity_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_order_item_price_non_negative"),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])

    op.create_table(
        "credit_topups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("amount_brl", sa.Numeric(14, 2), nullable=False),
        sa.Column("credits_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("provider", sa.String(32), server_default=sa.text("'mercado_pago'"), nullable=False),
        sa.Column("status", sa.String(24), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("provider_preference_id", sa.String(160), unique=True),
        sa.Column("provider_payment_id", sa.String(160), unique=True),
        sa.Column("checkout_url", sa.Text()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint("amount_brl > 0", name="ck_topup_amount_positive"),
        sa.CheckConstraint("credits_amount > 0", name="ck_topup_credits_positive"),
    )
    op.create_index("ix_credit_topups_user_id", "credit_topups", ["user_id"])
    op.create_index("ix_credit_topups_guild_id", "credit_topups", ["guild_id"])
    op.create_index("ix_credit_topups_status", "credit_topups", ["status"])

    op.create_table(
        "feedbacks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("source", sa.String(24), server_default=sa.text("'modal'"), nullable=False),
        sa.Column("published_message_id", sa.BigInteger()),
        *_timestamps(),
        sa.CheckConstraint("stars BETWEEN 1 AND 5", name="ck_feedback_stars_range"),
    )
    op.create_index("ix_feedbacks_user_id", "feedbacks", ["user_id"])

    op.create_table(
        "feedback_reminders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel_mention_sent_at", sa.DateTime(timezone=True)),
        sa.Column("dm_sent_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index("ix_feedback_reminders_user_id", "feedback_reminders", ["user_id"])
    op.create_index("ix_feedback_reminders_due_at", "feedback_reminders", ["due_at"])
    op.create_index("ix_feedback_reminders_due_pending", "feedback_reminders", ["due_at", "completed_at"])

    op.create_table(
        "rank_tiers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("min_spend", sa.Numeric(14, 2), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("dm_message", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("guild_id", "role_id", name="uq_rank_tier_guild_role"),
        sa.UniqueConstraint("guild_id", "name", name="uq_rank_tier_guild_name"),
        sa.CheckConstraint("min_spend >= 0", name="ck_rank_tier_min_spend_non_negative"),
    )
    op.create_index("ix_rank_tiers_guild_id", "rank_tiers", ["guild_id"])

    op.create_table(
        "terms_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("emoji", sa.String(128)),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("guild_id", "code", name="uq_terms_guild_code"),
    )
    op.create_index("ix_terms_documents_guild_id", "terms_documents", ["guild_id"])

    op.create_table(
        "terms_acceptances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("terms_id", sa.Integer(), sa.ForeignKey("terms_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "terms_id", "version", name="uq_terms_acceptance_version"),
    )
    op.create_index("ix_terms_acceptances_user_id", "terms_acceptances", ["user_id"])

    op.create_table(
        "auto_replies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("emoji", sa.String(128)),
        sa.Column("cooldown_seconds", sa.Integer(), server_default=sa.text("30"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("guild_id", "name", name="uq_auto_reply_guild_name"),
        sa.CheckConstraint("cooldown_seconds >= 0", name="ck_auto_reply_cooldown_non_negative"),
    )
    op.create_index("ix_auto_replies_guild_id", "auto_replies", ["guild_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_discord_id", sa.BigInteger()),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("target_type", sa.String(80)),
        sa.Column("target_id", sa.String(160)),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_logs_guild_id", "audit_logs", ["guild_id"])
    op.create_index("ix_audit_logs_actor_discord_id", "audit_logs", ["actor_discord_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])


def downgrade() -> None:
    for table in (
        "audit_logs",
        "auto_replies",
        "terms_acceptances",
        "terms_documents",
        "rank_tiers",
        "feedback_reminders",
        "feedbacks",
        "credit_topups",
        "order_items",
        "orders",
        "wallet_transactions",
        "wallets",
        "robux_rates",
        "products",
        "guild_configs",
        "users",
    ):
        op.drop_table(table)
