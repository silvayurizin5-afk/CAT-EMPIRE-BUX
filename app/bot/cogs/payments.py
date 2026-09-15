import logging

import discord
from discord.ext import commands, tasks
from sqlalchemy import select

from app.db.models import GuildConfig
from app.db.session import SessionLocal
from app.services.payment_notifications import (
    PendingTopUpNotification,
    claim_pending_topup_notifications,
    mark_topup_notification_sent,
    release_topup_notification,
)

logger = logging.getLogger(__name__)


class PaymentNotificationsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.worker.start()

    def cog_unload(self) -> None:
        self.worker.cancel()

    async def _deliver(self, item: PendingTopUpNotification) -> None:
        guild = self.bot.get_guild(item.guild_id)
        if guild is None:
            async with SessionLocal() as session, session.begin():
                await release_topup_notification(
                    session,
                    notification_id=item.notification_id,
                    error="Servidor não encontrado no cache do bot",
                )
            return

        user = self.bot.get_user(item.discord_user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(item.discord_user_id)
            except discord.NotFound:
                async with SessionLocal() as session, session.begin():
                    await mark_topup_notification_sent(
                        session,
                        notification_id=item.notification_id,
                        note="Usuário do Discord não encontrado",
                    )
                return
            except discord.HTTPException as exc:
                async with SessionLocal() as session, session.begin():
                    await release_topup_notification(
                        session,
                        notification_id=item.notification_id,
                        error=f"Falha ao buscar usuário: {exc}",
                    )
                return

        note: str | None = None
        try:
            await user.send(
                "Pagamento confirmado na **NEXTBUY**.\n"
                f"Foram adicionados **{item.credits_amount:.2f} créditos** ao seu saldo.\n"
                f"Recarga: `{item.topup_id[:8]}`"
            )
        except discord.Forbidden:
            note = "DM fechada ou bloqueada"
        except discord.HTTPException as exc:
            async with SessionLocal() as session, session.begin():
                await release_topup_notification(
                    session,
                    notification_id=item.notification_id,
                    error=f"Falha temporária ao enviar DM: {exc}",
                )
            return

        async with SessionLocal() as session:
            config = await session.scalar(
                select(GuildConfig).where(GuildConfig.guild_id == item.guild_id)
            )
        if config and config.logs_channel_id:
            channel = guild.get_channel(config.logs_channel_id)
            if isinstance(channel, discord.TextChannel):
                embed = discord.Embed(
                    title="Pagamento confirmado",
                    description=f"Recarga `{item.topup_id[:8]}` aprovada pelo Mercado Pago.",
                )
                embed.add_field(name="Cliente", value=f"<@{item.discord_user_id}>")
                embed.add_field(name="Créditos", value=f"{item.credits_amount:.2f}")
                try:
                    await channel.send(
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    logger.exception("Falha ao registrar pagamento no canal de logs")

        async with SessionLocal() as session, session.begin():
            await mark_topup_notification_sent(
                session,
                notification_id=item.notification_id,
                note=note,
            )

    @tasks.loop(seconds=20)
    async def worker(self) -> None:
        async with SessionLocal() as session, session.begin():
            pending = await claim_pending_topup_notifications(session)
        for item in pending:
            try:
                await self._deliver(item)
            except Exception:
                logger.exception("Erro inesperado ao processar confirmação de recarga %s", item.topup_id)
                async with SessionLocal() as session, session.begin():
                    await release_topup_notification(
                        session,
                        notification_id=item.notification_id,
                        error="Erro inesperado no worker de notificações",
                    )

    @worker.before_loop
    async def before_worker(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PaymentNotificationsCog(bot))
