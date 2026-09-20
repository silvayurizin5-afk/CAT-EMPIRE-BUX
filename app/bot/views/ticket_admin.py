from uuid import UUID

import discord
from sqlalchemy import select

from app.bot.checks import can_admin
from app.bot.components_v2 import CardLayout, add_action_row, add_select_row
from app.bot.views.admin_feedback import ExtendedAdminPanelView
from app.bot.workflows.tickets import TicketDeleteConfirmView
from app.db.models import Order, OrderItem, User
from app.db.session import SessionLocal
from app.services.audit import write_audit_log
from app.services.calculator import format_brl
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


def _status_label(status: str) -> str:
    return {
        "pending": "Aguardando pagamento",
        "paid": "Pago",
        "processing": "Em atendimento",
        "delivered": "Entregue",
        "cancelled": "Cancelado",
    }.get(status, status.replace("_", " ").title())


async def _ticket_details(guild_id: int, order_id: UUID):
    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(Order, User)
                .join(User, User.id == Order.user_id)
                .where(Order.id == order_id, Order.guild_id == guild_id)
            )
        ).first()
        if row is None:
            return None
        order, user = row
        items = list(
            (
                await session.scalars(
                    select(OrderItem)
                    .where(OrderItem.order_id == order.id)
                    .order_by(OrderItem.id)
                )
            ).all()
        )
    return order, user, items


def _ticket_product_summary(items: list[OrderItem]) -> str:
    if not items:
        return "Pedido sem itens"
    if len(items) == 1:
        return f"{items[0].name_snapshot} × {items[0].quantity}"
    return f"{items[0].name_snapshot} + {len(items) - 1} item(ns)"


class TicketAdminSelect(discord.ui.Select):
    def __init__(self, rows, owner_id: int) -> None:
        self.owner_id = owner_id
        options = []
        for order, user, item_name in rows[:25]:
            channel_id = int(order.ticket_channel_id or 0)
            options.append(
                discord.SelectOption(
                    label=(item_name or "Pedido")[:100],
                    value=str(order.id),
                    description=(
                        f"{_status_label(order.status)} • "
                        f"{format_brl(order.total_credits)} • cliente {user.discord_user_id}"
                    )[:100],
                )
            )
        super().__init__(
            placeholder="Selecione o ticket que deseja gerenciar",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.user.id != self.owner_id:
            return
        order_id = UUID(self.values[0])
        loaded = await _ticket_details(interaction.guild.id, order_id)
        if loaded is None:
            await interaction.response.edit_message(
                content="Pedido não encontrado.",
                view=None,
            )
            return
        order, user, items = loaded
        channel = (
            interaction.guild.get_channel(order.ticket_channel_id)
            if order.ticket_channel_id
            else None
        )
        await interaction.response.edit_message(
            content=None,
            view=TicketAdminDetailLayout(
                order_id=order.id,
                channel_id=order.ticket_channel_id,
                owner_id=self.owner_id,
                customer_id=user.discord_user_id,
                product_summary=_ticket_product_summary(items),
                total=format_brl(order.total_credits),
                status=_status_label(order.status),
                channel_mention=channel.mention if isinstance(channel, discord.TextChannel) else "Canal não encontrado",
            ),
        )


class TicketAdminListLayout(discord.ui.LayoutView):
    def __init__(self, rows, *, owner_id: int) -> None:
        super().__init__(timeout=300)
        card = CardLayout(
            title="Gerenciar tickets",
            description=(
                "Selecione um ticket ativo abaixo. A exclusão salva o transcript quando "
                "o canal de transcripts estiver configurado."
            ),
            lines=[f"**Tickets encontrados:** `{len(rows)}`"],
            footer="NEXTBUY • Administração de tickets",
            timeout=300,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)
        add_select_row(self.container, TicketAdminSelect(rows, owner_id))


class TicketAdminDetailLayout(discord.ui.LayoutView):
    def __init__(
        self,
        *,
        order_id: UUID,
        channel_id: int | None,
        owner_id: int,
        customer_id: int,
        product_summary: str,
        total: str,
        status: str,
        channel_mention: str,
    ) -> None:
        super().__init__(timeout=180)
        self.order_id = order_id
        self.channel_id = channel_id
        self.owner_id = owner_id
        card = CardLayout(
            title="Ticket selecionado",
            lines=[
                "**Usuário:**",
                f"- <@{customer_id}> `({customer_id})`",
                "**Detalhes:**",
                f"- **Produto:** {product_summary}",
                f"- **Valor:** {total}",
                f"- **Status:** {status}",
                f"- **Canal:** {channel_mention}",
            ],
            footer="NEXTBUY • Gerenciamento de ticket",
            timeout=180,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)

        delete = discord.ui.Button(
            label="Excluir ticket",
            style=discord.ButtonStyle.danger,
        )
        delete.callback = self._delete
        add_action_row(self.container, delete)

    async def _delete(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.user.id != self.owner_id:
            return
        if not await can_admin(interaction):
            await interaction.response.send_message("Sem permissão.", ephemeral=True)
            return
        await interaction.response.send_message(
            (
                "**Confirmar exclusão deste ticket?**\n"
                "O canal será removido permanentemente. O transcript será salvo primeiro "
                "quando essa opção estiver configurada."
            ),
            view=TicketDeleteConfirmView(
                order_id=self.order_id,
                owner_id=interaction.user.id,
                channel_id=self.channel_id,
            ),
            ephemeral=True,
        )


async def send_ticket_management(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    async with SessionLocal() as session:
        orders = list(
            (
                await session.scalars(
                    select(Order)
                    .where(
                        Order.guild_id == interaction.guild.id,
                        Order.ticket_channel_id.is_not(None),
                    )
                    .order_by(Order.created_at.desc())
                    .limit(25)
                )
            ).all()
        )
        rows = []
        for order in orders:
            user = await session.get(User, order.user_id)
            first_item = await session.scalar(
                select(OrderItem)
                .where(OrderItem.order_id == order.id)
                .order_by(OrderItem.id)
                .limit(1)
            )
            if user is None:
                continue
            rows.append(
                (
                    order,
                    user,
                    first_item.name_snapshot if first_item is not None else "Pedido",
                )
            )

    if not rows:
        await interaction.edit_original_response(
            content="Não existem tickets ativos para gerenciar.",
            view=None,
        )
        return
    await interaction.edit_original_response(
        content=None,
        view=TicketAdminListLayout(rows, owner_id=interaction.user.id),
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
