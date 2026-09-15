import discord

from app.bot.views.admin_feedback import ExtendedAdminPanelView
from app.db.session import SessionLocal
from app.services.audit import write_audit_log
from app.services.ticket_settings import (
    get_effective_ticket_settings,
    upsert_ticket_settings,
)


class TicketMessagesModal(discord.ui.Modal, title="Mensagens dos tickets"):
    title_template = discord.ui.TextInput(
        label="Título",
        max_length=160,
        placeholder="Pedido {order}",
    )
    instruction_template = discord.ui.TextInput(
        label="Mensagem automática",
        style=discord.TextStyle.paragraph,
        max_length=1800,
        placeholder="{customer}, seu pedido {order} foi confirmado.",
    )

    def __init__(self, *, title_template: str, instruction_template: str) -> None:
        super().__init__()
        self.title_template.default = title_template
        self.instruction_template.default = instruction_template

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            async with SessionLocal() as session, session.begin():
                settings = await upsert_ticket_settings(
                    session,
                    guild_id=interaction.guild.id,
                    title_template=str(self.title_template),
                    instruction_template=str(self.instruction_template),
                )
                await write_audit_log(
                    session,
                    guild_id=interaction.guild.id,
                    actor_discord_id=interaction.user.id,
                    action="ticket.messages.update",
                    target_type="guild",
                    target_id=str(interaction.guild.id),
                    details={
                        "title_template": settings.title_template,
                        "instruction_template": settings.instruction_template,
                    },
                )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            "Mensagens dos tickets atualizadas. Variáveis disponíveis: "
            "`{customer}`, `{order}`, `{total}` e `{items}`.",
            ephemeral=True,
        )


class FinalAdminPanelView(ExtendedAdminPanelView):
    @discord.ui.button(label="Mensagens ticket", style=discord.ButtonStyle.primary)
    async def ticket_messages(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            return
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
