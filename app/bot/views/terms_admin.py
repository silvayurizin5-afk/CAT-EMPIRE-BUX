import discord

from app.bot.components_v2 import CardLayout, add_action_row, add_select_row
from app.bot.emoji import select_option_emoji
from app.bot.views.ticket_admin import FinalAdminPanelView
from app.bot.views.terms_public import normalize_http_url
from app.db.models import TermsDocument
from app.db.session import SessionLocal
from app.services.audit import write_audit_log
from app.services.catalog import upsert_terms
from app.services.configs import get_or_create_guild_config
from app.services.terms import list_terms, set_terms_active


def _terms_lines(terms: TermsDocument) -> list[str]:
    return [
        terms.content[:1800],
        f"**Código:** `{terms.code}`",
        f"**Versão:** `{terms.version}`",
        f"**Status:** `{'Ativo' if terms.active else 'Desativado'}`",
        f"**Descrição do seletor:** {(terms.summary or '—')[:300]}",
        f"**Emoji:** {(terms.emoji or '—')[:128]}",
        f"**Imagem:** {(terms.image_url or '—')[:300]}",
        f"**Link:** {(terms.link_url or '—')[:300]}",
        f"**Submensagem ephemeral:** {(terms.ephemeral_message or '—')[:500]}",
    ]


def build_terms_admin_embed(terms: TermsDocument) -> discord.Embed:
    """Compatibilidade com telas antigas; a interface ativa usa Components V2."""
    return discord.Embed(
        title=f"Termo • {terms.title}",
        description="\n".join(_terms_lines(terms)),
        color=discord.Color.from_rgb(43, 45, 49),
    )


class TermsPanelSettingsModal(discord.ui.Modal):
    def __init__(self, config) -> None:
        super().__init__(title="Painel público de termos")
        self.title_input = discord.ui.TextInput(
            label="Título",
            max_length=160,
            default=config.terms_panel_title[:160],
        )
        self.description_input = discord.ui.TextInput(
            label="Descrição",
            style=discord.TextStyle.paragraph,
            max_length=1800,
            default=config.terms_panel_description[:1800],
        )
        self.emoji_input = discord.ui.TextInput(
            label="Emoji",
            required=False,
            max_length=128,
            default=(config.terms_panel_emoji or "")[:128],
            placeholder="Unicode, <:nome:id>, <a:nome:id>, :nome:, nome ou ID",
        )
        self.banner_input = discord.ui.TextInput(
            label="URL do banner",
            required=False,
            max_length=2000,
            default=(config.terms_panel_banner_url or "")[:2000],
        )
        self.thumbnail_input = discord.ui.TextInput(
            label="URL da thumbnail",
            required=False,
            max_length=2000,
            default=(config.terms_panel_thumbnail_url or "")[:2000],
        )
        self.add_item(self.title_input)
        self.add_item(self.description_input)
        self.add_item(self.emoji_input)
        self.add_item(self.banner_input)
        self.add_item(self.thumbnail_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            banner_url = normalize_http_url(str(self.banner_input))
            thumbnail_url = normalize_http_url(str(self.thumbnail_input))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        title = str(self.title_input).strip()
        description = str(self.description_input).strip()
        if not title or not description:
            await interaction.response.send_message(
                "Título e descrição são obrigatórios.",
                ephemeral=True,
            )
            return

        async with SessionLocal() as session, session.begin():
            config = await get_or_create_guild_config(session, interaction.guild.id)
            config.terms_panel_title = title
            config.terms_panel_description = description
            config.terms_panel_emoji = str(self.emoji_input).strip()
            config.terms_panel_banner_url = banner_url
            config.terms_panel_thumbnail_url = thumbnail_url
            await write_audit_log(
                session,
                guild_id=interaction.guild.id,
                actor_discord_id=interaction.user.id,
                action="terms.panel.update",
                target_type="guild",
                target_id=str(interaction.guild.id),
                details={
                    "banner_url": banner_url,
                    "thumbnail_url": thumbnail_url,
                },
            )

        await interaction.response.send_message(
            "Painel público de termos atualizado.",
            ephemeral=True,
        )


class TermsPresentationModal(discord.ui.Modal):
    def __init__(self, terms: TermsDocument) -> None:
        super().__init__(title=f"Aparência • {terms.title[:30]}")
        self.terms_id = terms.id
        self.summary_input = discord.ui.TextInput(
            label="Descrição no seletor",
            required=False,
            max_length=100,
            default=(terms.summary or "")[:100],
        )
        self.emoji_input = discord.ui.TextInput(
            label="Emoji",
            required=False,
            max_length=128,
            default=(terms.emoji or "")[:128],
            placeholder="Unicode, custom, animado, :alias:, nome ou ID",
        )
        self.image_input = discord.ui.TextInput(
            label="URL de imagem/GIF",
            required=False,
            max_length=2000,
            default=(terms.image_url or "")[:2000],
        )
        self.link_input = discord.ui.TextInput(
            label="URL de link",
            required=False,
            max_length=2000,
            default=(terms.link_url or "")[:2000],
        )
        self.add_item(self.summary_input)
        self.add_item(self.emoji_input)
        self.add_item(self.image_input)
        self.add_item(self.link_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            image_url = normalize_http_url(str(self.image_input))
            link_url = normalize_http_url(str(self.link_input))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        async with SessionLocal() as session, session.begin():
            terms = await session.get(TermsDocument, self.terms_id)
            if terms is None or terms.guild_id != interaction.guild.id:
                await interaction.response.send_message(
                    "Termo não encontrado.",
                    ephemeral=True,
                )
                return
            terms.summary = str(self.summary_input).strip()
            terms.emoji = str(self.emoji_input).strip() or None
            terms.image_url = image_url
            terms.link_url = link_url
            await write_audit_log(
                session,
                guild_id=interaction.guild.id,
                actor_discord_id=interaction.user.id,
                action="terms.presentation.update",
                target_type="terms",
                target_id=str(terms.id),
                details={
                    "summary": terms.summary,
                    "image_url": image_url,
                    "link_url": link_url,
                },
            )

        await interaction.response.send_message(
            "Aparência do termo atualizada sem alterar a versão jurídica.",
            ephemeral=True,
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

    @discord.ui.button(label="Aparência", style=discord.ButtonStyle.secondary)
    async def appearance(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        async with SessionLocal() as session:
            terms = await session.get(TermsDocument, self.terms_id)
        if terms is None or terms.guild_id != interaction.guild.id:
            await interaction.response.send_message("Termo não encontrado.", ephemeral=True)
            return
        await interaction.response.send_modal(TermsPresentationModal(terms))

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
    def __init__(self, terms: list[TermsDocument], guild: discord.Guild) -> None:
        options = [
            discord.SelectOption(
                label=item.title[:100],
                value=str(item.id),
                description=(
                    item.summary
                    or f"v{item.version} • {'ativo' if item.active else 'desativado'} • {item.code}"
                )[:100],
                emoji=select_option_emoji(item.emoji, guild),
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
    def __init__(self, terms: list[TermsDocument], guild: discord.Guild) -> None:
        super().__init__(timeout=180)
        card = CardLayout(
            title="Gerenciar termos",
            description=(
                "Escolha um termo para editar a versão, aparência, emoji, imagens, "
                "links ou status."
            ),
            timeout=180,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)
        add_select_row(self.container, TermsManageSelect(terms, guild))


async def send_terms_management(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    async with SessionLocal() as session:
        terms = await list_terms(session, guild_id=interaction.guild.id)
    if not terms:
        await interaction.edit_original_response(
            content="Nenhum termo cadastrado. Use **Criar ou atualizar termo** primeiro.",
            view=None,
        )
        return
    await interaction.edit_original_response(
        content=None,
        embeds=[],
        view=TermsManagementView(terms, interaction.guild),
    )


async def send_terms_panel_settings(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    async with SessionLocal() as session, session.begin():
        config = await get_or_create_guild_config(session, interaction.guild.id)
    await interaction.response.send_modal(TermsPanelSettingsModal(config))


class CompleteAdminPanelView(FinalAdminPanelView):
    @discord.ui.button(label="Gerenciar termos", style=discord.ButtonStyle.primary)
    async def manage_terms(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await send_terms_management(interaction)
