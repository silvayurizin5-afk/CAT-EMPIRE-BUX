import io
import re
import unicodedata
from uuid import UUID

import discord
from sqlalchemy import select

from app.bot.checks import can_support
from app.bot.components_v2 import CardLayout, add_action_row
from app.bot.workflows.leaderboard import refresh_leaderboard
from app.bot.workflows.ranks import sync_customer_roles
from app.bot.workflows.tickets import TicketDeleteConfirmView, TicketStaffView
from app.db.models import GuildConfig, Order, OrderItem, User
from app.db.session import SessionLocal
from app.services.audit import write_audit_log
from app.services.calculator import format_brl
from app.services.manual_payments import cancel_manual_pix_order, confirm_manual_pix_payment
from app.services.pix import create_pix_charge


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


def _order_name(items: list[OrderItem]) -> str:
    if not items:
        return "Pedido"
    if len(items) == 1:
        return items[0].name_snapshot
    return f"{items[0].name_snapshot} + {len(items) - 1} item(ns)"


def _channel_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return (cleaned or "pedido")[:70]


class TicketStaffContainerLayout(discord.ui.LayoutView):
    """Coloca os controles legados de atendimento dentro de um Container V2."""

    def __init__(
        self,
        order_id: UUID,
        *,
        title: str = "Pedido confirmado",
        lines: list[str] | None = None,
    ) -> None:
        super().__init__(timeout=None)
        card = CardLayout(
            title=title,
            lines=lines or ["**Status:** `Confirmado`"],
            footer="NEXTBUY • Atendimento",
            timeout=None,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)

        self._legacy = TicketStaffView(order_id)
        buttons = list(self._legacy.children)
        for item in buttons:
            self._legacy.remove_item(item)
        if buttons:
            add_action_row(self.container, *buttons)


class ManualPixPaymentLayout(discord.ui.LayoutView):
    def __init__(
        self,
        order_id: UUID,
        *,
        product_name: str = "Pedido",
        member_mention: str | None = None,
        total_brl=None,
        item_lines: list[str] | None = None,
        pix_payload: str | None = None,
        qr_attachment_url: str | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.order_id = order_id
        lines: list[str] = []
        if member_mention:
            lines.append(f"**Cliente:** {member_mention}")
        if item_lines:
            lines.extend(item_lines)
        if total_brl is not None:
            lines.append(f"**Valor:** `{format_brl(total_brl)}`")
        lines.append("**Status:** `Aguardando confirmação`")
        lines.append(
            "Pague pelo QR Code ou pelo PIX Copia e Cola. Depois aguarde a equipe confirmar o recebimento."
        )
        if pix_payload:
            lines.append(f"**PIX Copia e Cola:**\n```{pix_payload}```")

        card = CardLayout(
            title=f"Pagamento PIX • {product_name}",
            lines=lines,
            footer="NEXTBUY • Pagamento",
            image_url=qr_attachment_url,
            timeout=None,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)

        confirm = discord.ui.Button(
            label="Confirmar pagamento",
            style=discord.ButtonStyle.success,
            custom_id=f"nextbuy:pix:{order_id}:confirm",
        )
        cancel = discord.ui.Button(
            label="Cancelar pedido",
            style=discord.ButtonStyle.danger,
            custom_id=f"nextbuy:pix:{order_id}:cancel",
        )
        delete = discord.ui.Button(
            label="Excluir ticket",
            style=discord.ButtonStyle.danger,
            custom_id=f"nextbuy:pix:{order_id}:delete",
        )
        confirm.callback = self._confirm
        cancel.callback = self._cancel
        delete.callback = self._delete
        add_action_row(self.container, confirm, cancel, delete)

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
        items = loaded[2] if loaded else []
        product_name = _order_name(items)
        total = loaded[0].total_credits if loaded else order.total_credits
        lines = [f"**Valor:** `{format_brl(total)}`", "**Status:** `Confirmado`"]
        if interaction.message is not None:
            await interaction.message.edit(
                content=None,
                embeds=[],
                view=TicketStaffContainerLayout(
                    self.order_id,
                    title=product_name,
                    lines=lines,
                ),
            )
        if isinstance(interaction.channel, discord.TextChannel):
            try:
                await interaction.channel.edit(name=f"pedido-{_channel_slug(product_name)}")
            except discord.HTTPException:
                pass
            if user is not None:
                await interaction.channel.send(
                    f"<@{user.discord_user_id}> pagamento confirmado. O pedido está liberado para atendimento.",
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
        await interaction.followup.send("Pagamento confirmado e registrado.", ephemeral=True)

    async def _delete(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not await can_support(interaction):
            await interaction.response.send_message(
                "Sem permissão para excluir tickets.",
                ephemeral=True,
            )
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Canal inválido.", ephemeral=True)
            return
        await interaction.response.send_message(
            (
                "**Excluir permanentemente este ticket de pagamento?**\n"
                "Como o pagamento ainda está pendente, o pedido será cancelado e o estoque/cupom "
                "serão devolvidos. O transcript será salvo primeiro quando configurado."
            ),
            view=TicketDeleteConfirmView(
                order_id=self.order_id,
                owner_id=interaction.user.id,
                channel_id=interaction.channel.id,
            ),
            ephemeral=True,
        )

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
        items = loaded[2] if loaded else []
        product_name = _order_name(items)
        if interaction.message is not None:
            await interaction.message.edit(
                content=None,
                embeds=[],
                view=CardLayout(
                    title=product_name,
                    lines=[f"**Valor:** `{format_brl(order.total_credits)}`", "**Status:** `Cancelado`"],
                    footer="NEXTBUY • Pagamento",
                    timeout=None,
                ),
            )
        if isinstance(interaction.channel, discord.TextChannel) and user is not None:
            await interaction.channel.send(
                f"<@{user.discord_user_id}> este pedido foi cancelado pela equipe.",
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        await interaction.followup.send("Pedido cancelado e estoque liberado.", ephemeral=True)


# Alias mantido para imports antigos e persistência de versões anteriores.
ManualPixPaymentView = ManualPixPaymentLayout


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
    product_name = _order_name(items)
    channel = await guild.create_text_channel(
        name=f"pagamento-{_channel_slug(product_name)}",
        category=category,
        overwrites=overwrites,
        topic=f"NEXTBUY PIX order={order.id} customer={user.discord_user_id}",
        reason="NEXTBUY: aguardando pagamento PIX",
    )

    try:
        filename = "pix-qrcode.png"
        file = discord.File(io.BytesIO(charge.qr_png), filename=filename)
        item_lines = [f"- **{item.name_snapshot}** × `{item.quantity}`" for item in items]
        view = ManualPixPaymentLayout(
            order.id,
            product_name=product_name,
            member_mention=member.mention,
            total_brl=order.total_credits,
            item_lines=item_lines,
            pix_payload=charge.payload,
            qr_attachment_url=f"attachment://{filename}",
        )
        await channel.send(
            view=view,
            file=file,
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
        bot.add_view(ManualPixPaymentLayout(order_id))
