from __future__ import annotations

import discord

from app.bot.emoji import select_option_emoji
from app.bot.views.game_catalog import (
    build_game_catalog_embed,
    publish_game_catalog_panel,
    refresh_published_game_catalog_panel,
)
from app.bot.views.store_panel_admin import _parse_color
from app.db.models import Product
from app.db.session import SessionLocal
from app.services.game_catalog import (
    get_or_create_game_catalog,
    list_all_game_products,
    list_catalog_games,
    save_catalog_game_selection,
)


def _http_url_or_none(value: str) -> str | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    if not cleaned.lower().startswith(("http://", "https://")):
        raise ValueError("A URL precisa começar com http:// ou https://")
    return cleaned


async def _preview(guild: discord.Guild) -> discord.Embed:
    async with SessionLocal() as session, session.begin():
        config = await get_or_create_game_catalog(session, guild.id)
        games = await list_catalog_games(session, guild_id=guild.id, config=config)
    return build_game_catalog_embed(config, len(games))


class GameCatalogAppearanceModal(discord.ui.Modal, title="Catálogo • visual principal"):
    def __init__(self, config) -> None:
        super().__init__()
        self.title_input = discord.ui.TextInput(
            label="Título",
            max_length=256,
            default=(config.title or "")[:256],
        )
        self.status_text = discord.ui.TextInput(
            label="Texto do status (vazio = ocultar)",
            required=False,
            max_length=160,
            default=(config.status_text or "")[:160],
        )
        self.status_emoji = discord.ui.TextInput(
            label="Emoji do status (vazio = ocultar)",
            required=False,
            max_length=128,
            default=(config.status_emoji or "")[:128],
        )
        self.description = discord.ui.TextInput(
            label="Descrição",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=3000,
            default=(config.description or "")[:3000],
        )
        self.color = discord.ui.TextInput(
            label="Cor hexadecimal",
            max_length=9,
            default=f"#{config.color:06X}",
        )
        for item in (
            self.title_input,
            self.status_text,
            self.status_emoji,
            self.description,
            self.color,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            color = _parse_color(str(self.color))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_game_catalog(session, interaction.guild.id)
            config.title = str(self.title_input).strip() or "NEXTBUY"
            config.status_text = str(self.status_text).strip()
            config.status_emoji = str(self.status_emoji).strip()
            config.description = str(self.description).strip()
            config.color = color

        await refresh_published_game_catalog_panel(interaction.guild)
        await interaction.edit_original_response(
            content="Visual principal atualizado.",
            embed=await _preview(interaction.guild),
            view=GameCatalogAdminView(interaction.user.id),
        )


class GameCatalogMediaModal(discord.ui.Modal, title="Catálogo • mídia e rodapé"):
    def __init__(self, config) -> None:
        super().__init__()
        self.banner = discord.ui.TextInput(
            label="Banner / imagem grande (URL)",
            required=False,
            max_length=1000,
            default=(config.image_url or "")[:1000],
        )
        self.thumbnail = discord.ui.TextInput(
            label="Thumbnail / logo (URL)",
            required=False,
            max_length=1000,
            default=(config.thumbnail_url or "")[:1000],
        )
        self.footer = discord.ui.TextInput(
            label="Rodapé ({count} = quantidade de jogos)",
            required=False,
            max_length=2048,
            default=(config.footer_text or "")[:2048],
        )
        self.add_item(self.banner)
        self.add_item(self.thumbnail)
        self.add_item(self.footer)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            banner = _http_url_or_none(str(self.banner))
            thumbnail = _http_url_or_none(str(self.thumbnail))
            footer = str(self.footer).strip()
            footer.format(count=1)
        except (ValueError, KeyError):
            await interaction.response.send_message(
                "URL inválida ou rodapé com variável inválida. Use somente {count}.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_game_catalog(session, interaction.guild.id)
            config.image_url = banner
            config.thumbnail_url = thumbnail
            config.footer_text = footer

        await refresh_published_game_catalog_panel(interaction.guild)
        await interaction.edit_original_response(
            content="Banner, thumbnail e rodapé atualizados.",
            embed=await _preview(interaction.guild),
            view=GameCatalogAdminView(interaction.user.id),
        )


class GameCatalogControlsModal(discord.ui.Modal, title="Catálogo • botão e seletor"):
    def __init__(self, config) -> None:
        super().__init__()
        self.button_label = discord.ui.TextInput(
            label="Texto do botão",
            max_length=80,
            default=(config.open_button_label or "Abrir catálogo")[:80],
        )
        self.button_emoji = discord.ui.TextInput(
            label="Emoji do botão (vazio = nenhum)",
            required=False,
            max_length=128,
            default=(config.open_button_emoji or "")[:128],
        )
        self.button_style = discord.ui.TextInput(
            label="Estilo: primary/secondary/success/danger",
            max_length=16,
            default=(config.open_button_style or "success")[:16],
        )
        self.placeholder = discord.ui.TextInput(
            label="Texto do seletor de jogos",
            max_length=100,
            default=(config.game_placeholder or "Selecione um jogo")[:100],
        )
        for item in (
            self.button_label,
            self.button_emoji,
            self.button_style,
            self.placeholder,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        style = str(self.button_style).strip().lower()
        if style not in {"primary", "secondary", "success", "danger"}:
            await interaction.response.send_message(
                "Estilo inválido. Use primary, secondary, success ou danger.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_game_catalog(session, interaction.guild.id)
            config.open_button_label = str(self.button_label).strip() or "Abrir catálogo"
            config.open_button_emoji = str(self.button_emoji).strip()
            config.open_button_style = style
            config.game_placeholder = str(self.placeholder).strip() or "Selecione um jogo"

        await refresh_published_game_catalog_panel(interaction.guild)
        await interaction.edit_original_response(
            content="Botão e seletor atualizados.",
            embed=await _preview(interaction.guild),
            view=GameCatalogAdminView(interaction.user.id),
        )


class GameCatalogScreenModal(discord.ui.Modal, title="Catálogo • tela de jogos"):
    def __init__(self, config) -> None:
        super().__init__()
        self.title_input = discord.ui.TextInput(
            label="Título da tela de jogos",
            max_length=256,
            default=(config.catalog_title or "")[:256],
        )
        self.description = discord.ui.TextInput(
            label="Descrição",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=2500,
            default=(config.catalog_description or "")[:2500],
        )
        self.status = discord.ui.TextInput(
            label="Linha de status (vazio = ocultar)",
            required=False,
            max_length=160,
            default=(config.catalog_status_text or "")[:160],
        )
        self.footer = discord.ui.TextInput(
            label="Rodapé ({count} = quantidade)",
            required=False,
            max_length=2048,
            default=(config.catalog_footer_text or "")[:2048],
        )
        for item in (self.title_input, self.description, self.status, self.footer):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        footer = str(self.footer).strip()
        try:
            footer.format(count=1)
        except (KeyError, ValueError):
            await interaction.response.send_message(
                "Rodapé inválido. Use somente {count} como variável.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_game_catalog(session, interaction.guild.id)
            config.catalog_title = str(self.title_input).strip() or "NEXTBUY • Escolha seu jogo"
            config.catalog_description = str(self.description).strip()
            config.catalog_status_text = str(self.status).strip()
            config.catalog_footer_text = footer

        await refresh_published_game_catalog_panel(interaction.guild)
        await interaction.edit_original_response(
            content="Tela de escolha de jogos atualizada.",
            embed=await _preview(interaction.guild),
            view=GameCatalogAdminView(interaction.user.id),
        )


class GameCatalogMultiSelect(discord.ui.Select):
    def __init__(self, games: list[Product], selected_ids: list[int]) -> None:
        selected = set(int(value) for value in selected_ids)
        options = [
            discord.SelectOption(
                label="Todos os jogos ativos",
                value="all",
                description="Acompanha automaticamente todos os produtos do tipo jogo",
                default=not selected,
            )
        ]
        for game in games[:24]:
            options.append(
                discord.SelectOption(
                    label=game.name[:100],
                    value=str(game.id),
                    description=(
                        f"{'ativo' if game.active else 'desativado'} • "
                        f"{game.game_name or game.name}"
                    )[:100],
                    emoji=select_option_emoji(game.emoji),
                    default=game.id in selected,
                )
            )
        super().__init__(
            placeholder="Escolha quais jogos aparecem no catálogo",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        selected_ids = [] if "all" in self.values else [int(value) for value in self.values]
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_game_catalog(session, interaction.guild.id)
            await save_catalog_game_selection(
                session,
                config=config,
                game_ids=selected_ids,
            )
        await refresh_published_game_catalog_panel(interaction.guild)
        await interaction.edit_original_response(
            content=(
                "O catálogo agora acompanha automaticamente todos os jogos ativos."
                if not selected_ids
                else f"**{len(selected_ids)}** jogo(s) selecionado(s) manualmente."
            ),
            view=None,
        )


class GameCatalogGamesView(discord.ui.View):
    def __init__(self, games: list[Product], selected_ids: list[int]) -> None:
        super().__init__(timeout=300)
        self.add_item(GameCatalogMultiSelect(games, selected_ids))


class GameCatalogPublishSelect(discord.ui.ChannelSelect):
    def __init__(self, owner_id: int) -> None:
        super().__init__(
            placeholder="Escolha o canal do catálogo",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
        )
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.user.id != self.owner_id:
            return
        selected = self.values[0]
        channel = interaction.guild.get_channel(selected.id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Canal inválido.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await publish_game_catalog_panel(interaction, channel)
        except discord.Forbidden:
            await interaction.edit_original_response(
                content="O bot não tem permissão para publicar nesse canal.",
                view=None,
            )
            return
        except discord.HTTPException as exc:
            await interaction.edit_original_response(
                content=f"O Discord recusou a publicação (código {exc.code}).",
                view=None,
            )
            return

        await interaction.edit_original_response(
            content=f"Catálogo de jogos publicado/atualizado em {channel.mention}.",
            view=None,
        )


class GameCatalogPublishView(discord.ui.View):
    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=180)
        self.add_item(GameCatalogPublishSelect(owner_id))


GAME_CATALOG_ACTIONS = (
    ("appearance", "Visual principal", "Título, status, descrição e cor"),
    ("media", "Banner e rodapé", "Banner, thumbnail/logo e rodapé dinâmico"),
    ("controls", "Botão e seletor", "Texto, emoji, estilo do botão e placeholder"),
    ("catalog_screen", "Tela de jogos", "Título, descrição, status e rodapé do catálogo"),
    ("games", "Jogos exibidos", "Todos os jogos ativos ou seleção manual"),
    ("publish", "Publicar / atualizar", "Escolha o canal do catálogo de jogos"),
)


class GameCatalogActionSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="O que deseja configurar no catálogo?",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=label, value=value, description=description)
                for value, label, description in GAME_CATALOG_ACTIONS
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if isinstance(self.view, GameCatalogAdminView):
            await self.view.handle_action(interaction, self.values[0])


class GameCatalogAdminView(discord.ui.View):
    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.add_item(GameCatalogActionSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Esse painel pertence a outra pessoa.",
            ephemeral=True,
        )
        return False

    async def handle_action(self, interaction: discord.Interaction, action: str) -> None:
        if interaction.guild is None:
            return

        if action in {"appearance", "media", "controls", "catalog_screen"}:
            async with SessionLocal() as session, session.begin():
                config = await get_or_create_game_catalog(session, interaction.guild.id)
                modal = {
                    "appearance": GameCatalogAppearanceModal,
                    "media": GameCatalogMediaModal,
                    "controls": GameCatalogControlsModal,
                    "catalog_screen": GameCatalogScreenModal,
                }[action](config)
            await interaction.response.send_modal(modal)
            return

        if action == "games":
            await interaction.response.defer(ephemeral=True, thinking=True)
            async with SessionLocal() as session, session.begin():
                config = await get_or_create_game_catalog(session, interaction.guild.id)
                games = await list_all_game_products(session, guild_id=interaction.guild.id)
                selected = list(config.selected_game_ids or [])
            if not games:
                await interaction.edit_original_response(
                    content=(
                        "Nenhum produto do tipo game/games/jogo/jogos foi criado. "
                        "Crie os jogos em Configurar loja → Criar produto."
                    ),
                    embed=None,
                    view=GameCatalogAdminView(self.owner_id),
                )
                return
            await interaction.edit_original_response(
                content=(
                    "Escolha os jogos do catálogo. Todos os jogos ativos faz o painel "
                    "acompanhar automaticamente novos jogos."
                ),
                embed=None,
                view=GameCatalogGamesView(games, selected),
            )
            return

        if action == "publish":
            await interaction.response.send_message(
                "Escolha o canal onde o catálogo de jogos ficará publicado:",
                view=GameCatalogPublishView(self.owner_id),
                ephemeral=True,
            )
            return


async def send_game_catalog_admin(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    await interaction.edit_original_response(
        content=(
            "Configure o segundo painel da loja dedicado a jogos. "
            "Os jogos e produtos reutilizam o mesmo catálogo, estoque e PIX da loja principal."
        ),
        embed=await _preview(interaction.guild),
        view=GameCatalogAdminView(interaction.user.id),
    )
