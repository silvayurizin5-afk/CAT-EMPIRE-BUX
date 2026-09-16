import discord

from app.bot.components_v2 import CardLayout, add_action_row
from app.db.session import SessionLocal
from app.services.calculator import format_brl, format_robux
from app.services.profiles import CustomerProfile, get_customer_profile

ROBUX_EMOJI = "<:ROBUXNextBuy:1549604652557934702>"
PIX_EMOJI = "<:PIX:1549632822388592663>"


def _profile_lines(profile: CustomerProfile) -> list[str]:
    lines = [
        f"**{PIX_EMOJI} Total gasto:** `{format_brl(profile.total_spent)}`",
        f"**{ROBUX_EMOJI} Robux:** `{format_robux(profile.robux_purchased)}`",
        f"**Compras:** `{profile.completed_orders}`",
        (
            f"**Posição geral:** `#{profile.leaderboard_position}`"
            if profile.leaderboard_position
            else "**Posição geral:** `Sem ranking`"
        ),
    ]
    if profile.completed_orders == 0:
        lines.append("Você ainda não concluiu nenhuma compra neste servidor.")
    if profile.games:
        lines.append("**Jogos**\n" + "\n".join(f"- **{name}**" for name in profile.games))
    if profile.recent_products:
        lines.append(
            "**Itens recentes**\n"
            + "\n".join(f"- **{name}**" for name in profile.recent_products)
        )
    return lines


def build_profile_card(display_name: str, profile: CustomerProfile) -> CardLayout:
    return CardLayout(
        title=f"Perfil — {display_name}",
        lines=_profile_lines(profile),
        footer="NEXTBUY • Perfil",
        timeout=180,
    )


def build_profile_embed(display_name: str, profile: CustomerProfile) -> discord.Embed:
    """Compatibilidade com chamadas antigas; a interface ativa usa Components V2."""
    return discord.Embed(
        title=f"Perfil — {display_name}",
        description="\n".join(_profile_lines(profile)),
        color=discord.Color.from_rgb(43, 45, 49),
    )


class LeaderboardView(discord.ui.LayoutView):
    def __init__(self, *, body: str | None = None) -> None:
        super().__init__(timeout=None)
        card = CardLayout(
            description=body or "## Leaderboard - NEXTBUY\nRanking sendo atualizado.",
            timeout=None,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)

        button = discord.ui.Button(
            label="Ver meu perfil",
            style=discord.ButtonStyle.secondary,
            custom_id="nextbuy:leaderboard:profile",
        )
        button.callback = self._profile
        add_action_row(self.container, button)

    async def _profile(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session:
            profile = await get_customer_profile(
                session,
                guild_id=interaction.guild.id,
                discord_user_id=interaction.user.id,
            )
        await interaction.edit_original_response(
            content=None,
            embeds=[],
            view=build_profile_card(interaction.user.display_name, profile),
        )
