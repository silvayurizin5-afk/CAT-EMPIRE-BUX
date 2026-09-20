import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from app.bot.checks import can_admin, can_support
from app.bot.views.support import (
    SupportControls,
    SupportPanel,
    TicketReasonModal,
    report_error,
    run_action,
    send_support_list,
)
from app.bot.views.support_admin import send_support_admin
from app.bot.workflows.support import ticket_event
from app.core.guild_guard import is_store_guild
from app.db.models import GuildConfig
from app.db.session import SessionLocal
from app.db.ticket_models import SupportTicket
from app.services.audit import write_audit_log
from app.services.support_tickets import get_support_options


class SupportTicketsCog(commands.Cog):
    tickets = app_commands.Group(
        name="tickets", description="Atendimento e configuração de tickets", guild_only=True
    )

    def __init__(self, bot):
        self.bot = bot

    @tickets.command(name="configurar", description="Configurar painel, textos, regras e canais")
    async def configure(self, interaction: discord.Interaction):
        await send_support_admin(interaction)

    @tickets.command(name="publicar", description="Publicar o painel de abertura de atendimento")
    async def publish(
        self, interaction: discord.Interaction, canal: discord.TextChannel | None = None
    ):
        if not await can_admin(interaction):
            await interaction.response.send_message("Sem permissão.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            channel = canal or interaction.channel
            if (
                not isinstance(channel, discord.TextChannel)
                or channel.guild.id != interaction.guild_id
            ):
                raise ValueError("Escolha um canal de texto deste servidor.")
            async with SessionLocal() as session:
                options = await get_support_options(session, interaction.guild_id)
                config = await session.scalar(
                    select(GuildConfig).where(GuildConfig.guild_id == interaction.guild_id)
                )
                if (
                    not config
                    or not config.ticket_category_id
                    or not (config.support_role_id or config.admin_role_id)
                ):
                    raise ValueError(
                        "Configure categoria de tickets e cargo de suporte antes de publicar."
                    )
                if options.transcript_required and not config.transcript_channel_id:
                    raise ValueError("Configure o canal de transcripts antes de publicar.")
                if not config.logs_channel_id:
                    raise ValueError("Configure o canal de logs antes de publicar.")
            message = await channel.send(
                view=SupportPanel(options), allowed_mentions=discord.AllowedMentions.none()
            )
            async with SessionLocal() as session, session.begin():
                await write_audit_log(
                    session,
                    guild_id=interaction.guild_id,
                    actor_discord_id=interaction.user.id,
                    action="ticket.panel",
                    details={"channel_id": channel.id},
                )
            await interaction.followup.send(f"Painel publicado: {message.jump_url}", ephemeral=True)
        except Exception as exc:
            await report_error(interaction, exc)

    @tickets.command(name="listar", description="Gerenciar tickets de suporte por fora do canal")
    async def list_tickets(self, interaction: discord.Interaction):
        await send_support_list(interaction)

    @tickets.command(name="acao", description="Gerenciar um ticket de suporte selecionando o canal")
    @app_commands.choices(
        acao=[
            app_commands.Choice(name=label, value=value)
            for label, value in (
                ("Assumir", "claim"),
                ("Liberar", "release"),
                ("Fechar", "close"),
                ("Reabrir", "reopen"),
                ("Excluir", "delete"),
                ("Exportar transcript", "transcript"),
            )
        ]
    )
    async def action(
        self,
        interaction: discord.Interaction,
        acao: app_commands.Choice[str],
        canal: discord.TextChannel | None = None,
    ):
        if not await can_support(interaction):
            await interaction.response.send_message("Sem permissão.", ephemeral=True)
            return
        async with SessionLocal() as session:
            ticket = await session.scalar(
                select(SupportTicket).where(
                    SupportTicket.guild_id == interaction.guild_id,
                    SupportTicket.channel_id == (canal.id if canal else interaction.channel_id),
                    SupportTicket.state != "deleted",
                )
            )
        if not ticket:
            await interaction.response.send_message(
                "Selecione um canal de ticket de suporte.", ephemeral=True
            )
        elif acao.value in {"close", "delete"}:
            await interaction.response.send_modal(TicketReasonModal(ticket.id, acao.value))
        else:
            await run_action(interaction, ticket.id, acao.value)

    @tickets.command(name="participante", description="Adicionar ou remover alguém de um ticket")
    @app_commands.choices(
        acao=[
            app_commands.Choice(name="Adicionar", value="add"),
            app_commands.Choice(name="Remover", value="remove"),
        ]
    )
    async def participant(
        self,
        interaction: discord.Interaction,
        acao: app_commands.Choice[str],
        membro: discord.Member,
        canal: discord.TextChannel | None = None,
    ):
        if not await can_support(interaction):
            await interaction.response.send_message("Sem permissão.", ephemeral=True)
            return
        async with SessionLocal() as session:
            ticket = await session.scalar(
                select(SupportTicket).where(
                    SupportTicket.guild_id == interaction.guild_id,
                    SupportTicket.channel_id == (canal.id if canal else interaction.channel_id),
                    SupportTicket.state != "deleted",
                )
            )
        if ticket is None:
            await interaction.response.send_message(
                "Selecione um canal de ticket de suporte.", ephemeral=True
            )
            return
        await run_action(interaction, ticket.id, acao.value, participant=membro)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if not is_store_guild(channel.guild.id):
            return
        async with SessionLocal() as session, session.begin():
            ticket = await session.scalar(
                select(SupportTicket)
                .where(
                    SupportTicket.guild_id == channel.guild.id,
                    SupportTicket.channel_id == channel.id,
                    SupportTicket.state != "deleted",
                )
                .with_for_update()
            )
            if ticket:
                ticket.state = "deleted"
                await ticket_event(
                    session,
                    ticket,
                    None,
                    "external_delete",
                    reason="Canal removido diretamente no Discord; histórico não pode ser recuperado.",
                    transcript_saved=bool(ticket.transcript_url),
                )


async def setup(bot):
    bot.add_view(SupportPanel())
    async with SessionLocal() as session:
        ids = list(
            (
                await session.scalars(
                    select(SupportTicket.id).where(SupportTicket.state != "deleted")
                )
            ).all()
        )
    for ticket_id in ids:
        bot.add_view(SupportControls(ticket_id))
    await bot.add_cog(SupportTicketsCog(bot))
