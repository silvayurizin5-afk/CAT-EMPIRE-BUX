import logging

import discord

from app.bot.views.profile import LeaderboardView
from app.db.session import SessionLocal
from app.services.configs import get_or_create_guild_config
from app.services.profiles import list_leaderboard

logger = logging.getLogger(__name__)


async def build_leaderboard_embed(guild: discord.Guild) -> discord.Embed:
    async with SessionLocal() as session:
        entries = await list_leaderboard(session, guild_id=guild.id, limit=20)

    embed = discord.Embed(
        title="NEXTBUY • Ranking de clientes",
        description="Top 20 por valor gasto em compras confirmadas neste servidor.",
    )
    if not entries:
        embed.add_field(name="Ranking", value="Nenhuma compra confirmada ainda.", inline=False)
        return embed

    lines: list[str] = []
    for index, entry in enumerate(entries, start=1):
        member = guild.get_member(entry.discord_user_id)
        label = member.mention if member is not None else f"<@{entry.discord_user_id}>"
        lines.append(
            f"**#{index}** {label} — **{entry.total_spent:.2f} créditos** "
            f"({entry.completed_orders} compras)"
        )
    embed.add_field(name="Top 20", value="\n".join(lines), inline=False)
    embed.set_footer(text="Use o botão abaixo para ver seu perfil de forma privada.")
    return embed


async def refresh_leaderboard(
    guild: discord.Guild,
    *,
    channel: discord.abc.Messageable | None = None,
) -> discord.Message | None:
    async with SessionLocal() as session:
        async with session.begin():
            config = await get_or_create_guild_config(session, guild.id)
            configured_channel_id = config.leaderboard_channel_id
            message_id = config.leaderboard_message_id

    target = channel
    if target is None and configured_channel_id:
        target = guild.get_channel(configured_channel_id)
    if target is None:
        return None

    embed = await build_leaderboard_embed(guild)
    view = LeaderboardView()
    message: discord.Message | None = None

    if message_id and hasattr(target, "fetch_message"):
        try:
            old_message = await target.fetch_message(message_id)
            await old_message.edit(embed=embed, view=view)
            message = old_message
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.info("Não foi possível editar o ranking antigo do servidor %s", guild.id)

    if message is None:
        try:
            message = await target.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Não foi possível publicar ranking no servidor %s", guild.id)
            return None

    async with SessionLocal() as session:
        async with session.begin():
            config = await get_or_create_guild_config(session, guild.id)
            config.leaderboard_channel_id = message.channel.id
            config.leaderboard_message_id = message.id
    return message
