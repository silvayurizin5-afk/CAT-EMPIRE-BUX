from __future__ import annotations

import discord

from app.bot.emoji import select_option_emoji
from app.bot.views.store_games import (
    GameSubPanelLayout,
    list_game_subpanel_products,
    product_store_option_description,
)
from app.db.models import Product
from app.db.session import SessionLocal
from app.db.store_models import GameCatalogPanelConfig
from app.services.game_catalog import get_or_create_game_catalog, list_catalog_games
from app.services.store_media import (
    StoreBannerError,
    prepare_store_banner,
    prepare_store_thumbnail,
    reusable_banner_attachment_url,
    reusable_thumbnail_attachment_url,
)


def _button_style(value: str) -> discord.ButtonStyle:
    return {
        "primary": discord.ButtonStyle.primary,
        "secondary": discord.ButtonStyle.secondary,
        "success": discord.ButtonStyle.success,
        "danger": discord.ButtonStyle.danger,
    }.get(str(value or "").strip().lower(), discord.ButtonStyle.success)


def _format_footer(template: str, count: int) -> str:
    try:
        return (template or "").format(count=count)
    except (KeyError, ValueError):
        return template or ""


def build_game_catalog_embed(
    config: GameCatalogPanelConfig,
    game_count: int,
    *,
    image_url: str | None = None,
    thumbnail_url: str | None = None,
) -> discord.Embed:
    status_line = " ".join(
        value
        for value in ((config.status_text or "").strip(), (config.status_emoji or "").strip())
        if value
    )
    description = "\n\n".join(
        value
        for value in (status_line, (config.description or "").strip())
        if value
    )
    embed = discord.Embed(
        title=(config.title or "NEXTBUY")[:256],
        description=description or None,
        color=config.color,
    )
    effective_image_url = image_url if image_url is not None else config.image_url
    effective_thumbnail_url = (
        thumbnail_url if thumbnail_url is not None else config.thumbnail_url
    )
    if effective_image_url:
        embed.set_image(url=effective_image_url)
    if effective_thumbnail_url:
        embed.set_thumbnail(url=effective_thumbnail_url)
    footer = _format_footer(config.footer_text, game_count).strip()
    if footer:
        embed.set_footer(text=footer[:2048])
    return embed


def build_game_selector_embed(
    config: GameCatalogPanelConfig,
    game_count: int,
) -> discord.Embed:
    description = "\n".join(
        value
        for value in (
            (config.catalog_description or "").strip(),
            (config.catalog_status_text or "").strip(),
        )
        if value
    )
    embed = discord.Embed(
        title=(config.catalog_title or "NEXTBUY • Escolha seu jogo")[:256],
        description=description or None,
        color=config.color,
    )
    footer = _format_footer(config.catalog_footer_text, game_count).strip()
    if footer:
        embed.set_footer(text=footer[:2048])
    return embed


class GameCatalogSelect(discord.ui.Select):
    def __init__(
        self,
        games: list[Product],
        *,
        placeholder: str,
        guild: discord.Guild | None,
    ) -> None:
        options = [
            discord.SelectOption(
                label=game.name[:100],
                value=str(game.id),
                description=product_store_option_description(game),
                emoji=select_option_emoji(game.emoji, guild),
            )
            for game in games[:25]
        ]
        if not options:
            options = [
                discord.SelectOption(
                    label="Nenhum jogo disponível",
                    value="none",
                    description="A equipe ainda não publicou jogos neste catálogo",
                )
            ]
        super().__init__(
            custom_id="nextbuy:game-catalog:select",
            placeholder=(placeholder or "Selecione um jogo")[:100],
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        if self.values[0] == "none":
            await interaction.response.send_message(
                "Nenhum jogo está disponível no momento.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        game_id = int(self.values[0])
        async with SessionLocal() as session:
            game = await session.get(Product, game_id)
            products = (
                await list_game_subpanel_products(
                    session,
                    guild_id=interaction.guild.id,
                    game_product=game,
                )
                if game is not None
                and game.guild_id == interaction.guild.id
                and game.active
                else []
            )

        if game is None or game.guild_id != interaction.guild.id or not game.active:
            await interaction.edit_original_response(
                content="Esse jogo não está mais disponível.",
                embed=None,
                view=None,
            )
            return

        await interaction.edit_original_response(
            content=None,
            embed=None,
            view=GameSubPanelLayout(
                game_product=game,
                products=products,
                owner_id=interaction.user.id,
                guild=interaction.guild,
            ),
        )


class GameCatalogSelectorView(discord.ui.View):
    def __init__(
        self,
        games: list[Product],
        *,
        placeholder: str,
        guild: discord.Guild | None,
        owner_id: int,
    ) -> None:
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.add_item(
            GameCatalogSelect(
                games,
                placeholder=placeholder,
                guild=guild,
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Esse catálogo pertence a outro cliente.",
            ephemeral=True,
        )
        return False


class GameCatalogOpenView(discord.ui.View):
    def __init__(self, config: GameCatalogPanelConfig | None = None) -> None:
        super().__init__(timeout=None)
        if config is not None:
            self.open_catalog.label = (config.open_button_label or "Abrir catálogo")[:80]
            self.open_catalog.style = _button_style(config.open_button_style)
            emoji = (config.open_button_emoji or "").strip()
            try:
                self.open_catalog.emoji = emoji or None
            except (TypeError, ValueError):
                self.open_catalog.emoji = None

    @discord.ui.button(
        label="Abrir catálogo",
        style=discord.ButtonStyle.success,
        custom_id="nextbuy:game-catalog:open",
    )
    async def open_catalog(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_game_catalog(session, interaction.guild.id)
            games = await list_catalog_games(
                session,
                guild_id=interaction.guild.id,
                config=config,
            )
        await interaction.edit_original_response(
            content=None,
            embed=build_game_selector_embed(config, len(games)),
            view=GameCatalogSelectorView(
                games,
                placeholder=config.game_placeholder,
                guild=interaction.guild,
                owner_id=interaction.user.id,
            ),
        )


def _attachment_for_url(
    attachment_url: str | None,
    attachments: list[discord.Attachment] | tuple[discord.Attachment, ...],
) -> discord.Attachment | None:
    if not attachment_url or not attachment_url.startswith("attachment://"):
        return None
    filename = attachment_url.removeprefix("attachment://")
    return next(
        (attachment for attachment in attachments if attachment.filename == filename),
        None,
    )


async def _prepare_game_catalog_media(
    config: GameCatalogPanelConfig,
    guild: discord.Guild,
    existing_attachments: list[discord.Attachment] | tuple[discord.Attachment, ...] = (),
) -> tuple[str | None, str | None, list[discord.Attachment | discord.File]]:
    attachments: list[discord.Attachment | discord.File] = []

    image_url = config.image_url
    reused_banner_url = reusable_banner_attachment_url(
        config.image_url,
        existing_attachments,
    )
    if reused_banner_url is not None:
        image_url = reused_banner_url
        reused_banner = _attachment_for_url(reused_banner_url, existing_attachments)
        if reused_banner is not None:
            attachments.append(reused_banner)
    elif config.image_url:
        try:
            prepared_banner = await prepare_store_banner(
                config.image_url,
                upload_limit=guild.filesize_limit,
            )
        except StoreBannerError:
            prepared_banner = None
        if prepared_banner is not None:
            image_url = prepared_banner.attachment_url
            attachments.append(prepared_banner.to_file())

    thumbnail_url = config.thumbnail_url
    reused_thumbnail_url = reusable_thumbnail_attachment_url(
        config.thumbnail_url,
        existing_attachments,
    )
    if reused_thumbnail_url is not None:
        thumbnail_url = reused_thumbnail_url
        reused_thumbnail = _attachment_for_url(
            reused_thumbnail_url,
            existing_attachments,
        )
        if reused_thumbnail is not None:
            attachments.append(reused_thumbnail)
    elif config.thumbnail_url:
        try:
            prepared_thumbnail = await prepare_store_thumbnail(
                config.thumbnail_url,
                upload_limit=guild.filesize_limit,
            )
        except StoreBannerError:
            prepared_thumbnail = None
        if prepared_thumbnail is not None:
            thumbnail_url = prepared_thumbnail.attachment_url
            attachments.append(prepared_thumbnail.to_file())

    return image_url, thumbnail_url, attachments


async def publish_game_catalog_panel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
) -> discord.Message:
    if interaction.guild is None:
        raise ValueError("Servidor inválido")

    async with SessionLocal() as session, session.begin():
        config = await get_or_create_game_catalog(session, interaction.guild.id)
        games = await list_catalog_games(
            session,
            guild_id=interaction.guild.id,
            config=config,
        )
        previous_channel_id = config.published_channel_id
        previous_message_id = config.published_message_id

    view = GameCatalogOpenView(config)
    message: discord.Message | None = None

    if previous_channel_id == channel.id and previous_message_id:
        try:
            message = await channel.fetch_message(previous_message_id)
            image_url, thumbnail_url, attachments = await _prepare_game_catalog_media(
                config,
                interaction.guild,
                message.attachments,
            )
            embed = build_game_catalog_embed(
                config,
                len(games),
                image_url=image_url,
                thumbnail_url=thumbnail_url,
            )
            await message.edit(embed=embed, attachments=attachments, view=view)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            message = None

    if message is None:
        image_url, thumbnail_url, attachments = await _prepare_game_catalog_media(
            config,
            interaction.guild,
        )
        embed = build_game_catalog_embed(
            config,
            len(games),
            image_url=image_url,
            thumbnail_url=thumbnail_url,
        )
        files = [item for item in attachments if isinstance(item, discord.File)]
        message = await channel.send(embed=embed, files=files, view=view)

    if (
        previous_channel_id
        and previous_message_id
        and (previous_channel_id != channel.id or previous_message_id != message.id)
    ):
        old_channel = interaction.guild.get_channel(previous_channel_id)
        if isinstance(old_channel, discord.TextChannel):
            try:
                old_message = await old_channel.fetch_message(previous_message_id)
                await old_message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    async with SessionLocal() as session, session.begin():
        saved = await get_or_create_game_catalog(session, interaction.guild.id)
        saved.published_channel_id = channel.id
        saved.published_message_id = message.id

    return message


async def refresh_published_game_catalog_panel(guild: discord.Guild) -> None:
    async with SessionLocal() as session, session.begin():
        config = await get_or_create_game_catalog(session, guild.id)
        games = await list_catalog_games(session, guild_id=guild.id, config=config)
        channel_id = config.published_channel_id
        message_id = config.published_message_id

    if not channel_id or not message_id:
        return
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        message = await channel.fetch_message(message_id)
        image_url, thumbnail_url, attachments = await _prepare_game_catalog_media(
            config,
            guild,
            message.attachments,
        )
        await message.edit(
            embed=build_game_catalog_embed(
                config,
                len(games),
                image_url=image_url,
                thumbnail_url=thumbnail_url,
            ),
            attachments=attachments,
            view=GameCatalogOpenView(config),
        )
    except discord.NotFound:
        async with SessionLocal() as session, session.begin():
            saved = await get_or_create_game_catalog(session, guild.id)
            saved.published_channel_id = None
            saved.published_message_id = None
    except (discord.Forbidden, discord.HTTPException):
        return


def restore_game_catalog_view(bot: discord.Client) -> None:
    bot.add_view(GameCatalogOpenView())
