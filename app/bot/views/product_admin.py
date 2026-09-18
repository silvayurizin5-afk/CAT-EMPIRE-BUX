from decimal import Decimal, InvalidOperation

import discord
from sqlalchemy import select

from app.bot.components_v2 import CardLayout, add_action_row, add_select_row
from app.bot.emoji import select_option_emoji
from app.bot.workflows.leaderboard import refresh_leaderboard
from app.db.models import OrderItem, Product
from app.db.session import SessionLocal
from app.services.calculator import format_brl
from app.services.catalog import (
    list_products,
    set_product_active,
    set_product_stock,
    update_product_presentation,
)


def _normalized_product_type(product: Product) -> str:
    return str(product.product_type or "").strip().lower().replace("-", "_").replace(" ", "_")


def _is_gamepass(product: Product) -> bool:
    return _normalized_product_type(product) in {"gamepass", "game_pass"}


def _configured_robux_amount(product: Product) -> int | None:
    if not _is_gamepass(product):
        return None
    raw = dict(product.metadata_json or {}).get("robux_amount")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _product_lines(product: Product) -> list[str]:
    status = "Ativo" if product.active else "Desativado"
    price = format_brl(product.price_credits) if product.price_credits is not None else "Sem preço"
    stock = "Ilimitado" if product.stock_quantity is None else str(product.stock_quantity)
    lines = [
        product.description or "Sem descrição.",
        f"**ID:** `{product.id}`",
        f"**Tipo:** `{product.product_type}`",
        f"**Jogo:** {product.game_name or '—'}",
        f"**Preço:** `{price}`",
        f"**Estoque:** `{stock}`",
        f"**Entrega:** `{product.delivery_mode}`",
        f"**Status:** `{status}`",
        f"**Emoji da loja:** {product.emoji or '—'}",
    ]
    if _is_gamepass(product):
        amount = _configured_robux_amount(product)
        lines.append(
            f"**Valor da Game Pass:** `{amount} Robux`"
            if amount is not None
            else "**Valor da Game Pass:** `não configurado`"
        )
    return lines


def build_product_admin_embed(product: Product) -> discord.Embed:
    """Compatibilidade com chamadas antigas; a tela ativa usa Components V2."""
    return discord.Embed(
        title=f"Produto • {product.name}",
        description="\n".join(_product_lines(product)),
        color=discord.Color.from_rgb(43, 45, 49),
    )


async def _refresh_store_if_needed(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    from app.bot.views.store_panel import refresh_published_store_panel

    await refresh_published_store_panel(interaction.guild)


class ProductPresentationModal(discord.ui.Modal):
    def __init__(self, product: Product) -> None:
        super().__init__(title=f"Editar {product.name[:35]}")
        self.product_id = product.id
        self.price = discord.ui.TextInput(
            label="Preço em reais",
            required=False,
            max_length=20,
            default=str(product.price_credits or ""),
        )
        self.description = discord.ui.TextInput(
            label="Descrição",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=1000,
            default=product.description[:1000],
        )
        self.image_url = discord.ui.TextInput(
            label="URL da imagem/banner",
            required=False,
            max_length=1000,
            default=(product.image_url or "")[:1000],
        )
        self.emoji = discord.ui.TextInput(
            label="Emoji da loja",
            required=False,
            max_length=128,
            default=product.emoji or "",
        )
        self.delivery_mode = discord.ui.TextInput(
            label="Entrega: manual, instant ou scheduled",
            max_length=20,
            default=product.delivery_mode,
        )
        for item in (
            self.price,
            self.description,
            self.image_url,
            self.emoji,
            self.delivery_mode,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_price = str(self.price).strip().replace(",", ".")
        try:
            price = Decimal(raw_price) if raw_price else None
        except InvalidOperation:
            await interaction.response.send_message("Preço inválido.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            product = await session.get(Product, self.product_id)
            if product is None:
                await interaction.edit_original_response(content="Produto não encontrado.")
                return
            try:
                await update_product_presentation(
                    session,
                    product=product,
                    price_credits=price,
                    description=str(self.description),
                    image_url=str(self.image_url),
                    emoji=str(self.emoji),
                    delivery_mode=str(self.delivery_mode),
                )
            except ValueError as exc:
                await interaction.edit_original_response(content=str(exc))
                return

        await _refresh_store_if_needed(interaction)
        await interaction.edit_original_response(
            content="Produto atualizado. O painel publicado também foi atualizado."
        )


class ProductStockModal(discord.ui.Modal, title="Configurar estoque"):
    quantity = discord.ui.TextInput(
        label="Quantidade (vazio = ilimitado)",
        required=False,
        placeholder="Ex: 25",
        max_length=12,
    )

    def __init__(self, product: Product) -> None:
        super().__init__()
        self.product_id = product.id
        self.quantity.default = (
            "" if product.stock_quantity is None else str(product.stock_quantity)
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.quantity).strip()
        try:
            stock = int(raw) if raw else None
            if stock is not None and stock < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "Estoque inválido. Use um número inteiro maior ou igual a zero.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            product = await session.get(Product, self.product_id)
            if product is None:
                await interaction.edit_original_response(content="Produto não encontrado.")
                return
            await set_product_stock(session, product=product, stock_quantity=stock)

        await _refresh_store_if_needed(interaction)
        label = "ilimitado" if stock is None else str(stock)
        await interaction.edit_original_response(content=f"Estoque atualizado para **{label}**.")


class ProductRobuxValueModal(discord.ui.Modal, title="Valor em Robux da Game Pass"):
    def __init__(self, product: Product) -> None:
        super().__init__()
        self.product_id = product.id
        amount = _configured_robux_amount(product)
        self.robux_amount = discord.ui.TextInput(
            label="Valor da Game Pass em Robux",
            placeholder="Ex: 2200",
            required=False,
            max_length=12,
            default=str(amount or ""),
        )
        self.add_item(self.robux_amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        raw = str(self.robux_amount).strip()
        if raw:
            if not raw.isdigit() or int(raw) <= 0:
                await interaction.response.send_message(
                    "Use um número inteiro positivo de Robux, por exemplo `2200`.",
                    ephemeral=True,
                )
                return
            amount: int | None = int(raw)
        else:
            amount = None

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            product = await session.get(Product, self.product_id)
            if product is None or product.guild_id != interaction.guild.id:
                await interaction.edit_original_response(content="Produto não encontrado.")
                return
            if not _is_gamepass(product):
                await interaction.edit_original_response(
                    content=(
                        "Esse campo é usado apenas para produtos do tipo `gamepass`. "
                        "Itens comuns atualizam somente o valor gasto em reais."
                    )
                )
                return

            metadata = dict(product.metadata_json or {})
            backfilled = 0
            if amount is None:
                metadata.pop("robux_amount", None)
            else:
                metadata["robux_amount"] = amount
            product.metadata_json = metadata

            # Pedidos antigos podem ter sido criados antes de existir a configuração
            # robux_amount. Congelamos o valor apenas nos snapshots que ainda não têm
            # essa informação, preservando o histórico se o produto mudar no futuro.
            if amount is not None:
                old_items = list(
                    (
                        await session.scalars(
                            select(OrderItem).where(OrderItem.product_id == product.id)
                        )
                    ).all()
                )
                for item in old_items:
                    item_metadata = dict(item.metadata_json or {})
                    if item_metadata.get("robux_amount") not in (None, "", 0, "0"):
                        continue
                    item_metadata["robux_amount"] = amount
                    item_metadata.setdefault("product_type", product.product_type)
                    item.metadata_json = item_metadata
                    backfilled += 1

            await session.flush()

        await refresh_leaderboard(interaction.guild)
        await interaction.edit_original_response(
            content=(
                (
                    f"Game Pass configurada como **{amount} Robux**. "
                    f"**{backfilled}** pedido(s) antigo(s) sem valor em Robux foram atualizados. "
                    "Perfil e ranking foram recalculados."
                )
                if amount is not None
                else "Valor em Robux removido do produto. Os snapshots históricos foram preservados "
                "e o perfil/ranking foi recalculado."
            )
        )


class ProductActionsView(discord.ui.View):
    def __init__(self, product_id: int) -> None:
        super().__init__(timeout=180)
        self.product_id = product_id

    @discord.ui.button(label="Editar visual/preço", style=discord.ButtonStyle.secondary)
    async def edit(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        async with SessionLocal() as session:
            product = await session.get(Product, self.product_id)
        if product is None:
            await interaction.response.send_message("Produto não encontrado.", ephemeral=True)
            return
        await interaction.response.send_modal(ProductPresentationModal(product))

    @discord.ui.button(label="Estoque", style=discord.ButtonStyle.secondary)
    async def stock(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        async with SessionLocal() as session:
            product = await session.get(Product, self.product_id)
        if product is None:
            await interaction.response.send_message("Produto não encontrado.", ephemeral=True)
            return
        await interaction.response.send_modal(ProductStockModal(product))

    @discord.ui.button(label="Valor em Robux", style=discord.ButtonStyle.secondary)
    async def robux_value(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        async with SessionLocal() as session:
            product = await session.get(Product, self.product_id)
        if product is None:
            await interaction.response.send_message("Produto não encontrado.", ephemeral=True)
            return
        if not _is_gamepass(product):
            await interaction.response.send_message(
                "Itens comuns atualizam apenas o valor gasto em reais. "
                "O valor em Robux é configurado somente para Game Pass.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(ProductRobuxValueModal(product))

    @discord.ui.button(label="Ativar/Desativar", style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            product = await session.get(Product, self.product_id)
            if product is None:
                await interaction.edit_original_response(content="Produto não encontrado.")
                return
            await set_product_active(session, product=product, active=not product.active)
            active = product.active
        await _refresh_store_if_needed(interaction)
        await interaction.edit_original_response(
            content=f"Produto {'ativado' if active else 'desativado'}."
        )


class ProductActionsLayout(discord.ui.LayoutView):
    def __init__(self, product: Product) -> None:
        super().__init__(timeout=180)
        card = CardLayout(
            title=f"Produto • {product.name}",
            lines=_product_lines(product),
            image_url=product.image_url,
            footer="NEXTBUY • Produto",
            timeout=180,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)

        legacy = ProductActionsView(product.id)
        buttons = list(legacy.children)
        for item in buttons:
            legacy.remove_item(item)
        if buttons:
            add_action_row(self.container, *buttons)


class ProductManageSelect(discord.ui.Select):
    def __init__(self, products: list[Product]) -> None:
        options = [
            discord.SelectOption(
                label=product.name[:100],
                value=str(product.id),
                description=(
                    f"{product.product_type} • "
                    f"{'ativo' if product.active else 'desativado'} • "
                    f"estoque {'∞' if product.stock_quantity is None else product.stock_quantity}"
                )[:100],
                emoji=select_option_emoji(product.emoji),
            )
            for product in products[:25]
        ]
        super().__init__(placeholder="Escolha o produto para gerenciar", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        product_id = int(self.values[0])
        async with SessionLocal() as session:
            product = await session.get(Product, product_id)
        if product is None:
            await interaction.edit_original_response(content="Produto não encontrado.", view=None)
            return
        await interaction.edit_original_response(
            content=None,
            embeds=[],
            view=ProductActionsLayout(product),
        )


class ProductManagementView(discord.ui.LayoutView):
    def __init__(self, products: list[Product]) -> None:
        super().__init__(timeout=180)
        card = CardLayout(
            title="Gerenciar produtos",
            description=(
                "Escolha um produto para editar preço, estoque, visual, "
                "valor em Robux ou status."
            ),
            timeout=180,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)
        add_select_row(self.container, ProductManageSelect(products))


async def send_product_management(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    async with SessionLocal() as session:
        products = await list_products(session, guild_id=interaction.guild.id)
    if not products:
        await interaction.edit_original_response(
            content="Ainda não existem produtos. Use **Criar produto** primeiro.",
            embeds=[],
            view=None,
        )
        return
    await interaction.edit_original_response(
        content=None,
        embeds=[],
        view=ProductManagementView(products),
    )
