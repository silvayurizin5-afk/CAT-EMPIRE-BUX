import discord

from app.bot.emoji import resolve_guild_emoji_aliases
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


class StoreControlsModalV2(discord.ui.Modal, title="Controles do painel"):
    def __init__(self, *, config) -> None:
        super().__init__()
        self.placeholder = discord.ui.TextInput(
            label="Texto do seletor de produtos",
            max_length=100,
            default=config.product_placeholder[:100],
        )
        self.product_count = discord.ui.TextInput(
            label="Rótulo da quantidade (vazio = ocultar)",
            required=False,
            max_length=100,
            default=(config.product_count_label or "")[:100],
        )
        for item in (self.placeholder, self.product_count):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return

        await interaction.response.defer()
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_store_panel(session, interaction.guild.id)
            config.product_placeholder = str(self.placeholder).strip() or "Selecione um produto"
            config.product_count_label = resolve_guild_emoji_aliases(
                str(self.product_count).strip(), interaction.guild
            )

        async with SessionLocal() as session, session.begin():
            config = await get_or_create_store_panel(session, interaction.guild.id)
            products = await list_store_products(
                session,
                guild_id=interaction.guild.id,
                config=config,
            )
            embed = build_store_panel_embed(config, len(products))
        await interaction.edit_original_response(
            content="Controles atualizados.",
            embed=embed,
            view=StorePanelAdminViewV2(owner_id=interaction.user.id),
        )
        await refresh_published_store_panel(interaction.guild)


class StoreAppearanceModalV2(discord.ui.Modal, title="Visual da loja"):
    def __init__(self, *, config) -> None:
        super().__init__()
        self.title_input = discord.ui.TextInput(
            label="Título (aceita :emoji_do_servidor:)",
            max_length=256,
            default=(config.title or "NEXTBUY")[:256],
        )
        self.description_input = discord.ui.TextInput(
            label="Descrição (aceita markdown e :emoji:)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=4000,
            default=(config.description or "")[:4000],
        )
        self.footer_input = discord.ui.TextInput(
            label="Rodapé (aceita :emoji:)",
            required=False,
            max_length=2048,
            default=(config.footer_text or "")[:2048],
        )
        self.color_input = discord.ui.TextInput(
            label="Cor hexadecimal",
            max_length=9,
            default=f"#{config.color:06X}",
        )
        for item in (
            self.title_input,
            self.description_input,
            self.footer_input,
            self.color_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        from app.bot.views.store_panel_admin import _parse_color

        try:
            color = _parse_color(str(self.color_input))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        title = resolve_guild_emoji_aliases(str(self.title_input).strip(), interaction.guild)
        description = resolve_guild_emoji_aliases(
            str(self.description_input).strip(), interaction.guild
        )
        footer = resolve_guild_emoji_aliases(str(self.footer_input).strip(), interaction.guild)

        await interaction.response.defer()
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_store_panel(session, interaction.guild.id)
            config.title = title or "NEXTBUY"
            config.description = description
            config.footer_text = footer
            config.color = color

        async with SessionLocal() as session, session.begin():
            config = await get_or_create_store_panel(session, interaction.guild.id)
            products = await list_store_products(
                session,
                guild_id=interaction.guild.id,
                config=config,
            )
            embed = build_store_panel_embed(config, len(products))

        await interaction.edit_original_response(
            content=(
                "Visual atualizado. Use `:nome_do_emoji:` para emojis do servidor; "
                "a prévia abaixo já usa a configuração salva."
            ),
            embed=embed,
            view=StorePanelAdminViewV2(owner_id=interaction.user.id),
        )
        await refresh_published_store_panel(interaction.guild)


class StoreMediaModalV2(discord.ui.Modal, title="Imagens da loja"):
    def __init__(self, *, config) -> None:
        super().__init__()
        self.title_image = discord.ui.TextInput(
            label="Foto do título / thumbnail (URL)",
            required=False,
            max_length=1000,
            default=(config.thumbnail_url or "")[:1000],
        )
        self.banner = discord.ui.TextInput(
            label="Banner / imagem grande (URL)",
            required=False,
            max_length=1000,
            default=(config.image_url or "")[:1000],
        )
        self.add_item(self.title_image)
        self.add_item(self.banner)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            title_image_url = _http_url_or_none(str(self.title_image))
            banner_url = _http_url_or_none(str(self.banner))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.defer()
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_store_panel(session, interaction.guild.id)
            config.thumbnail_url = title_image_url
            config.image_url = banner_url

        async with SessionLocal() as session, session.begin():
            config = await get_or_create_store_panel(session, interaction.guild.id)
            products = await list_store_products(
                session,
                guild_id=interaction.guild.id,
                config=config,
            )
            embed = build_store_panel_embed(config, len(products))

        await interaction.edit_original_response(
            content="Imagens atualizadas: foto do título e banner são independentes.",
            embed=embed,
            view=StorePanelAdminViewV2(owner_id=interaction.user.id),
        )
        await refresh_published_store_panel(interaction.guild)


class CheckoutControlsModal(discord.ui.Modal, title="Tela antes do pagamento"):
    def __init__(self, *, config) -> None:
        super().__init__()
        self.title_template = discord.ui.TextInput(
            label="Título: {emoji} {product} {game}",
            max_length=256,
            default=(config.checkout_title_template or "{emoji} {product}")[:256],
        )
        self.description = discord.ui.TextInput(
            label="Descrição",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1500,
            default=(config.checkout_description or "")[:1500],
        )
        self.buy_label = discord.ui.TextInput(
            label="Texto do botão Comprar",
            max_length=80,
            default=(config.buy_button_label or "Comprar")[:80],
        )
        self.coupon_label = discord.ui.TextInput(
            label="Texto do botão Cupom",
            max_length=80,
            default=(config.coupon_button_label or "Adicionar cupom")[:80],
        )
        for item in (self.title_template, self.description, self.buy_label, self.coupon_label):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        title = str(self.title_template).strip() or "{emoji} {product}"
        try:
            title.format(emoji="", product="Produto", game="Jogo")
        except (KeyError, ValueError):
            await interaction.response.send_message(
                "Template inválido. Use somente `{emoji}`, `{product}` e `{game}`.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_store_panel(session, interaction.guild.id)
            config.checkout_title_template = resolve_guild_emoji_aliases(
                title, interaction.guild
            )
            config.checkout_description = resolve_guild_emoji_aliases(
                str(self.description).strip(), interaction.guild
            )
            config.buy_button_label = str(self.buy_label).strip() or "Comprar"
            config.coupon_button_label = str(self.coupon_label).strip() or "Adicionar cupom"
        await interaction.edit_original_response(
            content="Tela de compra atualizada. Ela será usada antes de abrir o ticket de pagamento.",
            embed=None,
            view=StorePanelAdminViewV2(owner_id=interaction.user.id),
        )


class GameIconModal(discord.ui.Modal, title="Ícone do jogo"):
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
            content=f"Ícone de **{game}** {action} para cálculos e entregas."
        )


BASE_ACTIONS = tuple(
    item
    for item in STORE_PANEL_ACTIONS
    if item[0] not in {"controls"}
)
STORE_PANEL_ACTIONS_V2 = (
    (
        "appearance",
        "Visual da loja",
        "Título, descrição, rodapé, cor e emojis por :nome:",
    ),
    (
        "media",
        "Imagens da loja",
        "Foto do título e banner em campos separados",
    ),
    (
        "controls",
        "Painel da loja",
        "Seletor e quantidade de produtos",
    ),
    (
        "checkout",
        "Tela de compra",
        "Título, descrição e botões antes do ticket",
    ),
    *BASE_ACTIONS[1:-1],
    (
        "game_icons",
        "Ícones dos jogos",
        "Emoji do jogo usado nos cálculos e nas entregas",
    ),
    BASE_ACTIONS[-1],
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
        if action in {"appearance", "media", "controls", "checkout"}:
            async with SessionLocal() as session, session.begin():
                config = await get_or_create_store_panel(session, interaction.guild.id)
                if action == "appearance":
                    modal = StoreAppearanceModalV2(config=config)
                elif action == "media":
                    modal = StoreMediaModalV2(config=config)
                elif action == "controls":
                    modal = StoreControlsModalV2(config=config)
                else:
                    modal = CheckoutControlsModal(config=config)
            await interaction.response.send_modal(modal)
            return
        await super().handle_action(interaction, action)


async def send_store_panel_admin(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    async with SessionLocal() as session, session.begin():
        config = await get_or_create_store_panel(session, interaction.guild.id)
        products = await list_store_products(session, guild_id=interaction.guild.id, config=config)
        embed = build_store_panel_embed(config, len(products))
    await interaction.response.send_message(
        content=(
            "Configure toda a loja por este seletor. A prévia administrativa abaixo mostra o "
            "conteúdo; a publicação usa Components V2."
        ),
        embed=embed,
        view=StorePanelAdminViewV2(owner_id=interaction.user.id),
        ephemeral=True,
    )


__all__ = ["StorePanelAdminViewV2", "send_store_panel_admin"]
