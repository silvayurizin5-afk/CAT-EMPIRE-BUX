import discord

from app.db.models import TermsDocument
from app.db.session import SessionLocal
from app.services.terms import accept_current_terms, list_missing_terms
from app.services.users import get_or_create_user


def _terms_snapshot(terms: list[TermsDocument]) -> frozenset[tuple[int, int]]:
    return frozenset((item.id, item.version) for item in terms)


def build_terms_required_embed(terms: list[TermsDocument]) -> discord.Embed:
    embed = discord.Embed(
        title="Termos necessários",
        description=(
            "Antes de concluir a compra, leia os termos vigentes abaixo. "
            "Use o seletor para abrir cada seção e depois aceite para continuar."
        ),
    )
    lines = [
        f"• {item.emoji + ' ' if item.emoji else ''}**{item.title}** — versão {item.version}"
        for item in terms
    ]
    embed.add_field(
        name="Pendentes",
        value="\n".join(lines) if lines else "Nenhum termo pendente.",
        inline=False,
    )
    return embed


class TermsGateSelect(discord.ui.Select):
    def __init__(self, terms: list[TermsDocument]) -> None:
        self._terms = {item.id: item for item in terms}
        options = [
            discord.SelectOption(
                label=item.title[:100],
                value=str(item.id),
                description=f"Versão {item.version}"[:100],
                emoji=item.emoji or None,
            )
            for item in terms[:25]
        ]
        super().__init__(
            placeholder="Leia os termos antes de aceitar",
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        term = self._terms.get(int(self.values[0]))
        if term is None:
            await interaction.response.send_message("Termo não encontrado.", ephemeral=True)
            return
        embed = discord.Embed(title=term.title, description=term.content)
        embed.set_footer(text=f"Versão {term.version}")
        await interaction.response.edit_message(embed=embed, view=self.view)


class TermsGateView(discord.ui.View):
    def __init__(self, terms: list[TermsDocument], resume_view: discord.ui.View) -> None:
        if not terms:
            raise ValueError("TermsGateView exige pelo menos um termo")
        super().__init__(timeout=300)
        self._snapshot = _terms_snapshot(terms)
        self._resume_view = resume_view
        self.add_item(TermsGateSelect(terms))

    @discord.ui.button(
        label="Aceitar termos",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def accept(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            user = await get_or_create_user(session, interaction.user.id)
            current_missing = await list_missing_terms(
                session,
                guild_id=interaction.guild.id,
                user_id=user.id,
            )
            current_snapshot = _terms_snapshot(current_missing)
            if current_snapshot == self._snapshot:
                await accept_current_terms(
                    session,
                    guild_id=interaction.guild.id,
                    user_id=user.id,
                )

        if current_snapshot != self._snapshot:
            if current_missing:
                await interaction.edit_original_response(
                    content="Os termos mudaram. Revise a versão atual antes de continuar.",
                    embed=build_terms_required_embed(current_missing),
                    view=TermsGateView(current_missing, self._resume_view),
                )
                return
            await interaction.edit_original_response(
                content="Os termos pendentes foram removidos. Confirme a compra novamente.",
                embed=None,
                view=self._resume_view,
            )
            return

        await interaction.edit_original_response(
            content="Termos aceitos. Agora confirme a compra novamente.",
            embed=None,
            view=self._resume_view,
        )


async def require_current_terms(
    interaction: discord.Interaction,
    *,
    resume_view: discord.ui.View,
) -> bool:
    if interaction.guild is None:
        return False

    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, interaction.user.id)
        missing = await list_missing_terms(
            session,
            guild_id=interaction.guild.id,
            user_id=user.id,
        )

    if not missing:
        return True

    await interaction.edit_original_response(
        content="Você precisa aceitar os termos vigentes antes de concluir esta compra.",
        embed=build_terms_required_embed(missing),
        view=TermsGateView(missing, resume_view),
    )
    return False
