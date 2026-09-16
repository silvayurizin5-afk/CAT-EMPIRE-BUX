import asyncio
from decimal import Decimal
from uuid import UUID

import discord
from sqlalchemy import select

from app.bot.views import store
from app.bot.views.profile import build_profile_embed
from app.bot.views.terms_gate import require_current_terms
from app.bot.workflows.leaderboard import refresh_leaderboard
from app.bot.workflows.ranks import sync_customer_roles
from app.bot.workflows.tickets import open_order_ticket
from app.core.money import money
from app.db.models import Product
from app.db.session import SessionLocal
from app.db.store_models import StorePanelConfig
from app.services.catalog import list_active_terms
from app.services.profiles import get_customer_profile
from app.services.store_panel import (
    create_store_product_order,
    discounted_total,
    get_coupon_by_code,
    get_or_create_store_panel,
    list_store_products,
)
from app.services.users import get_or_create_user
from app.services.wallets import InsufficientCreditsError


async def _finish_paid_order(interaction: discord.Interaction, order_id: UUID) -> str:
    if interaction.guild is None:
        return "ticket pendente"
    member = interaction.guild.get_member(interaction.user.id)
    if member is None:
        try:
            member = await interaction.guild.fetch_member(interaction.user.id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            member = None
    if member is not None:
        await sync_customer_roles(member)
    await refresh_leaderboard(interaction.guild)
    ticket = await open_order_ticket(interaction, order_id=order_id)
    return ticket.mention if ticket is not None else "ticket pendente de configuração"


def build_store_panel_embed(config: StorePanelConfig, product_count: int) -> discord.Embed:
    embed = discord.Embed(
        title=config.title or "NEXTBUY",
        description=config.description or "Selecione um produto abaixo.",
        color=config.color,
    )
    embed.add_field(
        name="Produtos disponíveis",
        value=str(product_count),
        inline=True,
    )
    embed.add_field(
        name="Pagamento",
        value="Créditos NEXTBUY • 1 crédito = R$ 1,00",
        inline=True,
    )
    if config.image_url:
        embed.set_image(url=config.image_url)
    if config.thumbnail_url:
        embed.set_thumbnail(url=config.thumbnail_url)
    if config.footer_text:
        embed.set_footer(text=config.footer_text)
    return embed


def build_product_checkout_embed(
    product: Product,
    *,
    coupon_code: str | None = None,
    discount_percent: Decimal | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=product.name,
        description=product.description or "Confira os detalhes antes de comprar.",
        color=discord.Color.from_rgb(43, 45, 49),
    )
    embed.add_field(name="Jogo", value=product.game_name or "—", inline=True)
    if product.price_credits is None:
        embed.add_field(name="Preço", value="Sob cotação", inline=True)
    else:
        original = money(product.price_credits)
        if coupon_code and discount_percent:
            final = discounted_total(original, discount_percent)
            embed.add_field(
                name="Preço",
                value=(
                    f"~~R$ {original:.2f}~~\n"
                    f"**R$ {final:.2f} • {final:.2f} créditos**"
                ),
                inline=True,
            )
            embed.add_field(
                name="Cupom",
                value=f"`{coupon_code}` • {discount_percent:.2f}% de desconto",
                inline=False,
            )
        else:
            embed.add_field(
                name="Preço",
                value=f"R$ {original:.2f} • {original:.2f} créditos",
                inline=True,
            )
    stock = "Ilimitado" if product.stock_quantity is None else str(product.stock_quantity)
    embed.add_field(name="Estoque", value=stock, inline=True)
    if product.image_url:
        embed.set_image(url=product.image_url)
    return embed


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
        async with SessionLocal() as session:
            product = await session.get(Product, self.product_id)
            coupon = await get_coupon_by_code(
                session,
                guild_id=interaction.guild.id,
                code=str(self.code),
            )
        if product is None or product.guild_id != interaction.guild.id or not product.active:
            await interaction.response.send_message("Produto indisponível.", ephemeral=True)
            return
        if coupon is None:
            await interaction.response.send_message(
                "Cupom inválido, desativado ou sem usos disponíveis.",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            embed=build_product_checkout_embed(
                product,
                coupon_code=coupon.code,
                discount_percent=coupon.discount_percent,
            ),
            view=ConfiguredProductCheckoutView(
                product_id=product.id,
                owner_id=self.owner_id,
                coupon_id=coupon.id,
            ),
        )


class ConfiguredProductCheckoutView(discord.ui.View):
    def __init__(self, *, product_id: int, owner_id: int, coupon_id: int | None = None) -> None:
        super().__init__(timeout=300)
        self.product_id = product_id
        self.owner_id = owner_id
        self.coupon_id = coupon_id
        self._lock = asyncio.Lock()
        self._order_id: UUID | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Essa compra pertence a outro cliente.", ephemeral=True)
        return False

    @discord.ui.button(label="Comprar", style=discord.ButtonStyle.success, row=0)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await require_current_terms(interaction, resume_view=self):
            return

        async with self._lock:
            if self._order_id is not None:
                await interaction.edit_original_response(
                    content=f"Essa compra já foi criada: `{str(self._order_id)[:8]}`.",
                    embed=None,
                    view=None,
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
                    await store.pay_order_with_credits(session, order_id=order.id)
                self._order_id = order.id
            except InsufficientCreditsError:
                await interaction.edit_original_response(
                    content="Você não tem créditos suficientes para essa compra.",
                    embed=None,
                    view=None,
                )
                return
            except store.OutOfStockError:
                await interaction.edit_original_response(
                    content="Esse produto ficou sem estoque.", embed=None, view=None
                )
                return
            except ValueError as exc:
                await interaction.edit_original_response(content=str(exc), embed=None, view=None)
                return

        ticket_text = await _finish_paid_order(interaction, order.id)
        coupon_text = f" Cupom `{coupon.code}` aplicado." if coupon is not None else ""
        await interaction.edit_original_response(
            content=(
                f"Compra confirmada. Pedido `{str(order.id)[:8]}` criado.{coupon_text} "
                f"Atendimento: {ticket_text}."
            ),
            embed=None,
            view=None,
        )

    @discord.ui.button(label="Adicionar cupom", style=discord.ButtonStyle.secondary, row=0)
    async def coupon(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            CouponModal(product_id=self.product_id, owner_id=self.owner_id)
        )


class StoreProductSelect(discord.ui.Select):
    def __init__(self, products: list[Product], placeholder: str) -> None:
        options: list[discord.SelectOption] = []
        for product in products[:25]:
            if product.price_credits is None:
                price = "Sob cotação"
            else:
                price = f"R$ {product.price_credits:.2f}"
            stock = "∞" if product.stock_quantity is None else str(product.stock_quantity)
            options.append(
                discord.SelectOption(
                    label=product.name[:100],
                    value=str(product.id),
                    description=f"{price} • estoque {stock}"[:100],
                    emoji=product.emoji or None,
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
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        if self.values[0] == "none":
            await interaction.response.send_message("Nenhum produto disponível agora.", ephemeral=True)
            return
        product_id = int(self.values[0])
        async with SessionLocal() as session:
            product = await session.get(Product, product_id)
        if (
            product is None
            or product.guild_id != interaction.guild.id
            or not product.active
            or product.price_credits is None
            or (product.stock_quantity is not None and product.stock_quantity <= 0)
        ):
            await interaction.response.send_message("Produto indisponível.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_product_checkout_embed(product),
            view=ConfiguredProductCheckoutView(
                product_id=product.id,
                owner_id=interaction.user.id,
            ),
            ephemeral=True,
        )


class StorePanelView(discord.ui.View):
    def __init__(self, *, config: StorePanelConfig, products: list[Product]) -> None:
        super().__init__(timeout=None)
        self.add_item(StoreProductSelect(products, config.product_placeholder))

        topup = discord.ui.Button(
            label=config.topup_label[:80] or "Adicionar créditos",
            style=discord.ButtonStyle.success,
            custom_id="nextbuy:store:configured-topup",
            row=1,
        )
        topup.callback = self._topup
        self.add_item(topup)

        profile = discord.ui.Button(
            label=config.profile_label[:80] or "Meu perfil",
            style=discord.ButtonStyle.secondary,
            custom_id="nextbuy:store:configured-profile",
            row=1,
        )
        profile.callback = self._profile
        self.add_item(profile)

        terms = discord.ui.Button(
            label=config.terms_label[:80] or "Termos",
            style=discord.ButtonStyle.secondary,
            custom_id="nextbuy:store:configured-terms",
            row=1,
        )
        terms.callback = self._terms
        self.add_item(terms)

    async def _topup(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(store.TopUpModal())

    async def _profile(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        async with SessionLocal() as session:
            profile = await get_customer_profile(
                session,
                guild_id=interaction.guild.id,
                discord_user_id=interaction.user.id,
            )
        await interaction.response.send_message(
            embed=build_profile_embed(interaction.user.display_name, profile),
            ephemeral=True,
        )

    async def _terms(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        async with SessionLocal() as session:
            terms = await list_active_terms(session, guild_id=interaction.guild.id)
        if not terms:
            await interaction.response.send_message("Nenhum termo configurado.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Selecione o termo que quer ler:",
            view=store.TermsView(terms),
            ephemeral=True,
        )


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
        embed = build_store_panel_embed(config, len(products))
        view = StorePanelView(config=config, products=products)

        message: discord.Message | None = None
        if config.published_channel_id and config.published_message_id:
            old_channel = interaction.guild.get_channel(config.published_channel_id)
            if isinstance(old_channel, discord.TextChannel):
                try:
                    old_message = await old_channel.fetch_message(config.published_message_id)
                    if old_channel.id == channel.id:
                        await old_message.edit(embed=embed, view=view)
                        message = old_message
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    message = None
        if message is None:
            message = await channel.send(embed=embed, view=view)
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
        embed = build_store_panel_embed(config, len(products))
        view = StorePanelView(config=config, products=products)
        channel_id = config.published_channel_id
        message_id = config.published_message_id

    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return False
    try:
        message = await channel.fetch_message(message_id)
        await message.edit(embed=embed, view=view)
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
