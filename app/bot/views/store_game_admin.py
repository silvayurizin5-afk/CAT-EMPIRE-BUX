from __future__ import annotations

import discord

from app.bot.components_v2 import CardLayout, add_action_row
from app.bot.emoji import resolve_guild_emoji_aliases, select_option_emoji
from app.bot.views.store_games import (
    game_lookup_name,
    game_panel_selected_ids,
    is_game_product,
    product_store_metadata,
)
from app.db.models import Product
from app.db.session import SessionLocal
from app.services.catalog import list_products


def _http_url_or_none(value: str) -> str | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    if not cleaned.lower().startswith(("http://", "https://")):
        raise ValueError("A URL precisa começar com http:// ou https://")
    return cleaned


async def _refresh_store(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    from app.bot.views.store_panel import refresh_published_store_panel

    await refresh_published_store_panel(interaction.guild)


class ProductStoreDisplayModal(discord.ui.Modal):
    def __init__(self, product: Product) -> None:
        super().__init__(title=f"Exibição • {product.name[:30]}")
        self.product_id = product.id
        metadata = product_store_metadata(product)
        self.selector_description = discord.ui.TextInput(
            label="Descrição no seletor (vazio = automática)",
            required=False,
            max_length=100,
            default=str(metadata.get("store_selector_description") or "")[:100],
        )
        show_stock = metadata.get("store_show_stock", True)
        self.show_stock = discord.ui.TextInput(
            label="Mostrar estoque? sim/não",
            max_length=5,
            default="sim" if bool(show_stock) else "não",
        )
        self.add_item(self.selector_description)
        self.add_item(self.show_stock)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        raw_show = str(self.show_stock).strip().lower()
        if raw_show not in {"sim", "s", "yes", "y", "1", "não", "nao", "n", "no", "0"}:
            await interaction.response.send_message(
                "Em **Mostrar estoque?** use `sim` ou `não`.",
                ephemeral=True,
            )
            return
        show_stock = raw_show in {"sim", "s", "yes", "y", "1"}

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            product = await session.get(Product, self.product_id)
            if product is None or product.guild_id != interaction.guild.id:
                await interaction.edit_original_response(content="Produto não encontrado.")
                return
            metadata = product_store_metadata(product)
            description = str(self.selector_description).strip()
            if description:
                metadata["store_selector_description"] = description
            else:
                metadata.pop("store_selector_description", None)
            metadata["store_show_stock"] = show_stock
            product.metadata_json = metadata

        await _refresh_store(interaction)
        await interaction.edit_original_response(
            content=(
                "Exibição do seletor atualizada. "
                + ("O estoque será exibido." if show_stock else "O estoque ficará oculto.")
            )
        )


class GamePanelVisualModal(discord.ui.Modal):
    def __init__(self, product: Product) -> None:
        super().__init__(title=f"Visual • {product.name[:32]}")
        self.product_id = product.id
        metadata = product_store_metadata(product)
        self.title_input = discord.ui.TextInput(
            label="Título do subpainel",
            max_length=256,
            default=str(metadata.get("game_panel_title") or product.name)[:256],
        )
        self.description_input = discord.ui.TextInput(
            label="Descrição",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1800,
            default=str(
                metadata.get("game_panel_description") or product.description or ""
            )[:1800],
        )
        self.image_input = discord.ui.TextInput(
            label="Banner / imagem (URL)",
            required=False,
            max_length=1000,
            default=str(metadata.get("game_panel_image_url") or "")[:1000],
        )
        self.thumbnail_input = discord.ui.TextInput(
            label="Thumbnail (URL)",
            required=False,
            max_length=1000,
            default=str(metadata.get("game_panel_thumbnail_url") or "")[:1000],
        )
        self.footer_input = discord.ui.TextInput(
            label="Rodapé",
            required=False,
            max_length=1000,
            default=str(metadata.get("game_panel_footer_text") or "")[:1000],
        )
        for item in (
            self.title_input,
            self.description_input,
            self.image_input,
            self.thumbnail_input,
            self.footer_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            image_url = _http_url_or_none(str(self.image_input))
            thumbnail_url = _http_url_or_none(str(self.thumbnail_input))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            product = await session.get(Product, self.product_id)
            if product is None or product.guild_id != interaction.guild.id:
                await interaction.edit_original_response(content="Jogo não encontrado.")
                return
            metadata = product_store_metadata(product)
            metadata["game_panel_title"] = resolve_guild_emoji_aliases(
                str(self.title_input).strip() or product.name,
                interaction.guild,
            )
            metadata["game_panel_description"] = resolve_guild_emoji_aliases(
                str(self.description_input).strip(),
                interaction.guild,
            )
            if image_url:
                metadata["game_panel_image_url"] = image_url
            else:
                metadata.pop("game_panel_image_url", None)
            if thumbnail_url:
                metadata["game_panel_thumbnail_url"] = thumbnail_url
            else:
                metadata.pop("game_panel_thumbnail_url", None)
            footer = resolve_guild_emoji_aliases(
                str(self.footer_input).strip(),
                interaction.guild,
            )
            if footer:
                metadata["game_panel_footer_text"] = footer
            else:
                metadata.pop("game_panel_footer_text", None)
            product.metadata_json = metadata

        await _refresh_store(interaction)
        await interaction.edit_original_response(content="Visual do subpainel atualizado.")


class GamePanelControlsModal(discord.ui.Modal):
    def __init__(self, product: Product) -> None:
        super().__init__(title=f"Controles • {product.name[:28]}")
        self.product_id = product.id
        metadata = product_store_metadata(product)
        self.placeholder = discord.ui.TextInput(
            label="Texto do seletor",
            max_length=100,
            default=str(
                metadata.get("game_panel_placeholder")
                or f"Selecione uma opção de {product.name}"
            )[:100],
        )
        self.count_label = discord.ui.TextInput(
            label="Rótulo da quantidade (vazio = ocultar)",
            required=False,
            max_length=100,
            default=str(metadata.get("game_panel_product_count_label") or "")[:100],
        )
        self.add_item(self.placeholder)
        self.add_item(self.count_label)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            product = await session.get(Product, self.product_id)
            if product is None or product.guild_id != interaction.guild.id:
                await interaction.edit_original_response(content="Jogo não encontrado.")
                return
            metadata = product_store_metadata(product)
            metadata["game_panel_placeholder"] = (
                str(self.placeholder).strip() or f"Selecione uma opção de {product.name}"
            )
            count_label = resolve_guild_emoji_aliases(
                str(self.count_label).strip(),
                interaction.guild,
            )
            if count_label:
                metadata["game_panel_product_count_label"] = count_label
            else:
                metadata.pop("game_panel_product_count_label", None)
            product.metadata_json = metadata

        await _refresh_store(interaction)
        await interaction.edit_original_response(content="Controles do subpainel atualizados.")


class GameProductsSelect(discord.ui.Select):
    def __init__(self, game_product: Product, products: list[Product]) -> None:
        selected_ids = set(game_panel_selected_ids(game_product))
        candidates = [
            product
            for product in products
            if product.id != game_product.id and not is_game_product(product)
        ][:24]
        options = [
            discord.SelectOption(
                label="Automático pelo campo Jogo",
                value="auto",
                description=f"Usa produtos com Jogo = {game_lookup_name(game_product)}"[:100],
                default=not selected_ids,
            )
        ]
        for product in candidates:
            options.append(
                discord.SelectOption(
                    label=product.name[:100],
                    value=str(product.id),
                    description=(
                        f"{product.product_type} • "
                        f"{product.game_name or 'sem jogo'} • "
                        f"{'ativo' if product.active else 'desativado'}"
                    )[:100],
                    emoji=select_option_emoji(product.emoji),
                    default=product.id in selected_ids,
                )
            )

        super().__init__(
            placeholder="Escolha os produtos do subpainel",
            min_values=1,
            max_values=len(options),
            options=options,
        )
        self.game_product_id = game_product.id

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        selected_ids = (
            []
            if "auto" in self.values
            else [int(value) for value in self.values if value != "auto"]
        )
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            product = await session.get(Product, self.game_product_id)
            if product is None or product.guild_id != interaction.guild.id:
                await interaction.edit_original_response(content="Jogo não encontrado.")
                return
            metadata = product_store_metadata(product)
            if selected_ids:
                metadata["game_panel_product_ids"] = selected_ids[:24]
            else:
                metadata.pop("game_panel_product_ids", None)
            product.metadata_json = metadata

        await _refresh_store(interaction)
        await interaction.edit_original_response(
            content=(
                f"**{len(selected_ids)}** produto(s) definidos manualmente."
                if selected_ids
                else "Subpainel em modo automático pelo campo **Jogo**."
            ),
            view=None,
        )


class GameProductsSelectionView(discord.ui.View):
    def __init__(self, game_product: Product, products: list[Product]) -> None:
        super().__init__(timeout=300)
        self.add_item(GameProductsSelect(game_product, products))


class GamePanelAdminLayout(discord.ui.LayoutView):
    def __init__(self, product: Product, *, owner_id: int) -> None:
        super().__init__(timeout=300)
        self.product_id = product.id
        self.owner_id = owner_id
        metadata = product_store_metadata(product)
        mode = (
            f"{len(game_panel_selected_ids(product))} produto(s) manual(is)"
            if game_panel_selected_ids(product)
            else f"Automático: Jogo = {game_lookup_name(product)}"
        )
        card = CardLayout(
            title=f"Subpainel • {product.name}",
            description=(
                "Configure o painel ephemeral aberto quando este jogo for selecionado "
                "na loja."
            ),
            lines=[
                f"**Produtos:** {mode}",
                f"**Título:** {metadata.get('game_panel_title') or product.name}",
            ],
            footer="NEXTBUY • Subpainel de jogo",
            timeout=300,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)

        display = discord.ui.Button(label="Exibição na loja", style=discord.ButtonStyle.secondary)
        visual = discord.ui.Button(label="Visual do subpainel", style=discord.ButtonStyle.primary)
        controls = discord.ui.Button(label="Controles", style=discord.ButtonStyle.secondary)
        products = discord.ui.Button(label="Produtos", style=discord.ButtonStyle.success)
        display.callback = self._display
        visual.callback = self._visual
        controls.callback = self._controls
        products.callback = self._products
        add_action_row(self.container, display, visual, controls, products)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Esse painel pertence a outra pessoa.", ephemeral=True)
        return False

    async def _load(self, interaction: discord.Interaction) -> Product | None:
        if interaction.guild is None:
            return None
        async with SessionLocal() as session:
            product = await session.get(Product, self.product_id)
        if product is None or product.guild_id != interaction.guild.id:
            return None
        return product

    async def _display(self, interaction: discord.Interaction) -> None:
        product = await self._load(interaction)
        if product is None:
            await interaction.response.send_message("Jogo não encontrado.", ephemeral=True)
            return
        await interaction.response.send_modal(ProductStoreDisplayModal(product))

    async def _visual(self, interaction: discord.Interaction) -> None:
        product = await self._load(interaction)
        if product is None:
            await interaction.response.send_message("Jogo não encontrado.", ephemeral=True)
            return
        await interaction.response.send_modal(GamePanelVisualModal(product))

    async def _controls(self, interaction: discord.Interaction) -> None:
        product = await self._load(interaction)
        if product is None:
            await interaction.response.send_message("Jogo não encontrado.", ephemeral=True)
            return
        await interaction.response.send_modal(GamePanelControlsModal(product))

    async def _products(self, interaction: discord.Interaction) -> None:
        product = await self._load(interaction)
        if product is None or interaction.guild is None:
            await interaction.response.send_message("Jogo não encontrado.", ephemeral=True)
            return
        async with SessionLocal() as session:
            products = await list_products(session, guild_id=interaction.guild.id)
        await interaction.response.send_message(
            "Escolha **Automático** para o bot reconhecer pelo campo Jogo, "
            "ou selecione manualmente os produtos deste subpainel.",
            view=GameProductsSelectionView(product, products),
            ephemeral=True,
        )


async def open_store_product_configuration(
    interaction: discord.Interaction,
    *,
    product_id: int,
) -> None:
    if interaction.guild is None:
        return
    async with SessionLocal() as session:
        product = await session.get(Product, product_id)
    if product is None or product.guild_id != interaction.guild.id:
        await interaction.response.send_message("Produto não encontrado.", ephemeral=True)
        return
    if is_game_product(product):
        await interaction.response.send_message(
            view=GamePanelAdminLayout(product, owner_id=interaction.user.id),
            ephemeral=True,
        )
        return
    await interaction.response.send_modal(ProductStoreDisplayModal(product))
