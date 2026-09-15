import re
from datetime import UTC, datetime

import discord
from discord.ext import commands, tasks
from sqlalchemy import select

from app.db.models import GuildConfig
from app.db.session import SessionLocal
from app.services.feedback import (
    due_channel_reminders,
    due_dm_reminders,
    list_pending_orders_for_feedback,
    submit_feedback,
)

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
        self.reminder_worker.start()

    def cog_unload(self) -> None:
        self.reminder_worker.cancel()

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
                    "Não achei um único pedido pendente com esse código. "
                    "Use o código de 8 caracteres mostrado no lembrete."
                )
            else:
                examples = ", ".join(f"`#{str(row[0].id)[:8]}`" for row in pending_rows[:5])
                first_short_id = str(pending_rows[0][0].id)[:8]
                guidance = (
                    "Você tem mais de uma avaliação pendente. Informe o pedido, por exemplo: "
                    f"`5 #{first_short_id} - ótimo atendimento`. Pendentes: {examples}."
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

    @tasks.loop(seconds=60)
    async def reminder_worker(self) -> None:
        now = datetime.now(UTC)
        async with SessionLocal() as session:
            channel_rows = await due_channel_reminders(session, now=now)
            dm_rows = await due_dm_reminders(session, now=now)

        for reminder, order, user, config in channel_rows:
            guild = self.bot.get_guild(order.guild_id)
            if guild is None or not config.feedback_channel_id:
                continue
            channel = guild.get_channel(config.feedback_channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue
            short_id = str(order.id)[:8]
            try:
                await channel.send(
                    (
                        f"<@{user.discord_user_id}> quando puder, avalie o pedido "
                        f"`{short_id}`. Use **1 a 5 - seu feedback**. "
                        f"Se tiver mais de um pendente, use **5 #{short_id} - seu feedback**."
                    ),
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
            except discord.HTTPException:
                continue
            async with SessionLocal() as session, session.begin():
                db_reminder = await session.get(type(reminder), reminder.id)
                if db_reminder and db_reminder.completed_at is None:
                    db_reminder.channel_mention_sent_at = now

        for reminder, order, user, _config in dm_rows:
            discord_user = self.bot.get_user(user.discord_user_id)
            if discord_user is None:
                try:
                    discord_user = await self.bot.fetch_user(user.discord_user_id)
                except discord.NotFound:
                    continue
            short_id = str(order.id)[:8]
            try:
                await discord_user.send(
                    f"Você ainda tem uma avaliação pendente do pedido `{short_id}` na NEXTBUY. "
                    f"No canal de feedbacks, você pode usar `5 #{short_id} - seu feedback`."
                )
            except discord.Forbidden:
                pass
            async with SessionLocal() as session, session.begin():
                db_reminder = await session.get(type(reminder), reminder.id)
                if db_reminder and db_reminder.completed_at is None:
                    db_reminder.dm_sent_at = now

    @reminder_worker.before_loop
    async def before_reminder_worker(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FeedbackCog(bot))
