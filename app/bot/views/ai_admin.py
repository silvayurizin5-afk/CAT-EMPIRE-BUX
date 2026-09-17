import discord

from app.bot.components_v2 import CardLayout, add_action_row, add_select_row
from app.db.session import SessionLocal
from app.services.ai_config import (
    get_or_create_ai_config,
    set_ai_channels,
    set_suggestions_channel,
    set_support_channel,
)
from app.services.ai_gateway import available_providers

FREE_FIRST_PROVIDER_ORDER = [
    "groq",
    "gemini",
    "openrouter",
    "openai",
    "anthropic",
    "xai",
    "mistral",
]


def _channel_mention(guild: discord.Guild, channel_id: int | None) -> str:
    if not channel_id:
        return "Não configurado"
    channel = guild.get_channel(channel_id)
    return channel.mention if isinstance(channel, discord.TextChannel) else f"`{channel_id}`"


def _allowed_channels(guild: discord.Guild, channel_ids: list[int]) -> str:
    mentions = [
        channel.mention
        for channel_id in channel_ids
        if isinstance((channel := guild.get_channel(channel_id)), discord.TextChannel)
    ]
    return ", ".join(mentions) if mentions else "Nenhum"


class AIAllowedChannelsSelect(discord.ui.ChannelSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Canais onde a IA pode responder",
            min_values=1,
            max_values=25,
            channel_types=[discord.ChannelType.text],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer()
        channel_ids = [int(value.id) for value in self.values]
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, interaction.guild.id)
            await set_ai_channels(session, config=config, channel_ids=channel_ids)
        await interaction.edit_original_response(
            view=await build_ai_admin_view(interaction.guild, owner_id=interaction.user.id)
        )


class AISupportChannelSelect(discord.ui.ChannelSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Canal de suporte da IA",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer()
        channel_id = int(self.values[0].id)
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, interaction.guild.id)
            await set_support_channel(session, config=config, channel_id=channel_id)
        await interaction.edit_original_response(
            view=await build_ai_admin_view(interaction.guild, owner_id=interaction.user.id)
        )


class AISuggestionsChannelSelect(discord.ui.ChannelSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Canal para sugestões de produtos",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer()
        channel_id = int(self.values[0].id)
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, interaction.guild.id)
            await set_suggestions_channel(session, config=config, channel_id=channel_id)
        await interaction.edit_original_response(
            view=await build_ai_admin_view(interaction.guild, owner_id=interaction.user.id)
        )


class AIAdminView(discord.ui.LayoutView):
    def __init__(
        self,
        *,
        owner_id: int,
        enabled: bool,
        allowed_channels: str,
        support_channel: str,
        suggestions_channel: str,
        provider_order: list[str],
        provider_status: str,
    ) -> None:
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.enabled = enabled

        card = CardLayout(
            title="NEXTBUY • Configuração da IA",
            description="Configure o comportamento da IA neste servidor sem expor nenhuma chave de API.",
            lines=[
                f"- **Status:** `{'Ativa' if enabled else 'Desativada'}`",
                f"- **Canais autorizados:** {allowed_channels}",
                f"- **Suporte:** {support_channel}",
                f"- **Sugestões:** {suggestions_channel}",
                f"- **Ordem dos provedores:** `{' > '.join(provider_order) if provider_order else 'padrão'}`",
                f"- **Provedores prontos:** {provider_status}",
            ],
            footer="As chaves continuam somente no .env; este painel nunca mostra ou salva segredos.",
            timeout=900,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)

        add_select_row(self.container, AIAllowedChannelsSelect())
        add_select_row(self.container, AISupportChannelSelect())
        add_select_row(self.container, AISuggestionsChannelSelect())

        toggle = discord.ui.Button(
            label="Desativar IA" if enabled else "Ativar IA",
            style=discord.ButtonStyle.danger if enabled else discord.ButtonStyle.success,
        )
        prioritize = discord.ui.Button(
            label="Priorizar provedores grátis",
            style=discord.ButtonStyle.secondary,
        )
        refresh = discord.ui.Button(
            label="Atualizar status",
            style=discord.ButtonStyle.secondary,
        )
        toggle.callback = self._toggle
        prioritize.callback = self._prioritize_free
        refresh.callback = self._refresh_status
        add_action_row(self.container, toggle, prioritize, refresh)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Esse painel pertence a outra pessoa.",
            ephemeral=True,
        )
        return False

    async def _toggle(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer()
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, interaction.guild.id)
            config.enabled = not config.enabled
        await interaction.edit_original_response(
            view=await build_ai_admin_view(interaction.guild, owner_id=self.owner_id)
        )

    async def _prioritize_free(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer()
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_ai_config(session, interaction.guild.id)
            config.provider_order = list(FREE_FIRST_PROVIDER_ORDER)
        await interaction.edit_original_response(
            view=await build_ai_admin_view(interaction.guild, owner_id=self.owner_id)
        )

    async def _refresh_status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer()
        await interaction.edit_original_response(
            view=await build_ai_admin_view(interaction.guild, owner_id=self.owner_id)
        )


async def build_ai_admin_view(guild: discord.Guild, *, owner_id: int) -> AIAdminView:
    async with SessionLocal() as session, session.begin():
        config = await get_or_create_ai_config(session, guild.id)
        enabled = bool(config.enabled)
        allowed_ids = [int(value) for value in (config.allowed_channel_ids or [])]
        support_id = config.support_channel_id
        suggestions_id = config.suggestions_channel_id
        provider_order = [str(value) for value in (config.provider_order or [])]

    providers = available_providers(provider_order)
    provider_status = (
        ", ".join(f"`{provider.name}` ({provider.model})" for provider in providers)
        if providers
        else "**Nenhum** — configure pelo menos uma chave no `.env`."
    )

    return AIAdminView(
        owner_id=owner_id,
        enabled=enabled,
        allowed_channels=_allowed_channels(guild, allowed_ids),
        support_channel=_channel_mention(guild, support_id),
        suggestions_channel=_channel_mention(guild, suggestions_id),
        provider_order=provider_order,
        provider_status=provider_status,
    )


async def send_ai_admin(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    view = await build_ai_admin_view(interaction.guild, owner_id=interaction.user.id)
    await interaction.response.send_message(view=view, ephemeral=True)


__all__ = ["AIAdminView", "build_ai_admin_view", "send_ai_admin"]
