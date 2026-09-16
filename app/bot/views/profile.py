import discord

from app.db.session import SessionLocal
from app.services.calculator import format_brl, format_robux
from app.services.profiles import CustomerProfile, get_customer_profile

ROBUX_EMOJI = "<:ROBUXNextBuy:1549604652557934702>"
PIX_EMOJI = "<:PIX:1549632822388592663>"


def build_profile_embed(display_name: str, profile: CustomerProfile) -> discord.Embed:
    embed = discord.Embed(
        title=f"Perfil — {display_name}",
        color=discord.Color.from_rgb(43, 45, 49),
    )
    embed.add_field(
        name=f"{PIX_EMOJI} Total gasto",
        value=f"`{format_brl(profile.total_spent)}`",
        inline=True,
    )
    embed.add_field(
        name=f"{ROBUX_EMOJI} Robux",
        value=f"`{format_robux(profile.robux_purchased)}`",
        inline=True,
    )
    embed.add_field(name="Compras", value=f"`{profile.completed_orders}`", inline=True)
    embed.add_field(
        name="Posição geral",
        value=(
            f"`#{profile.leaderboard_position}`"
            if profile.leaderboard_position
            else "`Sem ranking`"
        ),
        inline=True,
    )
    if profile.games:
        embed.add_field(
            name="Jogos",
            value="\n".join(f"- **{name}**" for name in profile.games),
            inline=False,
        )
    if profile.recent_products:
        embed.add_field(
            name="Itens recentes",
            value="\n".join(f"- **{name}**" for name in profile.recent_products),
            inline=False,
        )
    if profile.completed_orders == 0:
        embed.description = "Você ainda não concluiu nenhuma compra neste servidor."
    return embed


class LeaderboardView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ver meu perfil",
        style=discord.ButtonStyle.secondary,
        custom_id="nextbuy:leaderboard:profile",
    )
    async def profile(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        async with SessionLocal() as session:
            profile = await get_customer_profile(
                session,
                guild_id=interaction.guild.id,
                discord_user_id=interaction.user.id,
            )
        await interaction.response.send_message(
            embed=build_profile_embed(interaction.user.display_name, profile),
            ephemeral=True,
        )
