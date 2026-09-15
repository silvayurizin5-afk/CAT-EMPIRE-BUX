import discord
from discord import app_commands
from discord.ext import commands

from app.bot.checks import can_admin
from app.bot.views.embed_builder import EmbedBuilderLauncherButton
from app.bot.views.rank_admin import FullAdminPanelView


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
            title="🛠️ NEXTBUY • Administração",
            description=(
                "Central de configuração da loja. Use os botões abaixo para criar, editar "
                "e publicar tudo sem espalhar comandos pelo servidor."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="🛍️ Loja",
            value="Produtos, cotações, termos, ranking e painéis.",
            inline=True,
        )
        embed.add_field(
            name="🤖 Automação",
            value="FAQ, feedbacks, tickets, canais e cargos.",
            inline=True,
        )
        embed.add_field(
            name="✨ Criação visual",
            value="Monte embeds com prévia ao vivo, imagens, campos e botões.",
            inline=False,
        )
        embed.set_footer(text="NEXTBUY • Alterações valem para este servidor")

        view = FullAdminPanelView()
        view.add_item(EmbedBuilderLauncherButton())
        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
