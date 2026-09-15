import json
import logging

import discord
from discord.ext import commands, tasks

from app.db.session import SessionLocal
from app.services.audit import (
    PendingAuditDelivery,
    claim_pending_audit_deliveries,
    mark_audit_delivery_published,
    release_audit_delivery,
)

logger = logging.getLogger(__name__)


def build_audit_embed(item: PendingAuditDelivery) -> discord.Embed:
    embed = discord.Embed(
        title="NEXTBUY • Auditoria",
        description=f"`{item.action}`",
        timestamp=item.created_at,
    )
    if item.actor_discord_id:
        embed.add_field(name="Responsável", value=f"<@{item.actor_discord_id}>")
    else:
        embed.add_field(name="Responsável", value="Sistema")
    if item.target_type or item.target_id:
        target = " / ".join(value for value in (item.target_type, item.target_id) if value)
        embed.add_field(name="Alvo", value=target[:1024], inline=False)
    if item.details:
        details = json.dumps(item.details, ensure_ascii=False, default=str, sort_keys=True)
        embed.add_field(name="Detalhes", value=f"```json\n{details[:900]}\n```", inline=False)
    embed.set_footer(text=f"Audit #{item.audit_log_id}")
    return embed


class AuditLogsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.worker.start()

    def cog_unload(self) -> None:
        self.worker.cancel()

    async def _deliver(self, item: PendingAuditDelivery) -> None:
        guild = self.bot.get_guild(item.guild_id)
        if guild is None:
            async with SessionLocal() as session, session.begin():
                await release_audit_delivery(
                    session,
                    delivery_id=item.delivery_id,
                    error="Servidor não encontrado no cache do bot",
                )
            return

        channel = guild.get_channel(item.logs_channel_id)
        if not isinstance(channel, discord.TextChannel):
            async with SessionLocal() as session, session.begin():
                await release_audit_delivery(
                    session,
                    delivery_id=item.delivery_id,
                    error="Canal de logs não encontrado ou não é textual",
                )
            return

        try:
            await channel.send(
                embed=build_audit_embed(item),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as exc:
            async with SessionLocal() as session, session.begin():
                await release_audit_delivery(
                    session,
                    delivery_id=item.delivery_id,
                    error=f"Falha ao publicar no Discord: {exc}",
                )
            return

        async with SessionLocal() as session, session.begin():
            await mark_audit_delivery_published(
                session,
                delivery_id=item.delivery_id,
            )

    @tasks.loop(seconds=20)
    async def worker(self) -> None:
        async with SessionLocal() as session, session.begin():
            pending = await claim_pending_audit_deliveries(session)
        for item in pending:
            try:
                await self._deliver(item)
            except Exception:
                logger.exception("Erro inesperado ao publicar audit log %s", item.audit_log_id)
                async with SessionLocal() as session, session.begin():
                    await release_audit_delivery(
                        session,
                        delivery_id=item.delivery_id,
                        error="Erro inesperado no worker de auditoria",
                    )

    @worker.before_loop
    async def before_worker(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AuditLogsCog(bot))
