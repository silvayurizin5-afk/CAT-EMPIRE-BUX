from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, TimestampMixin


class StorePanelConfig(Base, TimestampMixin):
    __tablename__ = "store_panel_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(256), default="NEXTBUY", server_default="NEXTBUY")
    description: Mapped[str] = mapped_column(
        Text,
        default="Selecione um produto abaixo para ver os detalhes e comprar.",
        server_default="Selecione um produto abaixo para ver os detalhes e comprar.",
    )
    color: Mapped[int] = mapped_column(Integer, default=0x2B2D31, server_default="2829617")
    image_url: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    footer_text: Mapped[str] = mapped_column(
        String(2048), default="NEXTBUY • Loja", server_default="NEXTBUY • Loja"
    )
    product_placeholder: Mapped[str] = mapped_column(
        String(100), default="Selecione um produto", server_default="Selecione um produto"
    )
    topup_label: Mapped[str] = mapped_column(
        String(80), default="Adicionar créditos", server_default="Adicionar créditos"
    )
    profile_label: Mapped[str] = mapped_column(
        String(80), default="Meu perfil", server_default="Meu perfil"
    )
    terms_label: Mapped[str] = mapped_column(
        String(80), default="Termos", server_default="Termos"
    )
    selected_product_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    game_icons: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    published_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    published_message_id: Mapped[int | None] = mapped_column(BigInteger)


class StoreCoupon(Base, TimestampMixin):
    __tablename__ = "store_coupons"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    code: Mapped[str] = mapped_column(String(40))
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    max_uses: Mapped[int | None] = mapped_column(Integer)
    uses: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint("guild_id", "code", name="uq_store_coupon_guild_code"),
        CheckConstraint(
            "discount_percent > 0 AND discount_percent <= 100",
            name="ck_store_coupon_discount_percent",
        ),
        CheckConstraint("max_uses IS NULL OR max_uses > 0", name="ck_store_coupon_max_uses"),
        CheckConstraint("uses >= 0", name="ck_store_coupon_uses_non_negative"),
    )
