import asyncio
from decimal import Decimal
from uuid import UUID

import discord
from sqlalchemy import select

from app.bot.emoji import select_option_emoji
from app.bot.views.components_v2 import format_percent
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
DEFAULT_ACCENT = 0x2B2D31


def build_store_panel_embed(config: StorePanelConfig, product_count: int) -> discord.Embed:
    """Legacy admin preview. Public store messages use Components V2."""
    embed = discord.Embed(
        title=config.title or "NEXTBUY",
        description=config.description or "Selecione um produto abaixo.",
        color=config.color,
    )
    embed.add_field(
        name=config.product_count_label or "Produtos disponíveis",
        value=str(product_count),
        inline=True,
    )
    if config.image_url:
        embed.set_image(url=config.image_url)
    if config.thumbnail_url:
        embed.set_thumbnail(url=config.thumbnail_url)
    if config.footer_text:
        embed.set_footer(text=config.footer_text)
    return embed


def _safe_checkout_title(config: StorePanelConfig, product: Product) -> str:
    emoji = product.emoji or ""
    try:
        title = config.checkout_title_template.format(
            emoji=emoji,
            product=product.name,
            game=product.game_name or "",
        )
    except (KeyError, ValueError):
        title = f"{emoji} {product.name}"
    return " ".join(title.split())[:256] or product.name


def _store_intro(config: StorePanelConfig, product_count: int) -> str:
    title = config.title or "NEXTBUY"
    description = config.description or "Selecione um produto abaixo."
    count_label = config.product_count_label or "Produtos disponíveis"
    return f"## {title}\n{description}\n\n**{count_label}:** `{product_count}`"


def _product_checkout_text(
    config: StorePanelConfig,
    product: Product,
    *,
    coupon_code: str | None = None,
    discount_percent: Decimal | None = None,
) -> str:
    lines = [
        f"## {_safe_checkout_title(config, product)}",
        config.checkout_description or "Confira os detalhes antes de continuar com a compra.",
    ]
    if product.game_name:
        lines.append(f"**Jogo:** {product.game_name}")
    if product.description:
        lines.append(product.description)

    if product.price_credits is None:
        lines.append(f"**{PIX_EMOJI} Preço:** Indisponível")
    else:
        original = money(product.price_credits)
        if coupon_code and discount_percent is not None:
            final = discounted_total(original, discount_percent)
            lines.append(f"**{PIX_EMOJI} Preço:** ~~{format_brl(original)}~~ → **`{format_brl(final)}`**")
            lines.append(
                f"**Cupom:** `{coupon_code}` • **{format_percent(discount_percent)}** de desconto"
            )
        else:
            lines.append(f"**{PIX_EMOJI} Preço:** **`{format_brl(original)}`**")
    return "\n\n".join(line for line in lines if line)


class CouponModal(discord.ui.Modal, title="Adicionar cupom"):
    code = discord.ui.TextInput(
        label="Código do cupom",
        placeholder="Ex: NEXT10",
        min_length=1,
        max_length=40,
    )

    def __init__(self, *, product_id: int, owner_id: int) -> None:
        super().__init__()
        self.product_id = product_id
        self.owner_id = owner_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.user.id != self.owner_id:
            return
        await interaction.response.defer()
        async with SessionLocal() as session:
            product = await session.get(Product, self.product_id)
            config = await get_or_create_store_panel(session, interaction.guild.id)
            coupon = await get_coupon_by_code(
                session,
                guild_id=interaction.guild.id,
                code=str(self.code),
            )
        if product is None or product.guild_id != interaction.guild.id or not product.active:
            await interaction.edit_original_response(
                view=StoreNoticeView("Produto indisponível."),
                content=None,
                embed=None,
            )
            return
        if coupon is None:
            await interaction.edit_original_response(
                view=StoreNoticeView("Cupom inválido, desativado ou sem usos disponíveis."),
                content=None,
                embed=None,
            )
            return
        await interaction.edit_original_response(
            content=None,
            embed=None,
            view=ConfiguredProductCheckoutView(
                config=config,
                product=product,
                owner_id=self.owner_id,
                coupon_id=coupon.id,
                coupon_code=coupon.code,
                discount_percent=coupon.discount_percent,
            ),
        )


class StoreNoticeView(discord.ui.LayoutView):
    def __init__(self, text: str) -> None:
        super().__init__(timeout=120)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(text[:4000]),
                accent_color=DEFAULT_ACCENT,
            )
        )


class ConfiguredProductCheckoutView(discord.ui.LayoutView):
    def __init__(
        self,
        *,
        config: StorePanelConfig,
        product: Product,
        owner_id: int,
        coupon_id: int | None = None,
        coupon_code: str | None = None,
        discount_percent: Decimal | None = None,
    ) -> None:
        super().__init__(timeout=300)
        self.product_id = product.id
        self.owner_id = owner_id
        self.coupon_id = coupon_id
        self._lock = asyncio.Lock()
        self._order_id: UUID | None = None

        buy = discord.ui.Button(
            label=(config.buy_button_label or "Comprar")[:80],
            style=discord.ButtonStyle.success,
            custom_id=f"nextbuy:checkout:{product.id}:buy",
        )
        buy.callback = self._confirm
        coupon = discord.ui.Button(
            label=(config.coupon_button_label or "Adicionar cupom")[:80],
            style=discord.ButtonStyle.secondary,
            custom_id=f"nextbuy:checkout:{product.id}:coupon",
        )
        coupon.callback = self._coupon
        row = discord.ui.ActionRow(buy, coupon)

        children: list[discord.ui.Item] = [
            discord.ui.TextDisplay(
                _product_checkout_text(
                    config,
                    product,
                    coupon_code=coupon_code,
                    discount_percent=discount_percent,
                )[:4000]
            )
        ]
        if product.image_url:
            gallery = discord.ui.MediaGallery()
            gallery.add_item(media=product.image_url, description=product.name[:256])
            children.append(gallery)
        children.append(row)
        self.add_item(discord.ui.Container(*children, accent_color=config.color or DEFAULT_ACCENT))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Essa compra pertence a outro cliente.", ephemeral=True)
        return False

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
                content=None,
                embed=None,
                view=StoreNoticeView(
                    "O pagamento PIX ainda não foi configurado pela equipe. "
                    "Defina `PIX_KEY` e `PIX_RECEIVER_NAME` no ambiente do bot."
                ),
            )
            return

        async with self._lock:
            if self._order_id is not None:
                await interaction.edit_original_response(
                    content=None,
                    embed=None,
                    view=StoreNoticeView("Essa compra já foi criada. Confira o canal de pagamento."),
                )
                return
            try:
                async with SessionLocal() as session, session.begin():
                    user = await get_or_create_user(session, interaction.user.id)
                    product = await session.get(Product, self.product_id)
                    if product is None or product.guild_id != interaction.guild.id:
                        raise ValueError("Produto não encontrado")
                    order, coupon = await create_store_product_order(
                        session,
                        guild_id=interaction.guild.id,
                        user_id=user.id,
                        product=product,
                        coupon_id=self.coupon_id,
                    )
                self._order_id = order.id
            except OutOfStockError:
                await interaction.edit_original_response(
                    content=None,
                    embed=None,
                    view=StoreNoticeView("Esse produto ficou sem estoque."),
                )
                return
            except ValueError as exc:
                await interaction.edit_original_response(
                    content=None,
                    embed=None,
                    view=StoreNoticeView(str(exc) or "Não consegui criar o pedido agora."),
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
                content=None,
                embed=None,
                view=StoreNoticeView(
                    "Não consegui abrir o canal privado de pagamento. "
                    "O pedido foi cancelado e o estoque foi devolvido."
                ),
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
                content=None,
                embed=None,
                view=StoreNoticeView("Não consegui criar o canal de pagamento. O pedido foi cancelado."),
            )
            return

        coupon_text = f" com cupom `{coupon.code}`" if coupon is not None else ""
        await interaction.edit_original_response(
            content=None,
            embed=None,
            view=StoreNoticeView(
                f"**{product.name}** • **`{format_brl(order.total_credits)}`**{coupon_text}\n\n"
                f"O QR Code e o PIX Copia e Cola estão em {ticket.mention}. "
                "Depois de pagar, aguarde a confirmação da equipe."
            ),
        )

    async def _coupon(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            CouponModal(product_id=self.product_id, owner_id=self.owner_id)
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
            if product.product_type.startswith("robux"):
                description = price
            else:
                stock = "∞" if product.stock_quantity is None else str(product.stock_quantity)
                description = f"{price} • estoque {stock}"
            options.append(
                discord.SelectOption(
                    label=product.name[:100],
                    value=str(product.id),
                    description=description[:100],
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
            await interaction.edit_original_response(
                content=None,
                embed=None,
                view=StoreNoticeView("Produto indisponível."),
            )
            return
        await interaction.edit_original_response(
            content=None,
            embed=None,
            view=ConfiguredProductCheckoutView(
                config=config,
                product=product,
                owner_id=interaction.user.id,
            ),
        )


class StorePanelView(discord.ui.LayoutView):
    def __init__(self, *, config: StorePanelConfig, products: list[Product]) -> None:
        super().__init__(timeout=None)
        select = StoreProductSelect(products, config.product_placeholder)
        row = discord.ui.ActionRow(select)

        intro = discord.ui.TextDisplay(_store_intro(config, len(products))[:4000])
        children: list[discord.ui.Item] = []
        if config.thumbnail_url:
            children.append(
                discord.ui.Section(
                    intro,
                    accessory=discord.ui.Thumbnail(config.thumbnail_url, description=config.title[:256]),
                )
            )
        else:
            children.append(intro)
        if config.image_url:
            gallery = discord.ui.MediaGallery()
            gallery.add_item(media=config.image_url, description=(config.title or "NEXTBUY")[:256])
            children.append(gallery)
        children.append(row)
        if config.footer_text:
            children.append(discord.ui.TextDisplay(f"-# {config.footer_text}"[:4000]))
        self.add_item(discord.ui.Container(*children, accent_color=config.color or DEFAULT_ACCENT))


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
        view = StorePanelView(config=config, products=products)

        message: discord.Message | None = None
        if config.published_channel_id and config.published_message_id:
            old_channel = interaction.guild.get_channel(config.published_channel_id)
            if isinstance(old_channel, discord.TextChannel):
                try:
                    old_message = await old_channel.fetch_message(config.published_message_id)
                    if old_channel.id == channel.id:
                        await old_message.edit(content=None, embed=None, view=view)
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
        view = StorePanelView(config=config, products=products)
        channel_id = config.published_channel_id
        message_id = config.published_message_id

    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return False
    try:
        message = await channel.fetch_message(message_id)
        await message.edit(content=None, embed=None, view=view)
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
            bot.add_view(
                StorePanelView(config=config, products=products),
                message_id=config.published_message_id,
            )
