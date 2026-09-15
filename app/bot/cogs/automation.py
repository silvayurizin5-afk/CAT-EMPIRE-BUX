import time

import discord
from discord.ext import commands

from app.db.session import SessionLocal
from app.services.calculator import CalculationKind, parse_calculation_message
from app.services.configs import get_or_create_guild_config
from app.services.faq import find_auto_reply
from app.services.quotes import quote_credits_for_robux, quote_robux_from_credits


class AutomationCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._faq_cooldowns: dict[tuple[int, int, int], float] = {}

    async def _handle_calculator(self, message: discord.Message) -> bool:
        if message.guild is None:
            return False
        request = parse_calculation_message(message.content)
        if request is None:
            return False

        async with SessionLocal() as session:
            if request.kind == CalculationKind.CREDITS_TO_ROBUX:
                quotes = await quote_robux_from_credits(
                    session,
                    guild_id=message.guild.id,
                    credits=request.amount,
                )
                if not quotes:
                    return False
                embed = discord.Embed(
                    title=f"💰 Cálculo • R$ {request.amount:.2f}",
                    description="**1 crédito = R$ 1,00.** Valores calculados pelas cotações atuais.",
                )
                for rate, robux in quotes:
                    extra = f"\n{rate.delivery_label}" if rate.delivery_label else ""
                    embed.add_field(
                        name=rate.label,
                        value=f"**{robux} Robux**{extra}",
                        inline=False,
                    )
            else:
                quotes = await quote_credits_for_robux(
                    session,
                    guild_id=message.guild.id,
                    robux=int(request.amount),
                )
                if not quotes:
                    return False
                embed = discord.Embed(
                    title=f"💎 Cálculo • {int(request.amount)} Robux",
                    description="Preço estimado em créditos e reais. **1 crédito = R$ 1,00.**",
                )
                for rate, credits in quotes:
                    extra = f"\n{rate.delivery_label}" if rate.delivery_label else ""
                    embed.add_field(
                        name=rate.label,
                        value=f"**{credits:.2f} créditos (R$ {credits:.2f})**{extra}",
                        inline=False,
                    )

        await message.channel.send(embed=embed)
        return True

    async def _handle_faq(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        async with SessionLocal() as session:
            reply = await find_auto_reply(
                session,
                guild_id=message.guild.id,
                message=message.content,
            )
        if reply is None:
            return

        key = (message.guild.id, message.author.id, reply.id)
        now = time.monotonic()
        last = self._faq_cooldowns.get(key, 0.0)
        if now - last < reply.cooldown_seconds:
            return
        self._faq_cooldowns[key] = now

        title = f"{reply.emoji} {reply.title}" if reply.emoji else reply.title
        embed = discord.Embed(title=title, description=reply.content)
        await message.channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        async with SessionLocal() as session:
            config = await get_or_create_guild_config(session, message.guild.id)
            calculator_channel_id = config.calculator_channel_id
            faq_channel_id = config.faq_channel_id

        if calculator_channel_id and message.channel.id == calculator_channel_id:
            handled = await self._handle_calculator(message)
            if handled:
                return

        if faq_channel_id and message.channel.id == faq_channel_id:
            await self._handle_faq(message)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutomationCog(bot))
