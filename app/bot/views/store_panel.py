import asyncio
import re
from decimal import Decimal
from uuid import UUID

import discord
from sqlalchemy import select

from app.bot.components_v2 import CardLayout, add_action_row, add_select_row, format_percent
from app.bot.emoji import resolve_guild_emoji_aliases, select_option_emoji
from app.bot.views.manual_pix import open_manual_pix_ticket
from app.bot.views.terms_gate import require_current_terms
from app.core.money import money
from app.db.models import Product
from app.db.session import SessionLocal
from app.db.store_models import StorePanelConfig
from app.services.calculator import format_brl
from app.services.manual_payments import cancel_manual_pix_order
from app.services.orders import OutOfStockError
from app.services.pix import PixConfigError, validate_pix_config
from app.services.store_panel import (
    create_store_product_order,
    discounted_total,
    get_coupon_by_code,
    get_or_create_store_panel,
    list_store_products,
)
from app.services.users import get_or_create_user

PIX_EMOJI = "<:PIX:1549632822388592663>"
_EMOJI_CDN_RE = re.compile(
    r"^https?://(?:cdn|media)\.discordapp\.(?:com|net)/emojis/(\d+)\.(gif|png|webp)(?:\?.*)?$",
    re.IGNORECASE,
)


def _accent(config: StorePanelConfig) -> discord.Colour:
    return discord.Colour(config.color)


def _emoji_from_url(value: str | None) -> str:
    match = _EMOJI_CDN_RE.match((value or "").strip())
    if match is None:
        return ""
    emoji_id, extension = match.groups()
    prefix = "a" if extension.lower() == "gif" else ""
    return f"<{prefix}:emoji:{emoji_id}>"


def _product_emoji(product: Product) -> str:
    raw = (product.emoji or "").strip()
    if raw.startswith("<:") or raw.startswith("<a:"):
        return raw
    return _emoji_from_url(raw) or _emoji_from_url(product.image_url)


def _product_image_url(product: Product) -> str | None:
    value = (product.image_url or "").strip()
    if not value or _EMOJI_CDN_RE.match(value):
        return None
    return value


def build_store_panel_embed(
    config: StorePanelConfig,
    product_count: int,
    guild: discord.Guild | None = None,
) -> discord.Embed:
    """Prévia administrativa; resolve :emoji: usando os emojis do servidor."""
    title = resolve_guild_emoji_aliases(config.title or "NEXTBUY", guild)
    description = resolve_guild_emoji_aliases(
        config.description or "Selecione um produto abaixo.", guild
    )
    footer = resolve_guild_emoji_aliases(config.footer_text or "", guild)
    count_label = resolve_guild_emoji_aliases(config.product_count_label or "", guild)

    embed = discord.Embed(
        title=title,
        description=description,
        color=config.color,
    )
    if count_label:
        embed.add_field(name=count_label, value=str(product_count), inline=False)
    if config.image_url:
        embed.set_image(url=config.image_url)
    if config.thumbnail_url:
        embed.set_thumbnail(url=config.thumbnail_url)
    if footer:
        embed.set_footer(text=footer)
    return embed


def _product_title(config: StorePanelConfig, product: Product) -> str:
    emoji = _product_emoji(product)
    template = config.checkout_title_template or "{emoji} {product}"
    try:
        title = template.format(emoji=emoji, product=product.name, game=product.game_name or "")
    except (KeyError, ValueError):
        title = f"{emoji} {product.name}"
    return " ".join(title.split()) or product.name


def build_store_panel_card(
    config: StorePanelConfig,
    products: list[Product],
    *,
    guild: discord.Guild | None = None,
    timeout: float | None = None,
) -> "StorePanelLayout":
    return StorePanelLayout(
        config=config,
        products=products,
        guild=guild,
        timeout=timeout,
    )


def build_product_checkout_card(
    config: StorePanelConfig,
    product: Product,
    *,
    owner_id: int,
    quantity: int = 1,
    coupon_id: int | None = None,
    coupon_code: str | None = None,
    discount_percent: Decimal | None = None,
) -> "ConfiguredProductCheckoutLayout":
    return ConfiguredProductCheckoutLayout(
        config=config,
        product=product,
        owner_id=owner_id,
        quantity=quantity,
        coupon_id=coupon_id,
        coupon_code=coupon_code,
        discount_percent=discount_percent,
    )


class CouponModal(discord.ui.Modal, title="Adicionar cupom"):
    code = discord.ui.TextInput(
        label="Código do cupom",
        placeholder="Ex: NEXT10",
        min_length=1,
        max_length=40,
    )

    def __init__(self, *, product_id: int, owner_id: int, quantity: int) -> None:
        super().__init__()
        self.product_id = product_id
        self.owner_id = owner_id
        self.quantity = quantity

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.user.id != self.owner_id:
            return
        await interaction.response.defer()
        async with SessionLocal() as session, session.begin():
            product = await session.get(Product, self.product_id)
            config = await get_or_create_store_panel(session, interaction.guild.id)
            coupon = await get_coupon_by_code(
                session,
                guild_id=interaction.guild.id,
                code=str(self.code),
            )
        if product is None or product.guild_id != interaction.guild.id or not product.active:
            await interaction.edit_original_response(content="Produto indisponível.", view=None)
            return
        if coupon is None:
            await interaction.edit_original_response(
                content="Cupom inválido, desativado ou sem usos disponíveis."
            )
            return
        await interaction.edit_original_response(
            content=None,
            view=build_product_checkout_card(
                config,
                product,
                owner_id=self.owner_id,
                quantity=self.quantity,
                coupon_id=coupon.id,
                coupon_code=coupon.code,
                discount_percent=coupon.discount_percent,
            ),
        )


class ConfiguredProductCheckoutLayout(discord.ui.LayoutView):
    def __init__(
        self,
        *,
        config: StorePanelConfig,
        product: Product,
        owner_id: int,
        quantity: int = 1,
        coupon_id: int | None = None,
        coupon_code: str | None = None,
        discount_percent: Decimal | None = None,
    ) -> None:
        super().__init__(timeout=300)
        max_quantity = 99 if product.stock_quantity is None else max(1, min(99, product.stock_quantity))
        self.product_id = product.id
        self.owner_id = owner_id
        self.quantity = max(1, min(int(quantity), max_quantity))
        self.coupon_id = coupon_id
        self.coupon_code = coupon_code
        self.discount_percent = discount_percent
        self._lock = asyncio.Lock()
        self._order_id: UUID | None = None

        lines: list[str] = []
        if product.game_name:
            lines.append(f"**Jogo:** {product.game_name}")
        if product.description:
            lines.append(product.description)

        lines.append(f"**Quantidade:** `{self.quantity}`")
        if product.price_credits is None:
            lines.append(f"**{PIX_EMOJI} Preço:** `Indisponível`")
        else:
            unit = money(product.price_credits)
            original = money(unit * self.quantity)
            if self.quantity > 1:
                lines.append(f"**Valor unitário:** `{format_brl(unit)}`")
            if coupon_code and discount_percent is not None:
                final = discounted_total(original, discount_percent)
                lines.append(
                    f"**{PIX_EMOJI} Total:** ~~{format_brl(original)}~~  **`{format_brl(final)}`**"
                )
                lines.append(
                    f"**Cupom:** `{coupon_code}` • **{format_percent(discount_percent)} de desconto**"
                )
            else:
                lines.append(f"**{PIX_EMOJI} Total:** `{format_brl(original)}`")

        card = CardLayout(
            title=_product_title(config, product),
            description=config.checkout_description or None,
            lines=lines,
            footer=config.footer_text or None,
            accent_colour=_accent(config),
            image_url=_product_image_url(product),
            timeout=300,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)

        decrease = discord.ui.Button(
            label="−",
            style=discord.ButtonStyle.secondary,
            disabled=self.quantity <= 1,
        )
        quantity_label = discord.ui.Button(
            label=f"Qtd: {self.quantity}",
            style=discord.ButtonStyle.secondary,
            disabled=True,
        )
        increase = discord.ui.Button(
            label="+",
            style=discord.ButtonStyle.secondary,
            disabled=self.quantity >= max_quantity,
        )
        buy = discord.ui.Button(
            label=(config.buy_button_label or "Comprar")[:80],
            style=discord.ButtonStyle.success,
            custom_id=f"nextbuy:store:buy:{product.id}:{owner_id}",
        )
        coupon = discord.ui.Button(
            label=(config.coupon_button_label or "Adicionar cupom")[:80],
            style=discord.ButtonStyle.secondary,
            custom_id=f"nextbuy:store:coupon:{product.id}:{owner_id}",
        )
        decrease.callback = self._decrease
        increase.callback = self._increase
        buy.callback = self._confirm
        coupon.callback = self._coupon
        add_action_row(self.container, decrease, quantity_label, increase, buy, coupon)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Essa compra pertence a outro cliente.", ephemeral=True)
        return False

    async def _refresh_quantity(self, interaction: discord.Interaction, delta: int) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer()
        async with SessionLocal() as session, session.begin():
            product = await session.get(Product, self.product_id)
            config = await get_or_create_store_panel(session, interaction.guild.id)
        if product is None or product.guild_id != interaction.guild.id or not product.active:
            await interaction.edit_original_response(content="Produto indisponível.", view=None)
            return
        max_quantity = 99 if product.stock_quantity is None else max(1, min(99, product.stock_quantity))
        quantity = max(1, min(self.quantity + delta, max_quantity))
        await interaction.edit_original_response(
            content=None,
            view=build_product_checkout_card(
                config,
                product,
                owner_id=self.owner_id,
                quantity=quantity,
                coupon_id=self.coupon_id,
                coupon_code=self.coupon_code,
                discount_percent=self.discount_percent,
            ),
        )

    async def _decrease(self, interaction: discord.Interaction) -> None:
        await self._refresh_quantity(interaction, -1)

    async def _increase(self, interaction: discord.Interaction) -> None:
        await self._refresh_quantity(interaction, 1)

    async def _coupon(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            CouponModal(
                product_id=self.product_id,
                owner_id=self.owner_id,
                quantity=self.quantity,
            )
        )

    async def _confirm(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await require_current_terms(interaction, resume_view=self):
            return

        try:
            validate_pix_config()
        except PixConfigError:
            await interaction.edit_original_response(
                content=(
                    "O pagamento PIX ainda não foi configurado pela equipe. "
                    "Defina PIX_KEY e PIX_RECEIVER_NAME no ambiente do bot."
                ),
                view=None,
            )
            return

        product_name = "Produto"
        async with self._lock:
            if self._order_id is not None:
                await interaction.edit_original_response(
                    content="Essa compra já foi criada. Consulte o canal privado de pagamento.",
                    view=None,
                )
                return
            try:
                async with SessionLocal() as session, session.begin():
                    user = await get_or_create_user(session, interaction.user.id)
                    product = await session.get(Product, self.product_id)
                    if product is None or product.guild_id != interaction.guild.id:
                        raise ValueError("Produto não encontrado")
                    product_name = product.name
                    order, coupon = await create_store_product_order(
                        session,
                        guild_id=interaction.guild.id,
                        user_id=user.id,
                        product=product,
                        quantity=self.quantity,
                        coupon_id=self.coupon_id,
                    )
                self._order_id = order.id
            except OutOfStockError:
                await interaction.edit_original_response(
                    content="Não há estoque suficiente para essa quantidade.", view=None
                )
                return
            except ValueError as exc:
                await interaction.edit_original_response(
                    content=str(exc) or "Não consegui criar o pedido agora.",
                    view=None,
                )
                return

        try:
            ticket = await open_manual_pix_ticket(interaction, order_id=order.id)
        except (ValueError, discord.Forbidden, discord.HTTPException):
            async with SessionLocal() as session, session.begin():
                await cancel_manual_pix_order(
                    session,
                    order_id=order.id,
                    actor_discord_id=interaction.user.id,
                    reason="falha ao abrir ticket de pagamento",
                )
            self._order_id = None
            await interaction.edit_original_response(
                content=(
                    "Não consegui abrir o canal privado de pagamento. "
                    "O pedido foi cancelado e o estoque foi devolvido."
                ),
                view=None,
            )
            return

        if ticket is None:
            async with SessionLocal() as session, session.begin():
                await cancel_manual_pix_order(
                    session,
                    order_id=order.id,
                    actor_discord_id=interaction.user.id,
                    reason="ticket de pagamento indisponível",
                )
            self._order_id = None
            await interaction.edit_original_response(
                content="Não consegui criar o canal de pagamento. O pedido foi cancelado.",
                view=None,
            )
            return

        coupon_text = f" • cupom `{coupon.code}`" if coupon is not None else ""
        quantity_text = f"{self.quantity}× " if self.quantity > 1 else ""
        await interaction.edit_original_response(
            content=(
                f"**{quantity_text}{product_name}** • **{format_brl(order.total_credits)}**{coupon_text}. "
                f"O QR Code e o PIX Copia e Cola estão em {ticket.mention}. "
                "Depois de pagar, aguarde a confirmação da equipe."
            ),
            view=None,
        )


class StoreProductSelect(discord.ui.Select):
    def __init__(self, products: list[Product], placeholder: str) -> None:
        options: list[discord.SelectOption] = []
        for product in products[:25]:
            price = (
                "Indisponível"
                if product.price_credits is None
                else format_brl(money(product.price_credits))
            )
            stock = "∞" if product.stock_quantity is None else str(product.stock_quantity)
            options.append(
                discord.SelectOption(
                    label=product.name[:100],
                    value=str(product.id),
                    description=f"{price} • estoque {stock}"[:100],
                    emoji=select_option_emoji(product.emoji),
                )
            )
        if not options:
            options.append(
                discord.SelectOption(
                    label="Nenhum produto disponível",
                    value="none",
                    description="Configure produtos no painel administrativo",
                )
            )
        super().__init__(
            custom_id="nextbuy:store:product-select",
            placeholder=placeholder[:100] or "Selecione um produto",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        if self.values[0] == "none":
            await interaction.response.send_message("Nenhum produto disponível agora.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        product_id = int(self.values[0])
        async with SessionLocal() as session, session.begin():
            product = await session.get(Product, product_id)
            config = await get_or_create_store_panel(session, interaction.guild.id)
        if (
            product is None
            or product.guild_id != interaction.guild.id
            or not product.active
            or product.price_credits is None
            or (product.stock_quantity is not None and product.stock_quantity <= 0)
        ):
            await interaction.edit_original_response(content="Produto indisponível.", view=None)
            return
        await interaction.edit_original_response(
            content=None,
            view=build_product_checkout_card(
                config,
                product,
                owner_id=interaction.user.id,
            ),
        )


class StorePanelLayout(discord.ui.LayoutView):
    def __init__(
        self,
        *,
        config: StorePanelConfig,
        products: list[Product],
        guild: discord.Guild | None = None,
        timeout: float | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        title = resolve_guild_emoji_aliases(config.title or "NEXTBUY", guild)
        description = resolve_guild_emoji_aliases(
            config.description or "Selecione um produto abaixo.", guild
        )
        footer = resolve_guild_emoji_aliases(config.footer_text or "", guild)
        count_label = resolve_guild_emoji_aliases(config.product_count_label or "", guild)

        lines: list[str] = []
        if count_label:
            lines.append(f"**{count_label}:** `{len(products)}`")
        card = CardLayout(
            title=title,
            description=description,
            lines=lines,
            footer=footer or None,
            accent_colour=_accent(config),
            image_url=config.image_url,
            thumbnail_url=config.thumbnail_url,
            timeout=timeout,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)
        add_select_row(self.container, StoreProductSelect(products, config.product_placeholder))


async def load_store_panel_view(guild_id: int) -> tuple[StorePanelConfig, list[Product]]:
    async with SessionLocal() as session, session.begin():
        config = await get_or_create_store_panel(session, guild_id)
        products = await list_store_products(session, guild_id=guild_id, config=config)
        return config, products


async def publish_store_panel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
) -> discord.Message | None:
    if interaction.guild is None:
        return None
    async with SessionLocal() as session, session.begin():
        config = await get_or_create_store_panel(session, interaction.guild.id)
        products = await list_store_products(session, guild_id=interaction.guild.id, config=config)
        view = build_store_panel_card(
            config,
            products,
            guild=interaction.guild,
            timeout=None,
        )

        message: discord.Message | None = None
        if config.published_channel_id and config.published_message_id:
            old_channel = interaction.guild.get_channel(config.published_channel_id)
            if isinstance(old_channel, discord.TextChannel):
                try:
                    old_message = await old_channel.fetch_message(config.published_message_id)
                    if old_channel.id == channel.id:
                        await old_message.edit(content=None, embeds=[], view=view)
                        message = old_message
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    message = None
        if message is None:
            message = await channel.send(view=view)
        config.published_channel_id = channel.id
        config.published_message_id = message.id
        await session.flush()
        return message


async def refresh_published_store_panel(guild: discord.Guild) -> bool:
    async with SessionLocal() as session, session.begin():
        config = await session.scalar(
            select(StorePanelConfig).where(StorePanelConfig.guild_id == guild.id)
        )
        if config is None or not config.published_channel_id or not config.published_message_id:
            return False
        products = await list_store_products(session, guild_id=guild.id, config=config)
        view = build_store_panel_card(
            config,
            products,
            guild=guild,
            timeout=None,
        )
        channel_id = config.published_channel_id
        message_id = config.published_message_id

    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return False
    try:
        message = await channel.fetch_message(message_id)
        await message.edit(content=None, embeds=[], view=view)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return False
    return True


async def restore_store_panel_views(bot: discord.Client) -> None:
    async with SessionLocal() as session:
        configs = list(
            (
                await session.scalars(
                    select(StorePanelConfig).where(StorePanelConfig.published_message_id.is_not(None))
                )
            ).all()
        )
        for config in configs:
            if config.published_message_id is None:
                continue
            products = await list_store_products(
                session,
                guild_id=config.guild_id,
                config=config,
            )
            guild = bot.get_guild(config.guild_id)
            bot.add_view(
                build_store_panel_card(
                    config,
                    products,
                    guild=guild,
                    timeout=None,
                ),
                message_id=config.published_message_id,
            )
