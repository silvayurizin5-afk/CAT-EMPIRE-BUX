import io
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


def _set_status(embed: discord.Embed, value: str) -> discord.Embed:
    updated = embed.copy()
    for index, field in enumerate(updated.fields):
        if field.name == "Status":
            updated.set_field_at(index, name="Status", value=value, inline=field.inline)
            break
    return updated


class ManualPixPaymentView(discord.ui.View):
    def __init__(self, order_id: UUID) -> None:
        super().__init__(timeout=None)
        self.order_id = order_id
        suffix = str(order_id)
        self.confirm.custom_id = f"nextbuy:pix:{suffix}:confirm"
        self.cancel.custom_id = f"nextbuy:pix:{suffix}:cancel"

    @discord.ui.button(
        label="Confirmar pagamento",
        style=discord.ButtonStyle.success,
        custom_id="nextbuy:pix:confirm",
    )
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None or not await can_support(interaction):
            await interaction.response.send_message("Sem permissão para confirmar pagamentos.", ephemeral=True)
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

        if interaction.message is not None:
            embed = interaction.message.embeds[0] if interaction.message.embeds else None
            await interaction.message.edit(
                embed=_set_status(embed, "Pago • confirmado manualmente") if embed else None,
                view=TicketStaffView(self.order_id),
            )
        if isinstance(interaction.channel, discord.TextChannel):
            try:
                await interaction.channel.edit(name=f"pedido-{str(self.order_id)[:8]}")
            except discord.HTTPException:
                pass
            if user is not None:
                await interaction.channel.send(
                    f"<@{user.discord_user_id}> pagamento confirmado pela equipe. O pedido está liberado para atendimento.",
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
        await interaction.followup.send("Pagamento confirmado e registrado.", ephemeral=True)

    @discord.ui.button(
        label="Cancelar pedido",
        style=discord.ButtonStyle.danger,
        custom_id="nextbuy:pix:cancel",
    )
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None or not await can_support(interaction):
            await interaction.response.send_message("Sem permissão para cancelar pagamentos.", ephemeral=True)
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

        if interaction.message is not None:
            embed = interaction.message.embeds[0] if interaction.message.embeds else None
            await interaction.message.edit(
                embed=_set_status(embed, "Cancelado") if embed else None,
                view=None,
            )
        if isinstance(interaction.channel, discord.TextChannel) and user is not None:
            await interaction.channel.send(
                f"<@{user.discord_user_id}> este pedido foi cancelado pela equipe.",
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
        name=f"pagamento-{str(order.id)[:8]}",
        category=category,
        overwrites=overwrites,
        topic=f"NEXTBUY PIX order={order.id} customer={user.discord_user_id}",
        reason="NEXTBUY: aguardando pagamento PIX",
    )

    try:
        lines = "\n".join(f"- **{item.name_snapshot}** × `{item.quantity}`" for item in items)
        embed = discord.Embed(
            title=f"Pagamento PIX • Pedido {str(order.id)[:8]}",
            description=lines or "Pedido sem itens.",
            color=discord.Color.from_rgb(43, 45, 49),
        )
        embed.add_field(name="Cliente", value=member.mention, inline=True)
        embed.add_field(name="Valor", value=f"`{format_brl(order.total_credits)}`", inline=True)
        embed.add_field(name="Status", value="Aguardando confirmação manual", inline=False)
        embed.add_field(
            name="Como funciona",
            value=(
                "Pague pelo QR Code ou pelo PIX Copia e Cola abaixo. "
                "Depois aguarde a equipe verificar o recebimento e clicar em **Confirmar pagamento**."
            ),
            inline=False,
        )
        filename = f"pix-{str(order.id)[:8]}.png"
        embed.set_image(url=f"attachment://{filename}")
        file = discord.File(io.BytesIO(charge.qr_png), filename=filename)
        await channel.send(
            content=member.mention,
            embed=embed,
            file=file,
            view=ManualPixPaymentView(order.id),
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        await channel.send(
            f"**PIX Copia e Cola:**\n```{charge.payload}```\nTXID: `{charge.txid}`",
            allowed_mentions=discord.AllowedMentions.none(),
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
