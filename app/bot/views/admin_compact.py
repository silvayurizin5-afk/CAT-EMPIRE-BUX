import discord
from sqlalchemy import func, select

from app.bot.views.admin_feedback import FeedbackSettingsModal
from app.bot.views.admin_forms import (
    AutoReplyModal,
    ConfigTargetView,
    RankRoleView,
    TermsModal,
)
from app.bot.views.automation_admin import send_auto_reply_management
from app.bot.views.embed_builder import send_embed_builder
from app.bot.views.rank_admin import send_rank_tier_management
from app.bot.views.store_panel_admin import send_store_panel_admin
from app.bot.views.terms_admin import send_terms_management
from app.bot.views.ticket_admin import TicketMessagesModal
from app.db.models import Feedback, Order
from app.db.session import SessionLocal
from app.services.configs import get_or_create_guild_config
from app.services.ticket_settings import get_effective_ticket_settings


ADMIN_ACTIONS = (
    (
        "store_panel",
        "Configurar loja",
        "Embed, produtos, cupons, preços, estoques e publicação",
    ),
    ("store_summary", "Resumo da loja", "Pedidos, entregas e feedbacks"),
    ("embed_builder", "Criar embed avulsa", "Abrir o editor visual com prévia ao vivo"),
    ("new_faq", "Criar resposta automática", "Cadastrar uma nova resposta do FAQ"),
    ("manage_faq", "Gerenciar FAQ", "Editar respostas e botões"),
    ("feedback", "Configurar feedbacks", "Emoji, lembretes e permissões"),
    ("ticket_messages", "Mensagens dos tickets", "Editar textos automáticos dos pedidos"),
    ("new_terms", "Criar ou atualizar termo", "Cadastrar uma nova versão de termo"),
    ("manage_terms", "Gerenciar termos", "Editar, ativar ou desativar termos"),
    ("new_rank", "Criar faixa de cliente", "Vincular faixa e cargo"),
    ("manage_ranks", "Gerenciar faixas", "Editar metas e ativação"),
    ("publish_ranking", "Publicar ranking", "Publicar ou atualizar no canal atual"),
    ("roles", "Configurar cargos", "Administrador, suporte, entrega e cliente"),
    ("channels", "Configurar canais", "Tickets, entregas, FAQ, ranking e logs"),
)


def build_admin_embed() -> discord.Embed:
    embed = discord.Embed(
        title="NEXTBUY • Administração",
        description=(
            "Selecione abaixo o que deseja configurar. A loja fica centralizada em "
            "uma única área, sem cotações ou créditos internos."
        ),
        color=discord.Color.from_rgb(43, 45, 49),
    )
    embed.add_field(
        name="Loja",
        value="Embed única, produtos, cupons, preços em reais, estoques e publicação.",
        inline=False,
    )
    embed.add_field(
        name="Automação",
        value="Calculadora, FAQ, feedbacks, tickets, termos e faixas de cliente.",
        inline=False,
    )
    embed.add_field(
        name="Servidor",
        value="Cargos, canais, ranking e criação visual avulsa.",
        inline=False,
    )
    embed.set_footer(text="Painel privado • alterações valem para este servidor")
    return embed


async def build_store_summary(guild_id: int) -> discord.Embed:
    async with SessionLocal() as session:
        delivered = await session.scalar(
            select(func.count(Order.id)).where(
                Order.guild_id == guild_id,
                Order.status == "delivered",
            )
        )
        pending = await session.scalar(
            select(func.count(Order.id)).where(
                Order.guild_id == guild_id,
                Order.status == "pending",
            )
        )
        confirmed = await session.scalar(
            select(func.count(Order.id)).where(
                Order.guild_id == guild_id,
                Order.status.in_(["paid", "processing"]),
            )
        )
        feedbacks = await session.scalar(
            select(func.count(Feedback.id))
            .join(Order, Feedback.order_id == Order.id)
            .where(Order.guild_id == guild_id)
        )

    embed = discord.Embed(
        title="NEXTBUY • Resumo da loja",
        description="Visão rápida da operação atual.",
        color=discord.Color.from_rgb(43, 45, 49),
    )
    embed.add_field(name="Entregues", value=str(delivered or 0), inline=True)
    embed.add_field(name="Pendentes", value=str(pending or 0), inline=True)
    embed.add_field(name="Confirmados", value=str(confirmed or 0), inline=True)
    embed.add_field(name="Feedbacks", value=str(feedbacks or 0), inline=True)
    embed.set_footer(text="Confirmados = pagos/processando e ainda não entregues")
    return embed


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
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(self.view, CompactAdminPanelView):
            return
        await self.view.handle_action(interaction, self.values[0])


class CompactAdminPanelView(discord.ui.View):
    def __init__(self, *, owner_id: int) -> None:
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.add_item(AdminActionSelect())

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

        if action == "store_summary":
            await interaction.response.send_message(
                embed=await build_store_summary(interaction.guild.id),
                ephemeral=True,
            )
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

        if action == "new_faq":
            await interaction.response.send_modal(AutoReplyModal())
            return

        if action == "manage_faq":
            await send_auto_reply_management(interaction)
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
