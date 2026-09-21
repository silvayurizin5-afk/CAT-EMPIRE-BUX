import discord

from app.bot.components_v2 import DEFAULT_ACCENT, add_action_row
from app.bot.emoji import (
    emoji_display_value,
    resolve_guild_emoji_aliases,
    select_option_emoji,
)
from app.bot.views.terms_public import safe_http_url
from app.db.models import TermsDocument
from app.db.session import SessionLocal
from app.services.terms import accept_current_terms, list_missing_terms
from app.services.users import get_or_create_user


def _terms_snapshot(terms: list[TermsDocument]) -> frozenset[tuple[int, int]]:
    return frozenset((item.id, item.version) for item in terms)


class TermsGateSelect(discord.ui.Select):
    def __init__(
        self,
        terms: list[TermsDocument],
        guild: discord.Guild | None = None,
    ) -> None:
        self._terms = {item.id: item for item in terms}
        options = [
            discord.SelectOption(
                label=item.title[:100],
                value=str(item.id),
                description=(item.summary or "Leia este termo")[:100],
                emoji=select_option_emoji(item.emoji, guild),
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
        guild: discord.Guild | None = None,
    ) -> None:
        if not terms:
            raise ValueError("TermsGateView exige pelo menos um termo")
        super().__init__(timeout=300)
        self._terms = terms
        self._terms_versions = _terms_snapshot(terms)
        self._resume_view = resume_view
        self._guild = guild
        self._selected: TermsDocument | None = None
        self._render()

    def _render(self) -> None:
        self.clear_items()
        selected = self._selected
        children: list[discord.ui.Item] = []

        if selected is None:
            pending = [
                (
                    f"- {emoji_display_value(item.emoji, self._guild) + ' ' if item.emoji else ''}"
                    f"**{item.title}**"
                )
                for item in self._terms
            ]
            body = "\n".join(
                [
                    "## Termos necessários",
                    "Antes de concluir a compra, leia os termos vigentes abaixo.",
                    "Use o seletor para abrir cada seção e depois aceite para continuar.",
                    "",
                    "**Pendentes**",
                    *pending,
                ]
            )
            children.append(discord.ui.TextDisplay(body))
        else:
            emoji = emoji_display_value(selected.emoji, self._guild)
            prefix = f"{emoji} " if emoji else ""
            content = resolve_guild_emoji_aliases(selected.content, self._guild)
            children.append(discord.ui.TextDisplay(f"## {prefix}{selected.title}"))
            children.append(discord.ui.TextDisplay(content or "\u200b"))

            image_url = safe_http_url(selected.image_url)
            if image_url:
                children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(image_url)))

            if selected.ephemeral_message.strip():
                children.append(discord.ui.Separator())
                children.append(
                    discord.ui.TextDisplay(
                        resolve_guild_emoji_aliases(
                            selected.ephemeral_message,
                            self._guild,
                        )
                    )
                )

            link_url = safe_http_url(selected.link_url)
            if link_url:
                children.append(
                    discord.ui.ActionRow(
                        discord.ui.Button(
                            label="Abrir link",
                            style=discord.ButtonStyle.link,
                            url=link_url,
                        )
                    )
                )

            children.append(discord.ui.Separator())
            children.append(discord.ui.TextDisplay("-# NEXTBUY • Termos"))

        self.container = discord.ui.Container(*children, accent_colour=DEFAULT_ACCENT)
        self.add_item(self.container)
        add_action_row(self.container, TermsGateSelect(self._terms, self._guild))
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
                    view=TermsGateView(
                        current_missing,
                        self._resume_view,
                        interaction.guild,
                    ),
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
        view=TermsGateView(missing, resume_view, interaction.guild),
    )
    return False
