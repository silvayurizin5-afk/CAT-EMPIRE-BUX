import discord

from app.bot.views.ticket_admin import FinalAdminPanelView
from app.db.models import TermsDocument
from app.db.session import SessionLocal
from app.services.audit import write_audit_log
from app.services.catalog import upsert_terms
from app.services.terms import list_terms, set_terms_active


def build_terms_admin_embed(terms: TermsDocument) -> discord.Embed:
    embed = discord.Embed(
        title=f"Termo • {terms.title}",
        description=terms.content[:4000],
    )
    embed.add_field(name="Código", value=f"`{terms.code}`")
    embed.add_field(name="Versão", value=str(terms.version))
    embed.add_field(name="Status", value="Ativo" if terms.active else "Desativado")
    if terms.emoji:
        embed.set_footer(text=f"Emoji: {terms.emoji}")
    return embed


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
        self.add_item(self.title_input)
        self.add_item(self.content_input)
        self.add_item(self.emoji_input)

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

        async with SessionLocal() as session, session.begin():
            terms = await session.get(TermsDocument, self.terms_id)
            if terms is None or terms.guild_id != interaction.guild.id:
                await interaction.response.send_message("Termo não encontrado.", ephemeral=True)
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

        await interaction.response.send_message(
            f"Termo atualizado para a versão **{updated.version}**. "
            "Quem tinha aceitado a versão anterior precisará aceitar a nova antes da próxima compra.",
            ephemeral=True,
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
        async with SessionLocal() as session, session.begin():
            terms = await session.get(TermsDocument, self.terms_id)
            if terms is None or terms.guild_id != interaction.guild.id:
                await interaction.response.send_message("Termo não encontrado.", ephemeral=True)
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
        await interaction.response.edit_message(
            content=None,
            embed=build_terms_admin_embed(terms),
            view=TermsActionsView(terms.id),
        )


class TermsManageSelect(discord.ui.Select):
    def __init__(self, terms: list[TermsDocument]) -> None:
        options = [
            discord.SelectOption(
                label=item.title[:100],
                value=str(item.id),
                description=(
                    f"v{item.version} • {'ativo' if item.active else 'desativado'} • {item.code}"
                )[:100],
                emoji=item.emoji or None,
            )
            for item in terms[:25]
        ]
        super().__init__(placeholder="Escolha o termo", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        terms_id = int(self.values[0])
        async with SessionLocal() as session:
            terms = await session.get(TermsDocument, terms_id)
        if terms is None or terms.guild_id != interaction.guild.id:
            await interaction.response.edit_message(content="Termo não encontrado.", view=None)
            return
        await interaction.response.edit_message(
            content=None,
            embed=build_terms_admin_embed(terms),
            view=TermsActionsView(terms.id),
        )


class TermsManagementView(discord.ui.View):
    def __init__(self, terms: list[TermsDocument]) -> None:
        super().__init__(timeout=180)
        self.add_item(TermsManageSelect(terms))


async def send_terms_management(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    async with SessionLocal() as session:
        terms = await list_terms(session, guild_id=interaction.guild.id)
    if not terms:
        await interaction.response.send_message(
            "Nenhum termo cadastrado. Use **Termos** primeiro.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        "Escolha um termo para editar ou ativar/desativar:",
        view=TermsManagementView(terms),
        ephemeral=True,
    )


class CompleteAdminPanelView(FinalAdminPanelView):
    @discord.ui.button(label="Gerenciar termos", style=discord.ButtonStyle.primary)
    async def manage_terms(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await send_terms_management(interaction)
