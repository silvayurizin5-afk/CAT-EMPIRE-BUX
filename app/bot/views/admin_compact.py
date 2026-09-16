import discord

from app.bot.components_v2 import CardLayout, add_select_row
from app.bot.views.admin_feedback import FeedbackSettingsModal
from app.bot.views.admin_forms import ConfigTargetView, RankRoleView, TermsModal
from app.bot.views.embed_builder import send_embed_builder
from app.bot.views.rank_admin import send_rank_tier_management
from app.bot.views.store_panel_admin import send_store_panel_admin
from app.bot.views.terms_admin import send_terms_management
from app.bot.views.ticket_admin import TicketMessagesModal
from app.db.session import SessionLocal
from app.services.configs import get_or_create_guild_config
from app.services.ticket_settings import get_effective_ticket_settings


ADMIN_ACTIONS = (
    (
        "store_panel",
        "Configurar loja",
        "Painel, produtos, cupons, preços, estoques e publicação",
    ),
    ("embed_builder", "Criar mensagem visual", "Editor visual para mensagens do servidor"),
    ("feedback", "Configurar feedbacks", "Emoji, lembretes e permissões"),
    ("ticket_messages", "Mensagens dos tickets", "Editar textos automáticos dos pedidos"),
    ("new_terms", "Criar ou atualizar termo", "Cadastrar uma nova versão de termo"),
    ("manage_terms", "Gerenciar termos", "Editar, ativar ou desativar termos"),
    ("new_rank", "Criar faixa de cliente", "Vincular faixa e cargo"),
    ("manage_ranks", "Gerenciar faixas", "Editar metas e ativação"),
    ("publish_ranking", "Publicar ranking", "Publicar ou atualizar no canal atual"),
    ("roles", "Configurar cargos", "Administrador, suporte, entrega e cliente"),
    ("channels", "Configurar canais", "Tickets, entregas, ranking e logs"),
)


def build_admin_embed() -> discord.Embed:
    """Compatibilidade temporária com telas administrativas legadas."""
    return discord.Embed(
        title="NEXTBUY • Administração",
        description=(
            "Selecione o que deseja configurar. A IA é configurada separadamente com `/ia`."
        ),
        color=discord.Color.from_rgb(43, 45, 49),
    )


class AdminActionSelect(discord.ui.Select):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(label=label, value=value, description=description)
            for value, label, description in ADMIN_ACTIONS
        ]
        super().__init__(
            placeholder="Selecione o que deseja configurar",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if isinstance(self.view, CompactAdminPanelView):
            await self.view.handle_action(interaction, self.values[0])


class CompactAdminPanelView(discord.ui.LayoutView):
    def __init__(self, *, owner_id: int) -> None:
        super().__init__(timeout=900)
        self.owner_id = owner_id
        card = CardLayout(
            title="NEXTBUY • Administração",
            description="Selecione abaixo o que deseja configurar neste servidor.",
            lines=[
                "- **Loja:** produtos, cupons, preços, estoques e publicação.",
                "- **Automação:** tickets, feedbacks, termos e faixas.",
                "- **IA:** use `/ia` para canais autorizados, suporte e sugestões.",
            ],
            footer="Painel privado • alterações valem para este servidor",
            timeout=900,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)
        add_select_row(self.container, AdminActionSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Esse painel pertence a outra pessoa.",
            ephemeral=True,
        )
        return False

    async def handle_action(self, interaction: discord.Interaction, action: str) -> None:
        if interaction.guild is None:
            return

        if action == "store_panel":
            await send_store_panel_admin(interaction)
            return

        if action == "roles":
            await interaction.response.send_message(
                "Escolha qual cargo configurar:",
                view=ConfigTargetView(kind="role"),
                ephemeral=True,
            )
            return

        if action == "channels":
            await interaction.response.send_message(
                "Escolha qual canal configurar:",
                view=ConfigTargetView(kind="channel"),
                ephemeral=True,
            )
            return

        if action == "feedback":
            async with SessionLocal() as session, session.begin():
                config = await get_or_create_guild_config(session, interaction.guild.id)
                emoji = config.feedback_emoji
                reminder = config.feedback_reminder_minutes
                dm_hours = config.feedback_dm_cooldown_hours
            await interaction.response.send_modal(
                FeedbackSettingsModal(
                    emoji=emoji,
                    reminder_minutes=reminder,
                    dm_cooldown_hours=dm_hours,
                )
            )
            return

        if action == "ticket_messages":
            async with SessionLocal() as session:
                settings = await get_effective_ticket_settings(
                    session,
                    guild_id=interaction.guild.id,
                )
            await interaction.response.send_modal(
                TicketMessagesModal(
                    title_template=settings.title_template,
                    instruction_template=settings.instruction_template,
                )
            )
            return

        if action == "new_terms":
            await interaction.response.send_modal(TermsModal())
            return

        if action == "manage_terms":
            await send_terms_management(interaction)
            return

        if action == "new_rank":
            await interaction.response.send_message(
                "Escolha o cargo que representa esta faixa:",
                view=RankRoleView(),
                ephemeral=True,
            )
            return

        if action == "manage_ranks":
            await send_rank_tier_management(interaction)
            return

        if action == "embed_builder":
            await send_embed_builder(interaction)
            return

        if action == "publish_ranking":
            if interaction.channel is None:
                await interaction.response.send_message("Canal inválido.", ephemeral=True)
                return
            from app.bot.workflows.leaderboard import refresh_leaderboard

            await interaction.response.defer(ephemeral=True, thinking=True)
            message = await refresh_leaderboard(interaction.guild, channel=interaction.channel)
            if message is None:
                await interaction.followup.send(
                    "Não consegui publicar o ranking.",
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                "Ranking publicado e vinculado. Atualização automática: 1 hora.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Essa opção ainda não está disponível.",
            ephemeral=True,
        )
