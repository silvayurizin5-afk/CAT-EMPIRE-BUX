import discord
from discord import app_commands
from discord.ext import commands

from app.bot.checks import can_admin
from app.bot.views.admin_compact_v2 import CompactAdminPanelView


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="admin", description="Abre o painel administrativo da NEXTBUY")
    @app_commands.guild_only()
    async def admin(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not await can_admin(interaction):
            await interaction.edit_original_response(
                content="Você não tem acesso ao painel.",
                embed=None,
                view=None,
            )
            return

        await interaction.edit_original_response(
            content=None,
            embed=None,
            view=CompactAdminPanelView(owner_id=interaction.user.id),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
