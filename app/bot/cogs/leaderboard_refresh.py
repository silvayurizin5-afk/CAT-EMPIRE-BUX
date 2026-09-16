import logging

from discord.ext import commands, tasks

from app.bot.workflows.leaderboard import refresh_leaderboard

logger = logging.getLogger(__name__)


class LeaderboardRefreshCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.refresh_leaderboards.start()

    async def cog_unload(self) -> None:
        self.refresh_leaderboards.cancel()

    @tasks.loop(hours=1)
    async def refresh_leaderboards(self) -> None:
        for guild in self.bot.guilds:
            try:
                await refresh_leaderboard(guild)
            except Exception:
                logger.exception(
                    "Falha ao atualizar leaderboard automaticamente no servidor %s",
                    guild.id,
                )

    @refresh_leaderboards.before_loop
    async def before_refresh_leaderboards(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LeaderboardRefreshCog(bot))
