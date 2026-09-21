from __future__ import annotations

from urllib.parse import urlparse

import discord
from sqlalchemy import select

from app.bot.components_v2 import DEFAULT_ACCENT, add_action_row
from app.bot.emoji import (
    emoji_display_value,
    resolve_guild_emoji_aliases,
    select_option_emoji,
)
from app.db.models import GuildConfig, TermsDocument
from app.db.session import SessionLocal
from app.services.audit import write_audit_log
from app.services.configs import get_or_create_guild_config
from app.services.terms import list_active_terms_for_acceptance


def normalize_http_url(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("A URL precisa começar com http:// ou https://.")
    return cleaned


def safe_http_url(value: str | None) -> str | None:
    try:
        return normalize_http_url(value)
    except ValueError:
        return None


def term_option_description(terms: TermsDocument) -> str:
    if terms.summary.strip():
        return terms.summary.strip()[:100]
    for line in terms.content.splitlines():
        cleaned = line.strip().lstrip("#>•-* ").strip()
        if cleaned:
            return cleaned[:100]
    return f"Versão {terms.version}"


class PublicTermDetailView(discord.ui.LayoutView):
    def __init__(self, terms: TermsDocument, guild: discord.Guild) -> None:
        super().__init__(timeout=300)
        emoji = emoji_display_value(terms.emoji, guild)
        heading = f"{emoji} " if emoji else ""
        body = resolve_guild_emoji_aliases(terms.content, guild)
        children: list[discord.ui.Item] = [
            discord.ui.TextDisplay(f"## {heading}{terms.title}\n{body}")
        ]

        image_url = safe_http_url(terms.image_url)
        if image_url:
            children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(image_url)))

        if terms.ephemeral_message.strip():
            children.append(discord.ui.Separator())
            children.append(
                discord.ui.TextDisplay(
                    resolve_guild_emoji_aliases(terms.ephemeral_message, guild)
                )
            )

        link_url = safe_http_url(terms.link_url)
        if link_url:
            children.append(
                discord.ui.ActionRow(
                    discord.ui.Button(
                        label="Abrir link",
                        style=discord.ButtonStyle.link,
                        url=link_url,
                    )
                )
            )

        children.append(discord.ui.Separator())
        children.append(discord.ui.TextDisplay(f"-# NEXTBUY • Versão {terms.version}"))
        self.container = discord.ui.Container(*children, accent_colour=DEFAULT_ACCENT)
        self.add_item(self.container)


class PublicTermsSelect(discord.ui.Select):
    def __init__(self, terms: list[TermsDocument], guild: discord.Guild) -> None:
        options = [
            discord.SelectOption(
                label=item.title[:100],
                value=str(item.id),
                description=term_option_description(item),
                emoji=select_option_emoji(item.emoji, guild),
            )
            for item in terms[:25]
        ]
        super().__init__(
            placeholder="📜 | Selecione o Termo",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="nextbuy:terms:public:select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        terms_id = int(self.values[0])
        async with SessionLocal() as session:
            terms = await session.get(TermsDocument, terms_id)
        if (
            terms is None
            or terms.guild_id != interaction.guild.id
            or not terms.active
        ):
            await interaction.response.send_message(
                "Este termo não está mais disponível.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            view=PublicTermDetailView(terms, interaction.guild),
            ephemeral=True,
        )


class PublicTermsPanelView(discord.ui.LayoutView):
    def __init__(
        self,
        terms: list[TermsDocument],
        guild: discord.Guild,
        config: GuildConfig,
    ) -> None:
        super().__init__(timeout=None)
        panel_emoji = emoji_display_value(config.terms_panel_emoji, guild)
        prefix = f"{panel_emoji} " if panel_emoji else ""
        description = resolve_guild_emoji_aliases(
            config.terms_panel_description,
            guild,
        )
        header = f"## {prefix}{config.terms_panel_title}\n{description}"

        children: list[discord.ui.Item] = []
        thumbnail_url = safe_http_url(config.terms_panel_thumbnail_url)
        if thumbnail_url:
            children.append(
                discord.ui.Section(
                    discord.ui.TextDisplay(header),
                    accessory=discord.ui.Thumbnail(thumbnail_url),
                )
            )
        else:
            children.append(discord.ui.TextDisplay(header))

        banner_url = safe_http_url(config.terms_panel_banner_url)
        if banner_url:
            children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(banner_url)))

        children.append(discord.ui.Separator())
        children.append(
            discord.ui.TextDisplay(
                "Selecione abaixo a categoria que deseja consultar."
            )
        )
        children.append(discord.ui.ActionRow(PublicTermsSelect(terms, guild)))
        self.container = discord.ui.Container(*children, accent_colour=DEFAULT_ACCENT)
        self.add_item(self.container)


async def publish_terms_panel(interaction: discord.Interaction) -> None:
    if interaction.guild is None or interaction.channel is None:
        await interaction.response.send_message("Canal inválido.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    async with SessionLocal() as session, session.begin():
        terms = await list_active_terms_for_acceptance(
            session,
            guild_id=interaction.guild.id,
        )
        config = await get_or_create_guild_config(session, interaction.guild.id)

    if not terms:
        await interaction.edit_original_response(
            content="Não existem termos ativos para publicar.",
            view=None,
        )
        return

    view = PublicTermsPanelView(terms, interaction.guild, config)
    message = None
    if (
        config.terms_channel_id == interaction.channel.id
        and config.terms_message_id is not None
        and hasattr(interaction.channel, "fetch_message")
    ):
        try:
            message = await interaction.channel.fetch_message(config.terms_message_id)
            await message.edit(content=None, embeds=[], view=view)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            message = None

    if message is None:
        message = await interaction.channel.send(view=view)

    async with SessionLocal() as session, session.begin():
        current = await get_or_create_guild_config(session, interaction.guild.id)
        current.terms_channel_id = interaction.channel.id
        current.terms_message_id = message.id
        await write_audit_log(
            session,
            guild_id=interaction.guild.id,
            actor_discord_id=interaction.user.id,
            action="terms.publish",
            target_type="message",
            target_id=str(message.id),
            details={"channel_id": interaction.channel.id, "terms_count": len(terms)},
        )

    await interaction.edit_original_response(
        content=f"Termos publicados em {interaction.channel.mention}.",
        view=None,
    )


async def restore_terms_panel_views(bot: discord.Client) -> None:
    async with SessionLocal() as session:
        configs = list(
            (
                await session.scalars(
                    select(GuildConfig).where(
                        GuildConfig.terms_channel_id.is_not(None),
                        GuildConfig.terms_message_id.is_not(None),
                    )
                )
            ).all()
        )

        for config in configs:
            guild = bot.get_guild(config.guild_id)
            if guild is None or config.terms_message_id is None:
                continue
            terms = await list_active_terms_for_acceptance(
                session,
                guild_id=config.guild_id,
            )
            if not terms:
                continue
            bot.add_view(
                PublicTermsPanelView(terms, guild, config),
                message_id=config.terms_message_id,
            )
