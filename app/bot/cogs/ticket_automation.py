import re
from decimal import Decimal
from uuid import UUID

import discord
from discord.ext import commands
from sqlalchemy import select

from app.db.models import Order, OrderItem, User
from app.db.session import SessionLocal
from app.services.ticket_settings import (
    TicketTemplateContext,
    get_effective_ticket_settings,
    render_ticket_template,
)

_TOPIC = re.compile(r"^NEXTBUY order=([0-9a-fA-F-]{36}) customer=(\d{1,20})$")


def _credits(value: Decimal) -> str:
    return f"{value:.2f}".replace(".", ",") + " créditos"


class TicketAutomationCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        if not isinstance(channel, discord.TextChannel) or not channel.topic:
            return
        match = _TOPIC.fullmatch(channel.topic.strip())
        if match is None:
            return
        try:
            order_id = UUID(match.group(1))
            topic_customer_id = int(match.group(2))
        except ValueError:
            return

        async with SessionLocal() as session:
            order = await session.get(Order, order_id)
            if order is None or order.guild_id != channel.guild.id:
                return
            user = await session.get(User, order.user_id)
            if user is None or user.discord_user_id != topic_customer_id:
                return
            items = list(
                (
                    await session.scalars(
                        select(OrderItem)
                        .where(OrderItem.order_id == order.id)
                        .order_by(OrderItem.id)
                    )
                ).all()
            )
            settings = await get_effective_ticket_settings(
                session,
                guild_id=channel.guild.id,
            )

        item_lines = "\n".join(f"• {item.name_snapshot} × {item.quantity}" for item in items)
        context = TicketTemplateContext(
            customer=f"<@{user.discord_user_id}>",
            order=str(order.id)[:8],
            total=_credits(order.total_credits),
            items=item_lines or "—",
        )
        title = render_ticket_template(settings.title_template, context)[:256]
        description = render_ticket_template(settings.instruction_template, context)[:4096]
        embed = discord.Embed(title=title, description=description)
        embed.set_footer(text="NEXTBUY • mensagem automática")
        try:
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        except discord.HTTPException:
            return


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketAutomationCog(bot))
