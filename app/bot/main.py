import logging

import discord
from discord import app_commands
from discord.ext import commands

from app.bot.views.manual_pix import TicketStaffContainerLayout, restore_manual_pix_views
from app.bot.views.profile import LeaderboardView
from app.bot.views.store_panel import restore_store_panel_views
from app.bot.workflows.feedback_permissions import (
    FeedbackPermissionSyncError,
    sync_feedback_channel_permissions,
)
from app.core.config import settings
from app.core.guild_guard import STORE_GUILD_ID, is_store_guild
from app.db.session import SessionLocal
from app.services.ai_gateway import available_providers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class StoreOnlyCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_store_guild(interaction.guild_id):
            return True
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Este bot é exclusivo do servidor oficial da NEXTBUY.",
                ephemeral=True,
            )
        return False


class NextBuyBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            tree_cls=StoreOnlyCommandTree,
        )
        self._feedback_permissions_synced = False

    async def _restore_ticket_views(self) -> None:
        from sqlalchemy import select

        from app.db.models import Order

        async with SessionLocal() as session:
            order_ids = (
                await session.scalars(
                    select(Order.id).where(
                        Order.ticket_channel_id.is_not(None),
                        Order.status.in_(["paid", "processing", "delivered"]),
                    )
                )
            ).all()
        for order_id in order_ids:
            self.add_view(TicketStaffContainerLayout(order_id))

    async def setup_hook(self) -> None:
        await self.load_extension("app.bot.cogs.admin")
        await self.load_extension("app.bot.cogs.staff")
        await self.load_extension("app.bot.cogs.feedback")
        await self.load_extension("app.bot.cogs.automation_runtime")
        await self.load_extension("app.bot.cogs.automation_refinement")
        await self.load_extension("app.bot.cogs.delivery_runtime")
        await self.load_extension("app.bot.cogs.ticket_automation")
        await self.load_extension("app.bot.cogs.audit_logs")
        await self.load_extension("app.bot.cogs.leaderboard_refresh")
        self.add_view(LeaderboardView())
        await self._restore_ticket_views()
        await restore_manual_pix_views(self)
        await restore_store_panel_views(self)
        official_guild = discord.Object(id=STORE_GUILD_ID)
        self.tree.copy_global_to(guild=official_guild)
        await self.tree.sync(guild=official_guild)

        # Remove comandos globais para que não apareçam/funcionem em outros servidores.
        self.tree.clear_commands(guild=None)
        await self.tree.sync()
        logger.info(
            "Comandos sincronizados exclusivamente no servidor oficial %s",
            STORE_GUILD_ID,
        )


bot = NextBuyBot()


@bot.event
async def on_ready() -> None:
    logger.info("NEXTBUY online como %s", bot.user)

    for guild in list(bot.guilds):
        if is_store_guild(guild.id):
            continue
        logger.warning(
            "Servidor não autorizado detectado (%s - %s). Saindo automaticamente.",
            guild.name,
            guild.id,
        )
        try:
            await guild.leave()
        except discord.HTTPException as exc:
            logger.warning(
                "Não foi possível sair do servidor não autorizado %s: %s",
                guild.id,
                exc,
            )

    providers = available_providers()
    if providers:
        logger.info(
            "IA pronta com provedores: %s",
            " > ".join(f"{provider.name} ({provider.model})" for provider in providers),
        )
    else:
        logger.warning(
            "IA sem provedor disponível. Configure GROQ_API_KEY, GEMINI_API_KEY ou "
            "OPENROUTER_API_KEY no .env."
        )

    if bot._feedback_permissions_synced:
        return
    for guild in bot.guilds:
        if not is_store_guild(guild.id):
            continue
        try:
            await sync_feedback_channel_permissions(guild)
        except FeedbackPermissionSyncError as exc:
            logger.warning(
                "Não foi possível sincronizar permissões de feedbacks no servidor %s: %s",
                guild.id,
                exc,
            )
    bot._feedback_permissions_synced = True


@bot.event
async def on_guild_join(guild: discord.Guild) -> None:
    if is_store_guild(guild.id):
        return
    logger.warning(
        "Convite bloqueado: servidor %s (%s) não é o servidor oficial.",
        guild.name,
        guild.id,
    )
    try:
        await guild.leave()
    except discord.HTTPException as exc:
        logger.warning("Falha ao sair do servidor não autorizado %s: %s", guild.id, exc)


def run() -> None:
    token = settings.discord_token.get_secret_value()
    if not token:
        raise RuntimeError("DISCORD_TOKEN não configurado")
    bot.run(token)


if __name__ == "__main__":
    run()
