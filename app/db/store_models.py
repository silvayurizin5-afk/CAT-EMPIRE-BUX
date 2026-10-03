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
    product_count_label: Mapped[str] = mapped_column(
        String(100), default="Produtos disponíveis", server_default="Produtos disponíveis"
    )
    checkout_title_template: Mapped[str] = mapped_column(
        String(256), default="{emoji} {product}", server_default="{emoji} {product}"
    )
    checkout_description: Mapped[str] = mapped_column(
        Text,
        default="Confira os detalhes antes de continuar com a compra.",
        server_default="Confira os detalhes antes de continuar com a compra.",
    )
    buy_button_label: Mapped[str] = mapped_column(
        String(80), default="Comprar", server_default="Comprar"
    )
    coupon_button_label: Mapped[str] = mapped_column(
        String(80), default="Adicionar cupom", server_default="Adicionar cupom"
    )
    # Configuração livre da mensagem pública de entrega. Mantida em JSON para permitir
    # evoluir templates/visual sem criar uma coluna para cada detalhe visual.
    delivery_config: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    # Campos legados mantidos apenas para compatibilidade com registros antigos.
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


class GameCatalogPanelConfig(Base, TimestampMixin):
    __tablename__ = "game_catalog_panel_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(
        String(256),
        default="NEXTBUY",
        server_default="NEXTBUY",
    )
    status_text: Mapped[str] = mapped_column(
        String(160),
        default="Atendimento on-line",
        server_default="Atendimento on-line",
    )
    status_emoji: Mapped[str] = mapped_column(
        String(128),
        default="🟢",
        server_default="🟢",
    )
    description: Mapped[str] = mapped_column(
        Text,
        default=(
            "Robux, Gamepasses, itens e produtos dos seus jogos favoritos em um só lugar.\n"
            "Clique em **Abrir catálogo** para escolher um jogo e depois o produto."
        ),
        server_default=(
            "Robux, Gamepasses, itens e produtos dos seus jogos favoritos em um só lugar.\n"
            "Clique em **Abrir catálogo** para escolher um jogo e depois o produto."
        ),
    )
    color: Mapped[int] = mapped_column(Integer, default=0x7B2CBF, server_default="8072383")
    image_url: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    footer_text: Mapped[str] = mapped_column(
        String(2048),
        default="{count} jogo(s) disponível(is) • NEXTBUY",
        server_default="{count} jogo(s) disponível(is) • NEXTBUY",
    )
    open_button_label: Mapped[str] = mapped_column(
        String(80),
        default="Abrir catálogo",
        server_default="Abrir catálogo",
    )
    open_button_emoji: Mapped[str] = mapped_column(
        String(128),
        default="🛒",
        server_default="🛒",
    )
    open_button_style: Mapped[str] = mapped_column(
        String(16),
        default="success",
        server_default="success",
    )
    catalog_title: Mapped[str] = mapped_column(
        String(256),
        default="NEXTBUY • Escolha seu jogo",
        server_default="NEXTBUY • Escolha seu jogo",
    )
    catalog_description: Mapped[str] = mapped_column(
        Text,
        default="Escolha um jogo para ver seus produtos.",
        server_default="Escolha um jogo para ver seus produtos.",
    )
    catalog_status_text: Mapped[str] = mapped_column(
        String(160),
        default="Atendimento: on-line.",
        server_default="Atendimento: on-line.",
    )
    catalog_footer_text: Mapped[str] = mapped_column(
        String(2048),
        default="Jogos • Página 1/1",
        server_default="Jogos • Página 1/1",
    )
    game_placeholder: Mapped[str] = mapped_column(
        String(100),
        default="Selecione um jogo",
        server_default="Selecione um jogo",
    )
    selected_game_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
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
            "discount_percent > 0 AND discount_percent < 100",
            name="ck_store_coupon_discount_percent",
        ),
        CheckConstraint("max_uses IS NULL OR max_uses > 0", name="ck_store_coupon_max_uses"),
        CheckConstraint("uses >= 0", name="ck_store_coupon_uses_non_negative"),
    )
