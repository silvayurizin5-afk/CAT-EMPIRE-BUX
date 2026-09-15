import discord
from discord.ext import commands

from app.core.config import settings


intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # necessário para calculadora/FAQ e feedback via mensagem

bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)


@bot.event
async def on_ready() -> None:
    print(f"NEXTBUY online como {bot.user}")


def run() -> None:
    bot.run(settings.discord_token)


if __name__ == "__main__":
    run()
