import discord
from discord import app_commands
from discord.ext import commands

from app.bot.checks import can_admin
from app.bot.views.admin import AdminPanelView
from app.bot.views.product_admin import send_product_management


class FullAdminPanelView(AdminPanelView):
    @discord.ui.button(label="Gerenciar produtos", style=discord.ButtonStyle.secondary)
    async def manage_products(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await send_product_management(interaction)


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="admin", description="Abre o painel administrativo da NEXTBUY")
    @app_commands.guild_only()
    async def admin(self, interaction: discord.Interaction) -> None:
        if not await can_admin(interaction):
            await interaction.response.send_message("Você não tem acesso ao painel.", ephemeral=True)
            return
        embed = discord.Embed(
            title="NEXTBUY • Administração",
            description="Configure a loja sem encher o Discord de comandos.",
        )
        await interaction.response.send_message(embed=embed, view=FullAdminPanelView(), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
