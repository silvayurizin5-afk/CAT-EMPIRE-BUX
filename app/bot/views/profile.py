import discord

from app.db.session import SessionLocal
from app.services.calculator import format_brl, format_robux
from app.services.profiles import CustomerProfile, get_customer_profile

ROBUX_EMOJI = "<:ROBUXNextBuy:1549604652557934702>"
PIX_EMOJI = "<:PIX:1549632822388592663>"
DEFAULT_ACCENT = 0x2B2D31


def profile_text(display_name: str, profile: CustomerProfile) -> str:
    lines = [
        f"## Perfil — {display_name}",
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
        lines.append("\nVocê ainda não concluiu nenhuma compra neste servidor.")
    if profile.games:
        lines.append("\n**Jogos**\n" + "\n".join(f"- **{name}**" for name in profile.games))
    if profile.recent_products:
        lines.append(
            "\n**Itens recentes**\n"
            + "\n".join(f"- **{name}**" for name in profile.recent_products)
        )
    return "\n".join(lines)


def build_profile_embed(display_name: str, profile: CustomerProfile) -> discord.Embed:
    """Compatibility helper for legacy callers. New UI uses profile_view()."""
    embed = discord.Embed(
        title=f"Perfil — {display_name}",
        description=profile_text(display_name, profile).split("\n", 1)[1],
        color=discord.Color(DEFAULT_ACCENT),
    )
    return embed


def profile_view(display_name: str, profile: CustomerProfile) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(profile_text(display_name, profile)[:4000]),
            accent_color=DEFAULT_ACCENT,
        )
    )
    return view


class LeaderboardView(discord.ui.LayoutView):
    def __init__(self, *, body: str | None = None) -> None:
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label="Ver meu perfil",
            style=discord.ButtonStyle.secondary,
            custom_id="nextbuy:leaderboard:profile",
        )
        button.callback = self._profile
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    body or "## Leaderboard - NEXTBUY\nRanking sendo atualizado."
                ),
                discord.ui.ActionRow(button),
                accent_color=DEFAULT_ACCENT,
            )
        )

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
            embed=None,
            view=profile_view(interaction.user.display_name, profile),
        )
