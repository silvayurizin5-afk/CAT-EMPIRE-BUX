import io
import re
import unicodedata
from uuid import UUID

import discord
from sqlalchemy import select

from app.bot.checks import can_support
from app.bot.workflows.leaderboard import refresh_leaderboard
from app.bot.workflows.ranks import sync_customer_roles
from app.bot.workflows.tickets import TicketStaffView
from app.db.models import GuildConfig, Order, OrderItem, User
from app.db.session import SessionLocal
from app.services.audit import write_audit_log
from app.services.calculator import format_brl
from app.services.manual_payments import cancel_manual_pix_order, confirm_manual_pix_payment
from app.services.pix import create_pix_charge

DEFAULT_ACCENT = 0x2B2D31


async def _load_order(order_id: UUID):
    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(Order, User)
                .join(User, User.id == Order.user_id)
                .where(Order.id == order_id)
            )
        ).first()
        if row is None:
            return None
        order, user = row
        items = list(
            (
                await session.scalars(
                    select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id)
                )
            ).all()
        )
        config = await session.scalar(
            select(GuildConfig).where(GuildConfig.guild_id == order.guild_id)
        )
        return order, user, items, config


def _product_title(items: list[OrderItem]) -> str:
    if not items:
        return "Pedido"
    first = items[0].name_snapshot
    if len(items) == 1:
        return first
    return f"{first} +{len(items) - 1}"


def _channel_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return (slug or "pedido")[:70]


def _items_text(items: list[OrderItem]) -> str:
    if not items:
        return "- Pedido sem itens."
    return "\n".join(f"- **{item.name_snapshot}** × `{item.quantity}`" for item in items)


def _payment_body(
    *,
    member_mention: str,
    order: Order,
    items: list[OrderItem],
    status: str,
    include_instructions: bool = True,
) -> str:
    text = (
        f"## Pagamento PIX • {_product_title(items)}\n"
        f"{_items_text(items)}\n\n"
        f"**Cliente:** {member_mention}\n"
        f"**Valor:** **`{format_brl(order.total_credits)}`**\n"
        f"**Status:** **{status}**"
    )
    if include_instructions:
        text += (
            "\n\nPague pelo QR Code ou pelo **PIX Copia e Cola** abaixo. "
            "Depois, aguarde a equipe confirmar o recebimento."
        )
    return text


class PaymentResultView(discord.ui.LayoutView):
    def __init__(self, body: str) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(body[:4000]),
                accent_color=DEFAULT_ACCENT,
            )
        )


class ManualPixPaymentView(discord.ui.LayoutView):
    def __init__(
        self,
        order_id: UUID,
        *,
        body: str | None = None,
        pix_payload: str | None = None,
        attachment_name: str | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.order_id = order_id
        suffix = str(order_id)

        confirm = discord.ui.Button(
            label="Confirmar pagamento",
            style=discord.ButtonStyle.success,
            custom_id=f"nextbuy:pix:{suffix}:confirm",
        )
        cancel = discord.ui.Button(
            label="Cancelar pedido",
            style=discord.ButtonStyle.danger,
            custom_id=f"nextbuy:pix:{suffix}:cancel",
        )
        confirm.callback = self._confirm
        cancel.callback = self._cancel

        children: list[discord.ui.Item] = [
            discord.ui.TextDisplay(
                body or "## Pagamento PIX\nAguardando confirmação da equipe."
            )
        ]
        if attachment_name:
            children.append(discord.ui.File(f"attachment://{attachment_name}"))
        if pix_payload:
            children.append(
                discord.ui.TextDisplay(
                    f"**PIX Copia e Cola:**\n```text\n{pix_payload}\n```"[:4000]
                )
            )
        children.append(discord.ui.ActionRow(confirm, cancel))
        self.add_item(discord.ui.Container(*children, accent_color=DEFAULT_ACCENT))

    async def _confirm(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not await can_support(interaction):
            await interaction.response.send_message(
                "Sem permissão para confirmar pagamentos.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with SessionLocal() as session, session.begin():
                order = await confirm_manual_pix_payment(
                    session,
                    order_id=self.order_id,
                    actor_discord_id=interaction.user.id,
                )
                user = await session.get(User, order.user_id)
        except (ValueError, RuntimeError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        if user is not None:
            member = interaction.guild.get_member(user.discord_user_id)
            if member is None:
                try:
                    member = await interaction.guild.fetch_member(user.discord_user_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    member = None
            if member is not None:
                await sync_customer_roles(member)
        await refresh_leaderboard(interaction.guild)

        loaded = await _load_order(self.order_id)
        if loaded is not None:
            db_order, db_user, items, _ = loaded
            member_mention = f"<@{db_user.discord_user_id}>"
            body = _payment_body(
                member_mention=member_mention,
                order=db_order,
                items=items,
                status="Confirmado",
                include_instructions=False,
            )
            if interaction.message is not None:
                await interaction.message.edit(
                    content=None,
                    embed=None,
                    view=TicketStaffView(self.order_id, body=body),
                )
            if isinstance(interaction.channel, discord.TextChannel):
                try:
                    await interaction.channel.edit(
                        name=f"pedido-{_channel_slug(_product_title(items))}"[:100]
                    )
                except discord.HTTPException:
                    pass
                await interaction.channel.send(
                    view=PaymentResultView(
                        f"<@{db_user.discord_user_id}>\n"
                        "## Pagamento confirmado\nO pedido está liberado para atendimento."
                    ),
                    allowed_mentions=discord.AllowedMentions(
                        users=True, roles=False, everyone=False
                    ),
                )
        await interaction.followup.send("Pagamento confirmado e registrado.", ephemeral=True)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not await can_support(interaction):
            await interaction.response.send_message(
                "Sem permissão para cancelar pagamentos.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with SessionLocal() as session, session.begin():
                order = await cancel_manual_pix_order(
                    session,
                    order_id=self.order_id,
                    actor_discord_id=interaction.user.id,
                    reason="cancelado pela equipe",
                )
                user = await session.get(User, order.user_id)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        loaded = await _load_order(self.order_id)
        if loaded is not None:
            db_order, db_user, items, _ = loaded
            body = _payment_body(
                member_mention=f"<@{db_user.discord_user_id}>",
                order=db_order,
                items=items,
                status="Cancelado",
                include_instructions=False,
            )
            if interaction.message is not None:
                await interaction.message.edit(
                    content=None,
                    embed=None,
                    view=PaymentResultView(body),
                )
        if isinstance(interaction.channel, discord.TextChannel) and user is not None:
            await interaction.channel.send(
                view=PaymentResultView(
                    f"<@{user.discord_user_id}>\n## Pedido cancelado\n"
                    "A equipe cancelou este pedido."
                ),
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        await interaction.followup.send("Pedido cancelado e estoque liberado.", ephemeral=True)


async def open_manual_pix_ticket(
    interaction: discord.Interaction,
    *,
    order_id: UUID,
) -> discord.TextChannel | None:
    if interaction.guild is None:
        return None
    loaded = await _load_order(order_id)
    if loaded is None:
        return None
    order, user, items, config = loaded
    if order.status != "pending":
        return None

    guild = interaction.guild
    if order.ticket_channel_id:
        existing = guild.get_channel(order.ticket_channel_id)
        if isinstance(existing, discord.TextChannel):
            return existing

    member = guild.get_member(user.discord_user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user.discord_user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
        ),
    }
    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            read_message_history=True,
        )
    if config:
        for role_id in {config.admin_role_id, config.support_role_id, config.delivery_role_id}:
            if role_id and (role := guild.get_role(role_id)):
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )

    category = None
    if config and config.ticket_category_id:
        candidate = guild.get_channel(config.ticket_category_id)
        if isinstance(candidate, discord.CategoryChannel):
            category = candidate

    charge = create_pix_charge(amount_brl=order.total_credits, order_id=order.id)
    channel = await guild.create_text_channel(
        name=f"pagamento-{_channel_slug(_product_title(items))}"[:100],
        category=category,
        overwrites=overwrites,
        topic=f"NEXTBUY PIX order={order.id} customer={user.discord_user_id}",
        reason="NEXTBUY: aguardando pagamento PIX",
    )

    try:
        filename = f"pix-{str(order.id)[:8]}.png"
        file = discord.File(io.BytesIO(charge.qr_png), filename=filename)
        body = _payment_body(
            member_mention=member.mention,
            order=order,
            items=items,
            status="Aguardando confirmação",
        )
        await channel.send(
            file=file,
            view=ManualPixPaymentView(
                order.id,
                body=body,
                pix_payload=charge.payload,
                attachment_name=filename,
            ),
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )

        async with SessionLocal() as session, session.begin():
            db_order = await session.get(Order, order.id)
            if db_order is None or db_order.status != "pending":
                raise ValueError("Pedido deixou de aguardar pagamento")
            db_order.ticket_channel_id = channel.id
            await write_audit_log(
                session,
                guild_id=guild.id,
                actor_discord_id=interaction.user.id,
                action="pix.payment_ticket.open",
                target_type="order",
                target_id=str(order.id),
                details={"channel_id": channel.id, "txid": charge.txid},
            )
    except Exception:
        try:
            await channel.delete(reason="NEXTBUY: falha ao preparar pagamento PIX")
        except discord.HTTPException:
            pass
        raise

    return channel


async def restore_manual_pix_views(bot: discord.Client) -> None:
    async with SessionLocal() as session:
        order_ids = list(
            (
                await session.scalars(
                    select(Order.id).where(
                        Order.status == "pending",
                        Order.ticket_channel_id.is_not(None),
                    )
                )
            ).all()
        )
    for order_id in order_ids:
        bot.add_view(ManualPixPaymentView(order_id))
