import re

import discord
from discord.ext import commands
from sqlalchemy import select

from app.db.models import GuildConfig
from app.db.session import SessionLocal
from app.services.feedback import list_pending_orders_for_feedback, submit_feedback

FEEDBACK_RE = re.compile(
    r"^\s*([1-5])(?:\s*(?:/\s*5|estrelas?|stars?|⭐+))?"
    r"(?:\s+#([0-9a-f]{8}))?\s*[-:–—]?\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)


def parse_feedback_message(content: str) -> tuple[int, str | None, str] | None:
    match = FEEDBACK_RE.match(content)
    if match is None:
        return None
    stars = int(match.group(1))
    order_prefix = match.group(2).lower() if match.group(2) else None
    comment = match.group(3).strip()
    if not comment:
        return None
    return stars, order_prefix, comment


class FeedbackCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        async with SessionLocal() as session:
            config = await session.scalar(
                select(GuildConfig).where(GuildConfig.guild_id == message.guild.id)
            )
        if config is None or config.feedback_channel_id != message.channel.id:
            return

        parsed = parse_feedback_message(message.content)
        if parsed is None:
            return
        stars, order_prefix, comment = parsed

        async with SessionLocal() as session, session.begin():
            pending_rows = await list_pending_orders_for_feedback(
                session,
                guild_id=message.guild.id,
                discord_user_id=message.author.id,
            )
            if not pending_rows:
                return

            if order_prefix:
                matches = [
                    row for row in pending_rows if str(row[0].id).lower().startswith(order_prefix)
                ]
                pending = matches[0] if len(matches) == 1 else None
            elif len(pending_rows) == 1:
                pending = pending_rows[0]
            else:
                pending = None

            if pending is not None:
                order, user, _ = pending
                await submit_feedback(
                    session,
                    order_id=order.id,
                    user_id=user.id,
                    stars=stars,
                    comment=comment,
                    source="channel",
                    published_message_id=message.id,
                )

        if pending is None:
            if order_prefix:
                guidance = (
                    "Não consegui relacionar essa avaliação a uma compra pendente. "
                    "Use o botão de avaliação disponível no atendimento correspondente."
                )
            else:
                guidance = (
                    "Você tem mais de uma avaliação pendente. "
                    "Use o botão de avaliação no atendimento correspondente para garantir "
                    "que o feedback seja associado à compra correta."
                )
            try:
                await message.reply(guidance, mention_author=False, delete_after=25)
            except discord.HTTPException:
                pass
            return

        try:
            await message.add_reaction(config.feedback_emoji or "🐱")
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FeedbackCog(bot))
