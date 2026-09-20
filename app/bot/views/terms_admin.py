import discord

from app.bot.components_v2 import CardLayout, add_action_row, add_select_row
from app.bot.emoji import select_option_emoji
from app.bot.views.ticket_admin import FinalAdminPanelView
from app.db.models import TermsDocument
from app.db.session import SessionLocal
from app.services.audit import write_audit_log
from app.services.catalog import upsert_terms
from app.services.terms import list_terms, set_terms_active


def _terms_lines(terms: TermsDocument) -> list[str]:
    return [
        terms.content[:4000],
        f"**Código:** `{terms.code}`",
        f"**Versão:** `{terms.version}`",
        f"**Status:** `{'Ativo' if terms.active else 'Desativado'}`",
        f"**Emoji:** {terms.emoji or '—'}",
        (
            f"**Submensagem ephemeral:** {terms.ephemeral_message}"
            if terms.ephemeral_message
            else "**Submensagem ephemeral:** —"
        ),
    ]


def build_terms_admin_embed(terms: TermsDocument) -> discord.Embed:
    """Compatibilidade com telas antigas; a interface ativa usa Components V2."""
    return discord.Embed(
        title=f"Termo • {terms.title}",
        description="\n".join(_terms_lines(terms)),
        color=discord.Color.from_rgb(43, 45, 49),
    )


class TermsEditModal(discord.ui.Modal):
    def __init__(self, terms: TermsDocument) -> None:
        super().__init__(title=f"Editar {terms.title[:35]}")
        self.terms_id = terms.id
        self.title_input = discord.ui.TextInput(
            label="Título",
            max_length=120,
            default=terms.title[:120],
        )
        self.content_input = discord.ui.TextInput(
            label="Conteúdo",
            style=discord.TextStyle.paragraph,
            max_length=4000,
            default=terms.content[:4000],
        )
        self.emoji_input = discord.ui.TextInput(
            label="Emoji",
            required=False,
            max_length=128,
            default=(terms.emoji or "")[:128],
        )
        self.ephemeral_input = discord.ui.TextInput(
            label="Submensagem ephemeral ao selecionar",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=1800,
            default=(terms.ephemeral_message or "")[:1800],
        )
        self.add_item(self.title_input)
        self.add_item(self.content_input)
        self.add_item(self.emoji_input)
        self.add_item(self.ephemeral_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        title = str(self.title_input).strip()
        content = str(self.content_input).strip()
        if not title or not content:
            await interaction.response.send_message(
                "Título e conteúdo são obrigatórios.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            terms = await session.get(TermsDocument, self.terms_id)
            if terms is None or terms.guild_id != interaction.guild.id:
                await interaction.edit_original_response(content="Termo não encontrado.")
                return
            was_active = terms.active
            old_version = terms.version
            updated = await upsert_terms(
                session,
                guild_id=interaction.guild.id,
                code=terms.code,
                title=title,
                content=content,
                emoji=str(self.emoji_input),
                ephemeral_message=str(self.ephemeral_input),
            )
            updated.active = was_active
            await write_audit_log(
                session,
                guild_id=interaction.guild.id,
                actor_discord_id=interaction.user.id,
                action="terms.update",
                target_type="terms",
                target_id=str(terms.id),
                details={
                    "code": terms.code,
                    "old_version": old_version,
                    "new_version": updated.version,
                    "active": was_active,
                },
            )

        await interaction.edit_original_response(
            content=(
                f"Termo atualizado para a versão **{updated.version}**. "
                "Quem aceitou a versão anterior precisará aceitar a nova antes da próxima compra."
            )
        )


class TermsActionsView(discord.ui.View):
    def __init__(self, terms_id: int) -> None:
        super().__init__(timeout=180)
        self.terms_id = terms_id

    @discord.ui.button(label="Editar / Nova versão", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        async with SessionLocal() as session:
            terms = await session.get(TermsDocument, self.terms_id)
        if terms is None or terms.guild_id != interaction.guild.id:
            await interaction.response.send_message("Termo não encontrado.", ephemeral=True)
            return
        await interaction.response.send_modal(TermsEditModal(terms))

    @discord.ui.button(label="Ativar/Desativar", style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer()
        async with SessionLocal() as session, session.begin():
            terms = await session.get(TermsDocument, self.terms_id)
            if terms is None or terms.guild_id != interaction.guild.id:
                await interaction.edit_original_response(content="Termo não encontrado.", view=None)
                return
            await set_terms_active(session, terms=terms, active=not terms.active)
            await write_audit_log(
                session,
                guild_id=interaction.guild.id,
                actor_discord_id=interaction.user.id,
                action="terms.toggle",
                target_type="terms",
                target_id=str(terms.id),
                details={"active": terms.active, "version": terms.version},
            )
        await interaction.edit_original_response(
            content=None,
            embeds=[],
            view=TermsActionsLayout(terms),
        )


class TermsActionsLayout(discord.ui.LayoutView):
    def __init__(self, terms: TermsDocument) -> None:
        super().__init__(timeout=180)
        card = CardLayout(
            title=f"Termo • {terms.title}",
            lines=_terms_lines(terms),
            footer="NEXTBUY • Termos",
            timeout=180,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)

        legacy = TermsActionsView(terms.id)
        buttons = list(legacy.children)
        for item in buttons:
            legacy.remove_item(item)
        if buttons:
            add_action_row(self.container, *buttons)


class TermsManageSelect(discord.ui.Select):
    def __init__(self, terms: list[TermsDocument]) -> None:
        options = [
            discord.SelectOption(
                label=item.title[:100],
                value=str(item.id),
                description=(
                    f"v{item.version} • {'ativo' if item.active else 'desativado'} • {item.code}"
                )[:100],
                emoji=select_option_emoji(item.emoji),
            )
            for item in terms[:25]
        ]
        super().__init__(placeholder="Escolha o termo", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer()
        terms_id = int(self.values[0])
        async with SessionLocal() as session:
            terms = await session.get(TermsDocument, terms_id)
        if terms is None or terms.guild_id != interaction.guild.id:
            await interaction.edit_original_response(content="Termo não encontrado.", view=None)
            return
        await interaction.edit_original_response(
            content=None,
            embeds=[],
            view=TermsActionsLayout(terms),
        )


class TermsManagementView(discord.ui.LayoutView):
    def __init__(self, terms: list[TermsDocument]) -> None:
        super().__init__(timeout=180)
        card = CardLayout(
            title="Gerenciar termos",
            description="Escolha um termo para editar, criar uma nova versão ou alterar o status.",
            timeout=180,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)
        add_select_row(self.container, TermsManageSelect(terms))


async def send_terms_management(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    async with SessionLocal() as session:
        terms = await list_terms(session, guild_id=interaction.guild.id)
    if not terms:
        await interaction.edit_original_response(
            content="Nenhum termo cadastrado. Use **Termos** primeiro.",
            view=None,
        )
        return
    await interaction.edit_original_response(
        content=None,
        embeds=[],
        view=TermsManagementView(terms),
    )


class CompleteAdminPanelView(FinalAdminPanelView):
    @discord.ui.button(label="Gerenciar termos", style=discord.ButtonStyle.primary)
    async def manage_terms(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await send_terms_management(interaction)
