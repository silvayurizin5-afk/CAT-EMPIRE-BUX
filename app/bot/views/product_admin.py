from decimal import Decimal, InvalidOperation

import discord

from app.db.models import Product
from app.db.session import SessionLocal
from app.services.catalog import (
    list_products,
    set_product_active,
    set_product_stock,
    update_product_presentation,
)


def build_product_admin_embed(product: Product) -> discord.Embed:
    status = "Ativo" if product.active else "Desativado"
    price = (
        f"{product.price_credits:.2f} créditos"
        if product.price_credits is not None
        else "Sob cotação"
    )
    stock = "Ilimitado" if product.stock_quantity is None else str(product.stock_quantity)
    embed = discord.Embed(
        title=f"Produto • {product.name}",
        description=product.description or "Sem descrição.",
    )
    embed.add_field(name="ID", value=str(product.id))
    embed.add_field(name="Tipo", value=product.product_type)
    embed.add_field(name="Jogo", value=product.game_name or "—")
    embed.add_field(name="Preço", value=price)
    embed.add_field(name="Estoque", value=stock)
    embed.add_field(name="Entrega", value=product.delivery_mode)
    embed.add_field(name="Status", value=status)
    embed.add_field(name="Emoji", value=product.emoji or "—")
    if product.image_url:
        embed.set_image(url=product.image_url)
    return embed


class ProductPresentationModal(discord.ui.Modal):
    def __init__(self, product: Product) -> None:
        super().__init__(title=f"Editar {product.name[:35]}")
        self.product_id = product.id
        self.price = discord.ui.TextInput(
            label="Preço em créditos (vazio = cotação)",
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
            label="Emoji",
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

        async with SessionLocal() as session, session.begin():
            product = await session.get(Product, self.product_id)
            if product is None:
                await interaction.response.send_message("Produto não encontrado.", ephemeral=True)
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
                await interaction.response.send_message(str(exc), ephemeral=True)
                return

        await interaction.response.send_message(
            "Produto atualizado. Reabra **Gerenciar produtos** para conferir o resultado.",
            ephemeral=True,
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

        async with SessionLocal() as session, session.begin():
            product = await session.get(Product, self.product_id)
            if product is None:
                await interaction.response.send_message("Produto não encontrado.", ephemeral=True)
                return
            await set_product_stock(session, product=product, stock_quantity=stock)

        label = "ilimitado" if stock is None else str(stock)
        await interaction.response.send_message(
            f"Estoque atualizado para **{label}**.", ephemeral=True
        )


class ProductActionsView(discord.ui.View):
    def __init__(self, product_id: int) -> None:
        super().__init__(timeout=180)
        self.product_id = product_id

    @discord.ui.button(label="Editar visual/preço", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        async with SessionLocal() as session:
            product = await session.get(Product, self.product_id)
        if product is None:
            await interaction.response.send_message("Produto não encontrado.", ephemeral=True)
            return
        await interaction.response.send_modal(ProductPresentationModal(product))

    @discord.ui.button(label="Estoque", style=discord.ButtonStyle.primary)
    async def stock(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        async with SessionLocal() as session:
            product = await session.get(Product, self.product_id)
        if product is None:
            await interaction.response.send_message("Produto não encontrado.", ephemeral=True)
            return
        await interaction.response.send_modal(ProductStockModal(product))

    @discord.ui.button(label="Ativar/Desativar", style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        async with SessionLocal() as session, session.begin():
            product = await session.get(Product, self.product_id)
            if product is None:
                await interaction.response.send_message("Produto não encontrado.", ephemeral=True)
                return
            await set_product_active(session, product=product, active=not product.active)
            active = product.active
        await interaction.response.send_message(
            f"Produto {'ativado' if active else 'desativado'}.", ephemeral=True
        )


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
                emoji=product.emoji or None,
            )
            for product in products[:25]
        ]
        super().__init__(placeholder="Escolha o produto para gerenciar", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        product_id = int(self.values[0])
        async with SessionLocal() as session:
            product = await session.get(Product, product_id)
        if product is None:
            await interaction.response.edit_message(content="Produto não encontrado.", view=None)
            return
        await interaction.response.edit_message(
            content=None,
            embed=build_product_admin_embed(product),
            view=ProductActionsView(product.id),
        )


class ProductManagementView(discord.ui.View):
    def __init__(self, products: list[Product]) -> None:
        super().__init__(timeout=180)
        self.add_item(ProductManageSelect(products))


async def send_product_management(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    async with SessionLocal() as session:
        products = await list_products(session, guild_id=interaction.guild.id)
    if not products:
        await interaction.response.send_message(
            "Ainda não existem produtos. Use **Novo produto** primeiro.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        "Escolha um produto:",
        view=ProductManagementView(products),
        ephemeral=True,
    )
