from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    discord_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    total_spent: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )


class Wallet(Base, TimestampMixin):
    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    balance: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )

    __table_args__ = (CheckConstraint("balance >= 0", name="ck_wallet_balance_non_negative"),)


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    reference: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (CheckConstraint("amount <> 0", name="ck_wallet_tx_amount_non_zero"),)


class GuildConfig(Base, TimestampMixin):
    __tablename__ = "guild_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    admin_role_id: Mapped[int | None] = mapped_column(BigInteger)
    support_role_id: Mapped[int | None] = mapped_column(BigInteger)
    delivery_role_id: Mapped[int | None] = mapped_column(BigInteger)
    customer_role_id: Mapped[int | None] = mapped_column(BigInteger)
    ticket_category_id: Mapped[int | None] = mapped_column(BigInteger)
    transcript_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    deliveries_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    feedback_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    calculator_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    faq_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    leaderboard_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    leaderboard_message_id: Mapped[int | None] = mapped_column(BigInteger)
    logs_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    feedback_emoji: Mapped[str] = mapped_column(String(128), default="🐱", server_default="🐱")
    feedback_reminder_minutes: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    feedback_dm_cooldown_hours: Mapped[int] = mapped_column(Integer, default=24, server_default="24")


class Product(Base, TimestampMixin):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(140))
    product_type: Mapped[str] = mapped_column(String(24), index=True)
    game_name: Mapped[str | None] = mapped_column(String(120), index=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    price_credits: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    image_url: Mapped[str | None] = mapped_column(Text)
    emoji: Mapped[str | None] = mapped_column(String(128))
    delivery_mode: Mapped[str] = mapped_column(String(32), default="manual", server_default="manual")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("guild_id", "slug", name="uq_products_guild_slug"),
        CheckConstraint("price_credits IS NULL OR price_credits >= 0", name="ck_product_price_non_negative"),
    )


class RobuxRate(Base, TimestampMixin):
    __tablename__ = "robux_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    code: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(80))
    price_per_robux: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    delivery_label: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint("guild_id", "code", name="uq_robux_rate_guild_code"),
        CheckConstraint("price_per_robux > 0", name="ck_robux_rate_positive"),
    )


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending", index=True)
    total_credits: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    ticket_channel_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    delivery_message_id: Mapped[int | None] = mapped_column(BigInteger)
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint("total_credits >= 0", name="ck_order_total_non_negative"),)


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    name_snapshot: Mapped[str] = mapped_column(String(160))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    image_url_snapshot: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_item_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_order_item_price_non_negative"),
    )


class CreditTopUp(Base, TimestampMixin):
    __tablename__ = "credit_topups"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount_brl: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    credits_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), default="mercado_pago", server_default="mercado_pago")
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending", index=True)
    provider_preference_id: Mapped[str | None] = mapped_column(String(160), unique=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(160), unique=True)
    checkout_url: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("amount_brl > 0", name="ck_topup_amount_positive"),
        CheckConstraint("credits_amount > 0", name="ck_topup_credits_positive"),
    )


class Feedback(Base, TimestampMixin):
    __tablename__ = "feedbacks"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    stars: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(24), default="modal", server_default="modal")
    published_message_id: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (CheckConstraint("stars BETWEEN 1 AND 5", name="ck_feedback_stars_range"),)


class FeedbackReminder(Base, TimestampMixin):
    __tablename__ = "feedback_reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    channel_mention_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dm_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RankTier(Base, TimestampMixin):
    __tablename__ = "rank_tiers"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(80))
    min_spend: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    role_id: Mapped[int] = mapped_column(BigInteger)
    dm_message: Mapped[str] = mapped_column(Text, default="", server_default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    __table_args__ = (
        UniqueConstraint("guild_id", "role_id", name="uq_rank_tier_guild_role"),
        UniqueConstraint("guild_id", "name", name="uq_rank_tier_guild_name"),
        CheckConstraint("min_spend >= 0", name="ck_rank_tier_min_spend_non_negative"),
    )


class TermsDocument(Base, TimestampMixin):
    __tablename__ = "terms_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    code: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(Text)
    emoji: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    __table_args__ = (UniqueConstraint("guild_id", "code", name="uq_terms_guild_code"),)


class TermsAcceptance(Base):
    __tablename__ = "terms_acceptances"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    terms_id: Mapped[int] = mapped_column(ForeignKey("terms_documents.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "terms_id", "version", name="uq_terms_acceptance_version"),
    )


class AutoReply(Base, TimestampMixin):
    __tablename__ = "auto_replies"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(80))
    keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    title: Mapped[str] = mapped_column(String(160))
    content: Mapped[str] = mapped_column(Text)
    emoji: Mapped[str | None] = mapped_column(String(128))
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    __table_args__ = (
        UniqueConstraint("guild_id", "name", name="uq_auto_reply_guild_name"),
        CheckConstraint("cooldown_seconds >= 0", name="ck_auto_reply_cooldown_non_negative"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    actor_discord_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str | None] = mapped_column(String(80))
    target_id: Mapped[str | None] = mapped_column(String(160))
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


Index("ix_orders_guild_user_created", Order.guild_id, Order.user_id, Order.created_at)
Index("ix_feedback_reminders_due_pending", FeedbackReminder.due_at, FeedbackReminder.completed_at)
