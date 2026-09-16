import discord

from app.bot.components_v2 import CardLayout, add_action_row, add_select_row
from app.bot.emoji import select_option_emoji
from app.db.models import TermsDocument
from app.db.session import SessionLocal
from app.services.terms import accept_current_terms, list_missing_terms
from app.services.users import get_or_create_user


def _terms_snapshot(terms: list[TermsDocument]) -> frozenset[tuple[int, int]]:
    return frozenset((item.id, item.version) for item in terms)


def _terms_lines(terms: list[TermsDocument], selected: TermsDocument | None = None) -> list[str]:
    if selected is not None:
        return [
            selected.content,
            f"-# Versão {selected.version}",
        ]
    lines = [
        f"- {item.emoji + ' ' if item.emoji else ''}**{item.title}** — versão {item.version}"
        for item in terms
    ]
    return [
        "Antes de concluir a compra, leia os termos vigentes abaixo.",
        "Use o seletor para abrir cada seção e depois aceite para continuar.",
        "**Pendentes**",
        *(lines or ["Nenhum termo pendente."]),
    ]


class TermsGateSelect(discord.ui.Select):
    def __init__(self, terms: list[TermsDocument]) -> None:
        self._terms = {item.id: item for item in terms}
        options = [
            discord.SelectOption(
                label=item.title[:100],
                value=str(item.id),
                description=f"Versão {item.version}"[:100],
                emoji=select_option_emoji(item.emoji),
            )
            for item in terms[:25]
        ]
        super().__init__(
            placeholder="Leia os termos antes de aceitar",
            options=options,
            custom_id="nextbuy:terms:select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        term = self._terms.get(int(self.values[0]))
        if term is None:
            await interaction.response.send_message("Termo não encontrado.", ephemeral=True)
            return
        if isinstance(self.view, TermsGateView):
            self.view.set_selected(term)
            await interaction.response.edit_message(content=None, embeds=[], view=self.view)


class TermsGateView(discord.ui.LayoutView):
    def __init__(
        self,
        terms: list[TermsDocument],
        resume_view: discord.ui.View | discord.ui.LayoutView,
    ) -> None:
        if not terms:
            raise ValueError("TermsGateView exige pelo menos um termo")
        super().__init__(timeout=300)
        self._terms = terms
        self._terms_versions = _terms_snapshot(terms)
        self._resume_view = resume_view
        self._selected: TermsDocument | None = None
        self._render()

    def _render(self) -> None:
        self.clear_items()
        selected = self._selected
        card = CardLayout(
            title=selected.title if selected is not None else "Termos necessários",
            lines=_terms_lines(self._terms, selected),
            footer="NEXTBUY • Termos",
            timeout=300,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)

        add_select_row(self.container, TermsGateSelect(self._terms))
        accept = discord.ui.Button(
            label="Aceitar termos",
            style=discord.ButtonStyle.success,
            custom_id="nextbuy:terms:accept",
        )
        accept.callback = self._accept
        add_action_row(self.container, accept)

    def set_selected(self, term: TermsDocument) -> None:
        self._selected = term
        self._render()

    async def _accept(self, interaction: discord.Interaction) -> None:
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
            if current_snapshot == self._terms_versions:
                await accept_current_terms(
                    session,
                    guild_id=interaction.guild.id,
                    user_id=user.id,
                )

        if current_snapshot != self._terms_versions:
            if current_missing:
                await interaction.edit_original_response(
                    content=None,
                    embeds=[],
                    view=TermsGateView(current_missing, self._resume_view),
                )
                return
            await interaction.edit_original_response(
                content=None,
                embeds=[],
                view=self._resume_view,
            )
            return

        await interaction.edit_original_response(
            content=None,
            embeds=[],
            view=self._resume_view,
        )


async def require_current_terms(
    interaction: discord.Interaction,
    *,
    resume_view: discord.ui.View | discord.ui.LayoutView,
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
        content=None,
        embeds=[],
        view=TermsGateView(missing, resume_view),
    )
    return False
