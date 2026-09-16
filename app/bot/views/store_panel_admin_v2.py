import discord

from app.bot.views.store_panel import (
    build_store_panel_embed,
    refresh_published_store_panel,
)
from app.bot.views.store_panel_admin import STORE_PANEL_ACTIONS, StorePanelAdminView
from app.db.session import SessionLocal
from app.services.calculator import normalize_text
from app.services.store_panel import get_or_create_store_panel, list_store_products


def _http_url_or_none(value: str) -> str | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    if not cleaned.lower().startswith(("http://", "https://")):
        raise ValueError("A URL precisa começar com http:// ou https://")
    return cleaned


async def _preview(guild_id: int) -> discord.Embed:
    async with SessionLocal() as session, session.begin():
        config = await get_or_create_store_panel(session, guild_id)
        products = await list_store_products(session, guild_id=guild_id, config=config)
        return build_store_panel_embed(config, len(products))


class StoreControlsModalV2(discord.ui.Modal, title="Controles do painel"):
    def __init__(self, *, config) -> None:
        super().__init__()
        self.placeholder = discord.ui.TextInput(
            label="Texto do seletor de produtos",
            max_length=100,
            default=config.product_placeholder[:100],
        )
        self.product_count_label = discord.ui.TextInput(
            label="Rótulo da quantidade de produtos",
            max_length=100,
            default=config.product_count_label[:100],
        )
        self.thumbnail = discord.ui.TextInput(
            label="Thumbnail (URL)",
            required=False,
            max_length=1000,
            default=(config.thumbnail_url or "")[:1000],
        )
        for item in (self.placeholder, self.product_count_label, self.thumbnail):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            thumbnail_url = _http_url_or_none(str(self.thumbnail))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.defer()
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_store_panel(session, interaction.guild.id)
            config.product_placeholder = str(self.placeholder).strip() or "Selecione um produto"
            config.product_count_label = (
                str(self.product_count_label).strip() or "Produtos disponíveis"
            )
            config.thumbnail_url = thumbnail_url

        await interaction.edit_original_response(
            content="Controles do painel atualizados.",
            embed=await _preview(interaction.guild.id),
            view=StorePanelAdminViewV2(owner_id=interaction.user.id),
        )
        await refresh_published_store_panel(interaction.guild)


class StoreCheckoutModal(discord.ui.Modal, title="Tela antes do pagamento"):
    def __init__(self, *, config) -> None:
        super().__init__()
        self.title_template = discord.ui.TextInput(
            label="Título: {emoji} {product} {game}",
            max_length=256,
            default=config.checkout_title_template[:256],
        )
        self.description = discord.ui.TextInput(
            label="Descrição da confirmação",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1500,
            default=config.checkout_description[:1500],
        )
        self.buy_label = discord.ui.TextInput(
            label="Texto do botão de compra",
            max_length=80,
            default=config.buy_button_label[:80],
        )
        self.coupon_label = discord.ui.TextInput(
            label="Texto do botão de cupom",
            max_length=80,
            default=config.coupon_button_label[:80],
        )
        for item in (
            self.title_template,
            self.description,
            self.buy_label,
            self.coupon_label,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer()
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_store_panel(session, interaction.guild.id)
            config.checkout_title_template = (
                str(self.title_template).strip() or "{emoji} {product}"
            )
            config.checkout_description = str(self.description).strip()
            config.buy_button_label = str(self.buy_label).strip() or "Comprar"
            config.coupon_button_label = str(self.coupon_label).strip() or "Adicionar cupom"
        await interaction.edit_original_response(
            content=(
                "Tela de confirmação atualizada. Ela não mostra estoque; "
                "o estoque continua visível no seletor dos produtos quando aplicável."
            ),
            embed=await _preview(interaction.guild.id),
            view=StorePanelAdminViewV2(owner_id=interaction.user.id),
        )


class GameIconModal(discord.ui.Modal, title="Ícone do jogo no cálculo"):
    game_name = discord.ui.TextInput(
        label="Nome do jogo",
        placeholder="Ex: Blox Fruits",
        max_length=120,
    )
    emoji = discord.ui.TextInput(
        label="Emoji do jogo",
        placeholder="Ex: <:blox:123456789012345678>",
        required=False,
        max_length=128,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        game = str(self.game_name).strip()
        key = normalize_text(game)
        if not key:
            await interaction.response.send_message("Informe o nome do jogo.", ephemeral=True)
            return
        emoji = str(self.emoji).strip()
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_store_panel(session, interaction.guild.id)
            icons = dict(config.game_icons or {})
            if emoji:
                icons[key] = emoji
            else:
                icons.pop(key, None)
            config.game_icons = icons
        action = "configurado" if emoji else "removido"
        await interaction.edit_original_response(
            content=f"Ícone de **{game}** {action} para os cálculos."
        )


_BASE_ACTIONS = tuple(item for item in STORE_PANEL_ACTIONS if item[0] != "controls")
STORE_PANEL_ACTIONS_V2 = (
    _BASE_ACTIONS[0],
    ("controls", "Visual e seletor", "Texto do seletor, contador e thumbnail"),
    ("checkout", "Tela de confirmação", "Título, texto e botões antes do pagamento"),
    *_BASE_ACTIONS[1:-1],
    (
        "game_icons",
        "Ícones dos jogos",
        "Emoji do jogo usado somente nos cálculos",
    ),
    _BASE_ACTIONS[-1],
)


class StorePanelActionSelectV2(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="O que deseja configurar na loja?",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=label, value=value, description=description)
                for value, label, description in STORE_PANEL_ACTIONS_V2
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if isinstance(self.view, StorePanelAdminViewV2):
            await self.view.handle_action(interaction, self.values[0])


class StorePanelAdminViewV2(StorePanelAdminView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id=owner_id)
        self.clear_items()
        self.add_item(StorePanelActionSelectV2())

    async def handle_action(self, interaction: discord.Interaction, action: str) -> None:
        if interaction.guild is None:
            return
        if action == "game_icons":
            await interaction.response.send_modal(GameIconModal())
            return
        if action in {"controls", "checkout"}:
            async with SessionLocal() as session, session.begin():
                config = await get_or_create_store_panel(session, interaction.guild.id)
                modal = (
                    StoreControlsModalV2(config=config)
                    if action == "controls"
                    else StoreCheckoutModal(config=config)
                )
            await interaction.response.send_modal(modal)
            return
        await super().handle_action(interaction, action)


async def send_store_panel_admin(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    await interaction.response.defer()
    embed = await _preview(interaction.guild.id)
    await interaction.edit_original_response(
        content=(
            "Configure toda a loja por este seletor. O painel público usa Components V2; "
            "a prévia abaixo mostra o conteúdo configurado."
        ),
        embed=embed,
        view=StorePanelAdminViewV2(owner_id=interaction.user.id),
    )


__all__ = ["StorePanelAdminViewV2", "send_store_panel_admin"]
