import asyncio
from decimal import Decimal, InvalidOperation
from uuid import UUID

import discord

from app.bot.views.profile import build_profile_embed
from app.bot.workflows.leaderboard import refresh_leaderboard
from app.bot.workflows.ranks import sync_customer_roles
from app.bot.workflows.tickets import open_order_ticket
from app.core.money import money
from app.db.models import Product, RobuxRate, TermsDocument
from app.db.session import SessionLocal
from app.integrations.mercado_pago import MercadoPagoClient, MercadoPagoError
from app.services.catalog import list_active_products, list_active_terms, list_product_types
from app.services.orders import create_product_order, create_robux_order, pay_order_with_credits
from app.services.profiles import get_customer_profile
from app.services.quotes import list_active_robux_rates
from app.services.topups import create_topup
from app.services.users import get_or_create_user
from app.services.wallets import InsufficientCreditsError


async def _finish_paid_order(interaction: discord.Interaction, order_id: UUID) -> str:
    if interaction.guild is None:
        return "ticket pendente"
    member = interaction.guild.get_member(interaction.user.id)
    if member is None:
        try:
            member = await interaction.guild.fetch_member(interaction.user.id)
        except discord.NotFound:
            member = None
    if member is not None:
        await sync_customer_roles(member)
    await refresh_leaderboard(interaction.guild)
    ticket = await open_order_ticket(interaction, order_id=order_id)
    return ticket.mention if ticket is not None else "ticket pendente de configuração"


class CheckoutLinkView(discord.ui.View):
    def __init__(self, url: str) -> None:
        super().__init__(timeout=900)
        self.add_item(discord.ui.Button(label="Pagar no Mercado Pago", url=url))


class TopUpModal(discord.ui.Modal, title="Adicionar créditos"):
    amount = discord.ui.TextInput(
        label="Valor em reais",
        placeholder="Ex: 10,80",
        min_length=1,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            amount = Decimal(str(self.amount).replace(",", "."))
            if amount <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            await interaction.response.send_message("Valor inválido.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with SessionLocal() as session, session.begin():
                user = await get_or_create_user(session, interaction.user.id)
                topup = await create_topup(
                    session,
                    user_id=user.id,
                    guild_id=interaction.guild.id,
                    amount_brl=amount,
                    mercado_pago=MercadoPagoClient(),
                )
        except MercadoPagoError:
            await interaction.followup.send(
                "Não consegui gerar o pagamento agora. A configuração do Mercado Pago precisa ser revisada.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Recarga criada: **R$ {topup.amount_brl:.2f} → {topup.credits_amount:.2f} créditos**.\n"
            "O saldo só entra depois que o Mercado Pago confirmar o pagamento.",
            view=CheckoutLinkView(topup.checkout_url or "https://www.mercadopago.com.br/"),
            ephemeral=True,
        )


class ConfirmPurchaseView(discord.ui.View):
    def __init__(self, product_id: int) -> None:
        super().__init__(timeout=180)
        self.product_id = product_id
        self._lock = asyncio.Lock()
        self._order_id: UUID | None = None

    @discord.ui.button(label="Confirmar compra", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
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
                        await interaction.edit_original_response(
                            content="Produto não encontrado.", embed=None, view=None
                        )
                        return
                    order = await create_product_order(
                        session,
                        guild_id=interaction.guild.id,
                        user_id=user.id,
                        product=product,
                    )
                    await pay_order_with_credits(session, order_id=order.id)
                self._order_id = order.id
            except InsufficientCreditsError:
                await interaction.edit_original_response(
                    content=(
                        "Você não tem créditos suficientes. Use **Adicionar créditos** "
                        "no painel da loja."
                    ),
                    embed=None,
                    view=None,
                )
                return

        ticket_text = await _finish_paid_order(interaction, order.id)
        await interaction.edit_original_response(
            content=(
                f"Compra confirmada. Pedido `{str(order.id)[:8]}` criado. "
                f"Atendimento: {ticket_text}."
            ),
            embed=None,
            view=None,
        )


class ConfirmRobuxPurchaseView(discord.ui.View):
    def __init__(self, *, rate_id: int, robux: int) -> None:
        super().__init__(timeout=180)
        self.rate_id = rate_id
        self.robux = robux
        self._lock = asyncio.Lock()
        self._order_id: UUID | None = None

    @discord.ui.button(label="Comprar com créditos", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
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
                    rate = await session.get(RobuxRate, self.rate_id)
                    if rate is None or rate.guild_id != interaction.guild.id or not rate.active:
                        await interaction.edit_original_response(
                            content="Essa cotação não está mais disponível.", embed=None, view=None
                        )
                        return
                    order = await create_robux_order(
                        session,
                        guild_id=interaction.guild.id,
                        user_id=user.id,
                        rate=rate,
                        robux=self.robux,
                    )
                    await pay_order_with_credits(session, order_id=order.id)
                self._order_id = order.id
            except InsufficientCreditsError:
                await interaction.edit_original_response(
                    content=(
                        "Saldo insuficiente para essa quantidade de Robux. "
                        "Adicione créditos e tente de novo."
                    ),
                    embed=None,
                    view=None,
                )
                return
            except ValueError as exc:
                await interaction.edit_original_response(content=str(exc), embed=None, view=None)
                return

        ticket_text = await _finish_paid_order(interaction, order.id)
        await interaction.edit_original_response(
            content=(
                f"Compra de **{self.robux} Robux** confirmada. "
                f"Pedido `{str(order.id)[:8]}`. Atendimento: {ticket_text}."
            ),
            embed=None,
            view=None,
        )


class RobuxAmountModal(discord.ui.Modal, title="Quantidade de Robux"):
    amount = discord.ui.TextInput(
        label="Quantidade",
        placeholder="Ex: 380",
        min_length=1,
        max_length=7,
    )

    def __init__(self, rate_id: int) -> None:
        super().__init__()
        self.rate_id = rate_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            robux = int(str(self.amount).strip())
            if robux <= 0 or robux > 1_000_000:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "Digite uma quantidade inteira de Robux entre 1 e 1.000.000.",
                ephemeral=True,
            )
            return

        async with SessionLocal() as session:
            rate = await session.get(RobuxRate, self.rate_id)
        if rate is None or rate.guild_id != interaction.guild.id or not rate.active:
            await interaction.response.send_message("Cotação indisponível.", ephemeral=True)
            return

        total = money(Decimal(robux) * rate.price_per_robux)
        if total <= 0:
            await interaction.response.send_message("Valor calculado inválido.", ephemeral=True)
            return
        embed = discord.Embed(
            title=f"{rate.label} • {robux} Robux",
            description="Confira antes de descontar seus créditos.",
        )
        embed.add_field(name="Total", value=f"{total:.2f} créditos (R$ {total:.2f})")
        if rate.delivery_label:
            embed.add_field(name="Entrega", value=rate.delivery_label, inline=False)
        await interaction.response.send_message(
            embed=embed,
            view=ConfirmRobuxPurchaseView(rate_id=rate.id, robux=robux),
            ephemeral=True,
        )


class RobuxRateSelect(discord.ui.Select):
    def __init__(self, rates: list[RobuxRate]) -> None:
        options = [
            discord.SelectOption(
                label=rate.label[:100],
                value=str(rate.id),
                description=(
                    f"R$ {rate.price_per_robux:.6f} por Robux"
                    + (f" • {rate.delivery_label}" if rate.delivery_label else "")
                )[:100],
            )
            for rate in rates[:25]
        ]
        super().__init__(placeholder="Escolha a forma de entrega", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(RobuxAmountModal(int(self.values[0])))


class RobuxRateSelectView(discord.ui.View):
    def __init__(self, rates: list[RobuxRate]) -> None:
        super().__init__(timeout=180)
        self.add_item(RobuxRateSelect(rates))


class ProductSelect(discord.ui.Select):
    def __init__(self, products: list[Product]) -> None:
        options = [
            discord.SelectOption(
                label=product.name[:100],
                value=str(product.id),
                description=(product.game_name or product.product_type)[:100],
                emoji=product.emoji or None,
            )
            for product in products[:25]
        ]
        super().__init__(placeholder="Escolha o produto", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        product_id = int(self.values[0])
        async with SessionLocal() as session:
            product = await session.get(Product, product_id)
        if product is None:
            await interaction.response.send_message("Produto indisponível.", ephemeral=True)
            return
        price = (
            f"{product.price_credits:.2f} créditos"
            if product.price_credits is not None
            else "Sob cotação"
        )
        embed = discord.Embed(
            title=product.name,
            description=product.description or "Confira os dados antes de comprar.",
        )
        embed.add_field(name="Jogo", value=product.game_name or "—")
        embed.add_field(name="Preço", value=price)
        if product.image_url:
            embed.set_image(url=product.image_url)
        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=ConfirmPurchaseView(product.id),
        )


class ProductSelectView(discord.ui.View):
    def __init__(self, products: list[Product]) -> None:
        super().__init__(timeout=180)
        self.add_item(ProductSelect(products))


class ProductTypeSelect(discord.ui.Select):
    def __init__(self, product_types: list[str]) -> None:
        labels = {
            "item": "Itens",
            "robux": "Robux",
            "gamepass": "Game Pass",
            "robux_custom": "Robux por quantidade",
        }
        options = [
            discord.SelectOption(label=labels.get(kind, kind.title()), value=kind)
            for kind in product_types[:25]
        ]
        super().__init__(placeholder="O que você quer comprar?", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        selected = self.values[0]
        if selected == "robux_custom":
            async with SessionLocal() as session:
                rates = await list_active_robux_rates(session, guild_id=interaction.guild.id)
            if not rates:
                await interaction.response.edit_message(
                    content="Nenhuma cotação de Robux disponível agora.", view=None
                )
                return
            await interaction.response.edit_message(
                content="Escolha a forma de entrega:",
                view=RobuxRateSelectView(rates),
            )
            return

        async with SessionLocal() as session:
            products = await list_active_products(
                session,
                guild_id=interaction.guild.id,
                product_type=selected,
            )
        if not products:
            await interaction.response.edit_message(
                content="Nenhum produto disponível nessa categoria.", view=None
            )
            return
        await interaction.response.edit_message(
            content="Escolha o produto:", view=ProductSelectView(products)
        )


class ProductTypeView(discord.ui.View):
    def __init__(self, product_types: list[str]) -> None:
        super().__init__(timeout=180)
        self.add_item(ProductTypeSelect(product_types))


class TermsSelect(discord.ui.Select):
    def __init__(self, terms: list[TermsDocument]) -> None:
        options = [
            discord.SelectOption(
                label=item.title[:100], value=str(item.id), emoji=item.emoji or None
            )
            for item in terms[:25]
        ]
        super().__init__(placeholder="Escolha um termo", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        terms_id = int(self.values[0])
        async with SessionLocal() as session:
            terms = await session.get(TermsDocument, terms_id)
        if terms is None:
            await interaction.response.send_message("Termo não encontrado.", ephemeral=True)
            return
        embed = discord.Embed(title=terms.title, description=terms.content)
        embed.set_footer(text=f"Versão {terms.version}")
        await interaction.response.edit_message(embed=embed, view=self.view)


class TermsView(discord.ui.View):
    def __init__(self, terms: list[TermsDocument]) -> None:
        super().__init__(timeout=180)
        self.add_item(TermsSelect(terms))


class StoreHomeView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Comprar",
        style=discord.ButtonStyle.primary,
        custom_id="nextbuy:store:buy",
    )
    async def buy(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        async with SessionLocal() as session:
            product_types = await list_product_types(session, guild_id=interaction.guild.id)
            rates = await list_active_robux_rates(session, guild_id=interaction.guild.id, limit=1)
        if rates and "robux_custom" not in product_types:
            product_types.append("robux_custom")
        if not product_types:
            await interaction.response.send_message(
                "A loja ainda não tem produtos ou cotações ativas.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Escolha a categoria:", view=ProductTypeView(product_types), ephemeral=True
        )

    @discord.ui.button(
        label="Adicionar créditos",
        style=discord.ButtonStyle.success,
        custom_id="nextbuy:store:topup",
    )
    async def topup(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(TopUpModal())

    @discord.ui.button(
        label="Meu perfil",
        style=discord.ButtonStyle.secondary,
        custom_id="nextbuy:store:profile",
    )
    async def profile(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
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

    @discord.ui.button(
        label="Termos",
        style=discord.ButtonStyle.secondary,
        custom_id="nextbuy:store:terms",
    )
    async def terms(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        async with SessionLocal() as session:
            terms = await list_active_terms(session, guild_id=interaction.guild.id)
        if not terms:
            await interaction.response.send_message("Nenhum termo configurado.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Selecione o termo que quer ler:", view=TermsView(terms), ephemeral=True
        )
