import logging

import discord

from app.bot.views.profile import LeaderboardView
from app.db.session import SessionLocal
from app.services.calculator import format_brl, format_robux
from app.services.configs import get_or_create_guild_config
from app.services.profiles import list_leaderboard

logger = logging.getLogger(__name__)

TROPHY_EMOJI = "<a:TrofeuNextBuy:1549636813432422451>"
ROBUX_EMOJI = "<:ROBUXNextBuy:1549604652557934702>"
PIX_EMOJI = "<:PIX:1549632822388592663>"
POSITION_EMOJIS = {
    1: "<:1rd:1549641381969137777>",
    2: "<:2rd:1549640241118187621>",
    3: "<:3rd:1549640371099930644>",
}


def _position_label(index: int) -> str:
    emoji = POSITION_EMOJIS.get(index)
    if emoji:
        return emoji
    return f"#{index}º"


async def build_leaderboard_embed(guild: discord.Guild) -> discord.Embed:
    async with SessionLocal() as session:
        entries = await list_leaderboard(session, guild_id=guild.id, limit=20)

    embed = discord.Embed(
        title=f"{TROPHY_EMOJI} Leaderboard - NEXTBUY",
        color=discord.Color.from_rgb(43, 45, 49),
    )
    if not entries:
        return embed

    blocks: list[str] = []
    for index, entry in enumerate(entries, start=1):
        member = guild.get_member(entry.discord_user_id)
        mention = member.mention if member is not None else f"<@{entry.discord_user_id}>"
        blocks.append(
            "\n".join(
                (
                    f"**{_position_label(index)} | {mention}**",
                    f"- {ROBUX_EMOJI} **{format_robux(entry.robux_purchased)}**",
                    f"- {PIX_EMOJI} **{format_brl(entry.total_spent)}**",
                )
            )
        )
    embed.description = "\n".join(blocks)
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
