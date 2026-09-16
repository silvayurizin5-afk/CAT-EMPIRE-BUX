import discord
from discord import app_commands
from discord.ext import commands

from app.bot.checks import can_admin
from app.db.session import SessionLocal
from app.services.ai_config import (
    get_or_create_ai_config,
    set_ai_channels,
    set_suggestions_channel,
    set_support_channel,
)
from app.services.ai_gateway import available_providers

DEFAULT_ACCENT = 0x2B2D31


def _mention(channel_id: int | None) -> str:
    return f"<#{channel_id}>" if channel_id else "Não configurado"


def _channels_text(channel_ids: list[int]) -> str:
    if not channel_ids:
        return "Nenhum canal autorizado. A IA ficará em silêncio."
    return ", ".join(f"<#{channel_id}>" for channel_id in channel_ids)


async def _config_text(guild_id: int) -> str:
    async with SessionLocal() as session, session.begin():
        config = await get_or_create_ai_config(session, guild_id)
        enabled = config.enabled
        allowed = list(config.allowed_channel_ids or [])
        support = config.support_channel_id
        suggestions = config.suggestions_channel_id
        order = list(config.provider_order or [])
    configured = [provider.name for provider in available_providers(order)]
    providers = ", ".join(configured) if configured else "Nenhuma API configurada"
    return (
        "## NEXTBUY • Configuração da IA\n"
        f"**Status:** {'Ativada' if enabled else 'Desativada'}\n"
        f"**Canais autorizados:** {_channels_text(allowed)}\n"
        f"**Suporte:** {_mention(support)}\n"
        f"**Sugestões:** {_mention(suggestions)}\n"
        f"**Provedores disponíveis:** {providers}\n\n"
        "A IA responde **somente** nos canais autorizados. Fora deles, ignora as mensagens."
    )


class AllowedChannelsSelect(discord.ui.ChannelSelect):
    def __init__(self, owner_id: int) -> None:
        super().__init__(
            placeholder="Canais onde a IA pode responder",
            min_values=1,
            max_values=25,
            channel_types=[discord.ChannelType.text],
            custom_id="nextbuy:ai:allowed-channels",
        )
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.user.id != self.owner_id:
            return
        await interaction.response.defer()
        ids = [channel.id for channel in self.values]
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, interaction.guild.id)
            await set_ai_channels(session, config=config, channel_ids=ids)
        await interaction.edit_original_response(
            view=AIConfigView(
                owner_id=self.owner_id,
                body=await _config_text(interaction.guild.id),
            )
        )


class SupportChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, owner_id: int) -> None:
        super().__init__(
            placeholder="Canal de suporte",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
            custom_id="nextbuy:ai:support-channel",
        )
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.user.id != self.owner_id:
            return
        await interaction.response.defer()
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, interaction.guild.id)
            await set_support_channel(
                session,
                config=config,
                channel_id=self.values[0].id,
            )
        await interaction.edit_original_response(
            view=AIConfigView(
                owner_id=self.owner_id,
                body=await _config_text(interaction.guild.id),
            )
        )


class SuggestionsChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, owner_id: int) -> None:
        super().__init__(
            placeholder="Canal de sugestões de produtos/jogos",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
            custom_id="nextbuy:ai:suggestions-channel",
        )
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.user.id != self.owner_id:
            return
        await interaction.response.defer()
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, interaction.guild.id)
            await set_suggestions_channel(
                session,
                config=config,
                channel_id=self.values[0].id,
            )
        await interaction.edit_original_response(
            view=AIConfigView(
                owner_id=self.owner_id,
                body=await _config_text(interaction.guild.id),
            )
        )


class AIConfigView(discord.ui.LayoutView):
    def __init__(self, *, owner_id: int, body: str) -> None:
        super().__init__(timeout=900)
        self.owner_id = owner_id
        toggle = discord.ui.Button(
            label="Ativar / Desativar IA",
            style=discord.ButtonStyle.secondary,
            custom_id="nextbuy:ai:toggle",
        )
        toggle.callback = self._toggle
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(body[:4000]),
                discord.ui.ActionRow(AllowedChannelsSelect(owner_id)),
                discord.ui.ActionRow(SupportChannelSelect(owner_id)),
                discord.ui.ActionRow(SuggestionsChannelSelect(owner_id)),
                discord.ui.ActionRow(toggle),
                accent_color=DEFAULT_ACCENT,
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Esse painel pertence a outra pessoa.", ephemeral=True)
        return False

    async def _toggle(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer()
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, interaction.guild.id)
            config.enabled = not config.enabled
        await interaction.edit_original_response(
            view=AIConfigView(
                owner_id=self.owner_id,
                body=await _config_text(interaction.guild.id),
            )
        )


class AIAdminCog(commands.GroupCog, group_name="ia", group_description="Configuração da IA NEXTBUY"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="configurar", description="Configura os canais e o comportamento da IA")
    @app_commands.guild_only()
    async def configure(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if interaction.guild is None or not await can_admin(interaction):
            await interaction.edit_original_response(content="Você não tem acesso a essa configuração.")
            return
        body = await _config_text(interaction.guild.id)
        await interaction.edit_original_response(
            content=None,
            embed=None,
            view=AIConfigView(owner_id=interaction.user.id, body=body),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AIAdminCog(bot))
