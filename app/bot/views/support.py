import logging
from uuid import UUID

import discord
from sqlalchemy import select

from app.bot.checks import can_support
from app.bot.components_v2 import CardLayout, add_action_row, add_select_row
from app.bot.workflows.support import open_support_ticket, operate_ticket
from app.db.session import SessionLocal
from app.db.ticket_models import SupportTicket
from app.services.support_tickets import SupportOptions

logger = logging.getLogger(__name__)


async def report_error(interaction, exc):
    if isinstance(exc, ValueError):
        message = str(exc)
    elif isinstance(exc, discord.Forbidden):
        message = (
            "Faltam permissões para gerenciar canais/permissões, ler histórico ou enviar anexos."
        )
    else:
        logger.error("Falha ao operar ticket", exc_info=(type(exc), exc, exc.__traceback__))
        message = "Não foi possível concluir a ação. O canal foi preservado; verifique as permissões e tente novamente."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


async def run_action(interaction, ticket_id, action, **kwargs):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        message = await operate_ticket(interaction, ticket_id, action, **kwargs)
    except Exception as exc:
        await report_error(interaction, exc)
        return
    try:
        await interaction.followup.send(
            message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )
    except discord.HTTPException:
        logger.warning("Ação %s concluída, mas a confirmação da interação expirou", action)


class OpenSupportModal(discord.ui.Modal, title="Abrir atendimento"):
    subject = discord.ui.TextInput(label="Assunto", min_length=2, max_length=100)
    description = discord.ui.TextInput(
        label="Como podemos ajudar?",
        min_length=2,
        max_length=1800,
        style=discord.TextStyle.paragraph,
    )

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            channel = await open_support_ticket(
                interaction, str(self.subject), str(self.description)
            )
            await interaction.followup.send(f"Seu atendimento: {channel.mention}", ephemeral=True)
        except Exception as exc:
            await report_error(interaction, exc)


class SupportPanel(CardLayout):
    def __init__(self, options: SupportOptions | None = None):
        options = options or SupportOptions()
        super().__init__(
            title=options.panel_title,
            description=options.panel_description,
            image_url=options.banner_url or None,
            accent_colour=int(options.color, 16),
            timeout=None,
        )
        button = discord.ui.Button(
            label=options.button_label,
            emoji=options.button_emoji or None,
            custom_id="nextbuy:support:open",
            style=discord.ButtonStyle.primary,
        )
        button.callback = self.open_ticket
        add_action_row(self.container, button)

    async def open_ticket(self, interaction):
        from app.core.guild_guard import is_store_guild

        if not is_store_guild(interaction.guild_id):
            await interaction.response.send_message("Servidor não autorizado.", ephemeral=True)
            return
        await interaction.response.send_modal(OpenSupportModal())


class TicketReasonModal(discord.ui.Modal):
    reason = discord.ui.TextInput(
        label="Motivo", min_length=2, max_length=1000, style=discord.TextStyle.paragraph
    )

    def __init__(self, ticket_id, action):
        super().__init__(title="Fechar ticket" if action == "close" else "Excluir permanentemente")
        self.ticket_id, self.action = ticket_id, action

    async def on_submit(self, interaction):
        if self.action == "delete":
            await interaction.response.send_message(
                "**Excluir permanentemente este canal?** O transcript será processado antes da exclusão.",
                view=SupportDeleteConfirm(self.ticket_id, interaction.user.id, str(self.reason)),
                ephemeral=True,
            )
        else:
            await run_action(interaction, self.ticket_id, self.action, reason=str(self.reason))


class SupportDeleteConfirm(discord.ui.View):
    def __init__(self, ticket_id, owner_id, reason):
        super().__init__(timeout=90)
        self.ticket_id, self.owner_id, self.reason = ticket_id, owner_id, reason

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Esta confirmação pertence a outra pessoa.", ephemeral=True
        )
        return False

    @discord.ui.button(label="Confirmar exclusão", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, _):
        await run_action(interaction, self.ticket_id, "delete", reason=self.reason)

    @discord.ui.button(label="Cancelar")
    async def cancel(self, interaction, _):
        await interaction.response.edit_message(content="Exclusão cancelada.", view=None)


class SupportAction(discord.ui.Button):
    def __init__(self, ticket_id, action, label, style=discord.ButtonStyle.secondary):
        super().__init__(
            label=label, style=style, custom_id=f"nextbuy:support:{ticket_id}:{action}"
        )
        self.ticket_id, self.action = ticket_id, action

    async def callback(self, interaction):
        if self.action in {"close", "delete"}:
            # Authorization is always rechecked at submission, including old panels.
            await interaction.response.send_modal(TicketReasonModal(self.ticket_id, self.action))
        else:
            await run_action(interaction, self.ticket_id, self.action)


class SupportControls(CardLayout):
    def __init__(
        self,
        ticket_id,
        *,
        subject="Atendimento",
        description="",
        welcome="Controles do atendimento",
        color=0x5865F2,
    ):
        super().__init__(
            title=subject,
            description=welcome,
            lines=[description] if description else [],
            accent_colour=color,
            footer="NEXTBUY • Atendimento privado",
            timeout=None,
        )
        add_action_row(
            self.container,
            SupportAction(ticket_id, "claim", "Assumir", discord.ButtonStyle.primary),
            SupportAction(ticket_id, "release", "Liberar"),
            SupportAction(ticket_id, "close", "Fechar"),
            SupportAction(ticket_id, "reopen", "Reabrir", discord.ButtonStyle.success),
        )
        add_action_row(
            self.container,
            SupportAction(ticket_id, "transcript", "Transcript"),
            SupportAction(ticket_id, "delete", "Excluir", discord.ButtonStyle.danger),
        )


class SupportListSelect(discord.ui.Select):
    def __init__(self, rows):
        super().__init__(
            placeholder="Selecione um atendimento",
            options=[
                discord.SelectOption(
                    label=t.subject[:100],
                    value=str(t.id),
                    description=f"{'Aberto' if t.state == 'open' else 'Fechado'} • Cliente {t.customer_id}",
                )
                for t in rows
            ],
        )

    async def callback(self, interaction):
        if not await can_support(interaction):
            await interaction.response.send_message("Sem permissão.", ephemeral=True)
            return
        async with SessionLocal() as session:
            ticket = await session.scalar(
                select(SupportTicket).where(
                    SupportTicket.id == UUID(self.values[0]),
                    SupportTicket.guild_id == interaction.guild_id,
                )
            )
        if not ticket or ticket.state == "deleted":
            await interaction.response.send_message("Ticket já removido.", ephemeral=True)
            return
        await interaction.response.send_message(
            view=SupportControls(
                ticket.id,
                subject=ticket.subject,
                description=f"**Cliente:** <@{ticket.customer_id}>\n**Canal:** <#{ticket.channel_id}>\n"
                f"**Status:** {'Aberto' if ticket.state == 'open' else 'Fechado'}\n"
                f"**Responsável:** {f'<@{ticket.assignee_id}>' if ticket.assignee_id else 'Não atribuído'}",
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class SupportList(CardLayout):
    def __init__(self, rows, owner_id, page, has_next):
        super().__init__(
            title="Gerenciar atendimentos", description=f"Página {page + 1}", timeout=300
        )
        self.owner_id = owner_id
        if rows:
            add_select_row(self.container, SupportListSelect(rows))
        else:
            self.container.add_item(discord.ui.TextDisplay("Nenhum atendimento nesta página."))
        buttons = []
        for label, target, disabled in (
            ("Anterior", page - 1, page == 0),
            ("Próxima", page + 1, not has_next),
        ):
            button = discord.ui.Button(label=label, disabled=disabled)

            async def callback(interaction, target=target):
                await send_support_list(interaction, page=target)

            button.callback = callback
            buttons.append(button)
        add_action_row(self.container, *buttons)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Abra seu próprio painel com /tickets listar.", ephemeral=True
        )
        return False


async def send_support_list(interaction, *, page=0):
    if not await can_support(interaction):
        await interaction.response.send_message("Sem permissão.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    async with SessionLocal() as session:
        rows = list(
            (
                await session.scalars(
                    select(SupportTicket)
                    .where(
                        SupportTicket.guild_id == interaction.guild_id,
                        SupportTicket.state != "deleted",
                    )
                    .order_by(SupportTicket.created_at.desc(), SupportTicket.id)
                    .offset(max(0, page) * 25)
                    .limit(26)
                )
            ).all()
        )
    await interaction.followup.send(
        view=SupportList(rows[:25], interaction.user.id, page, len(rows) > 25), ephemeral=True
    )
