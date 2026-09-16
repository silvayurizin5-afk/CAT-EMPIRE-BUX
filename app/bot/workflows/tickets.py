import io
import re
import unicodedata
from datetime import UTC, datetime
from uuid import UUID

import discord
from sqlalchemy import select

from app.bot.checks import can_deliver, can_support
from app.bot.workflows.transcripts import render_channel_transcript
from app.db.models import GuildConfig, Order, OrderItem, User
from app.db.session import SessionLocal
from app.services.audit import write_audit_log
from app.services.calculator import format_brl
from app.services.feedback import schedule_feedback_reminder, submit_feedback
from app.services.feedback_cards import render_feedback_card
from app.services.orders import mark_order_delivered

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


def _ticket_text(
    *,
    member_mention: str,
    order: Order,
    items: list[OrderItem],
    status: str,
) -> str:
    return (
        f"## {_product_title(items)}\n"
        f"{_items_text(items)}\n\n"
        f"**Cliente:** {member_mention}\n"
        f"**Total:** **`{format_brl(order.total_credits)}`**\n"
        f"**Status:** **{status}**"
    )


async def open_order_ticket(
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
    guild = interaction.guild

    if order.ticket_channel_id:
        existing = guild.get_channel(order.ticket_channel_id)
        if isinstance(existing, discord.TextChannel):
            return existing

    member = guild.get_member(user.discord_user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user.discord_user_id)
        except discord.NotFound:
            return None

    if config and config.customer_role_id:
        customer_role = guild.get_role(config.customer_role_id)
        if customer_role and customer_role not in member.roles:
            try:
                await member.add_roles(customer_role, reason="NEXTBUY: cliente com compra confirmada")
            except discord.HTTPException:
                pass

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

    channel = await guild.create_text_channel(
        name=f"pedido-{_channel_slug(_product_title(items))}"[:100],
        category=category,
        overwrites=overwrites,
        topic=f"NEXTBUY order={order.id} customer={user.discord_user_id}",
        reason="NEXTBUY: pedido pago",
    )
    async with SessionLocal() as session, session.begin():
        db_order = await session.get(Order, order.id)
        if db_order is not None:
            db_order.ticket_channel_id = channel.id
            await write_audit_log(
                session,
                guild_id=guild.id,
                actor_discord_id=interaction.user.id,
                action="ticket.open",
                target_type="order",
                target_id=str(order.id),
                details={
                    "channel_id": channel.id,
                    "customer_discord_id": user.discord_user_id,
                },
            )

    await channel.send(
        view=TicketStaffView(
            order.id,
            body=_ticket_text(
                member_mention=member.mention,
                order=order,
                items=items,
                status="Confirmado",
            ),
        ),
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
    return channel


async def publish_delivery(guild: discord.Guild, *, order_id: UUID) -> None:
    loaded = await _load_order(order_id)
    if loaded is None:
        return
    order, user, items, config = loaded
    if config is None or not config.deliveries_channel_id or order.delivery_message_id:
        return
    channel = guild.get_channel(config.deliveries_channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    member = guild.get_member(user.discord_user_id)
    mention = member.mention if member else f"<@{user.discord_user_id}>"
    body = (
        f"## Entrega realizada\n"
        f"{_items_text(items)}\n\n"
        f"**Cliente:** {mention}\n"
        f"-# {discord.utils.format_dt(order.delivered_at or datetime.now(UTC), style='R')}"
    )
    children: list[discord.ui.Item] = [discord.ui.TextDisplay(body)]
    if items and items[0].image_url_snapshot:
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media=items[0].image_url_snapshot, description=_product_title(items)[:256])
        children.append(gallery)
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*children, accent_color=DEFAULT_ACCENT))
    message = await channel.send(
        view=view,
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
    async with SessionLocal() as session, session.begin():
        db_order = await session.get(Order, order.id)
        if db_order is not None:
            db_order.delivery_message_id = message.id


class FeedbackModal(discord.ui.Modal, title="Avaliar compra"):
    comment = discord.ui.TextInput(
        label="Seu feedback",
        style=discord.TextStyle.paragraph,
        min_length=2,
        max_length=1000,
    )

    def __init__(self, *, order_id: UUID, stars: int) -> None:
        super().__init__()
        self.order_id = order_id
        self.stars = stars

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        async with SessionLocal() as session, session.begin():
            user = await session.scalar(
                select(User).where(User.discord_user_id == interaction.user.id)
            )
            if user is None:
                await interaction.response.send_message("Compra não encontrada.", ephemeral=True)
                return
            feedback = await submit_feedback(
                session,
                order_id=self.order_id,
                user_id=user.id,
                stars=self.stars,
                comment=str(self.comment),
                source="modal",
            )
            config = await session.scalar(
                select(GuildConfig).where(GuildConfig.guild_id == interaction.guild.id)
            )

        if config and config.feedback_channel_id:
            channel = interaction.guild.get_channel(config.feedback_channel_id)
            if isinstance(channel, discord.TextChannel):
                avatar_bytes: bytes | None = None
                try:
                    avatar_bytes = await interaction.user.display_avatar.read()
                except discord.HTTPException:
                    pass
                card_bytes = render_feedback_card(
                    customer_name=interaction.user.display_name,
                    stars=feedback.stars,
                    comment=feedback.comment,
                    order_short_id=str(self.order_id)[:8],
                    avatar_bytes=avatar_bytes,
                )
                filename = f"feedback-{str(self.order_id)[:8]}.png"
                file = discord.File(io.BytesIO(card_bytes), filename=filename)
                layout = discord.ui.LayoutView(timeout=None)
                layout.add_item(
                    discord.ui.Container(
                        discord.ui.TextDisplay(
                            f"## Feedback de compra verificada\n"
                            f"{interaction.user.mention} • {'⭐' * feedback.stars}"
                        ),
                        discord.ui.MediaGallery(
                            discord.MediaGalleryItem(f"attachment://{filename}")
                        ),
                        accent_color=DEFAULT_ACCENT,
                    )
                )
                published = await channel.send(
                    file=file,
                    view=layout,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
                async with SessionLocal() as session, session.begin():
                    db_feedback = await session.get(type(feedback), feedback.id)
                    if db_feedback is not None:
                        db_feedback.published_message_id = published.id
        await interaction.response.send_message("Feedback salvo. Obrigado pela avaliação.", ephemeral=True)


class StarSelect(discord.ui.Select):
    def __init__(self, order_id: UUID) -> None:
        options = [
            discord.SelectOption(label=f"{stars} estrela{'s' if stars > 1 else ''}", value=str(stars))
            for stars in range(1, 6)
        ]
        super().__init__(placeholder="Escolha sua nota", options=options)
        self.order_id = order_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            FeedbackModal(order_id=self.order_id, stars=int(self.values[0]))
        )


class FeedbackPromptView(discord.ui.LayoutView):
    def __init__(self, order_id: UUID, *, member_mention: str | None = None) -> None:
        super().__init__(timeout=300)
        self.order_id = order_id
        select = StarSelect(order_id)
        later = discord.ui.Button(label="Avaliar mais tarde", style=discord.ButtonStyle.secondary)
        later.callback = self._later
        text = "## Avaliar compra\nSua entrega foi concluída. Quer avaliar agora?"
        if member_mention:
            text = f"{member_mention}\n{text}"
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(text),
                discord.ui.ActionRow(select),
                discord.ui.ActionRow(later),
                accent_color=DEFAULT_ACCENT,
            )
        )

    async def _later(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Beleza. Se continuar pendente, eu te lembro no canal de feedbacks.",
            ephemeral=True,
        )


class TicketStaffView(discord.ui.LayoutView):
    def __init__(self, order_id: UUID, *, body: str | None = None) -> None:
        super().__init__(timeout=None)
        self.order_id = order_id
        suffix = str(order_id)

        processing = discord.ui.Button(
            label="Em atendimento",
            style=discord.ButtonStyle.secondary,
            custom_id=f"nextbuy:ticket:{suffix}:processing",
        )
        delivered = discord.ui.Button(
            label="Marcar entregue",
            style=discord.ButtonStyle.success,
            custom_id=f"nextbuy:ticket:{suffix}:delivered",
        )
        close = discord.ui.Button(
            label="Fechar ticket",
            style=discord.ButtonStyle.danger,
            custom_id=f"nextbuy:ticket:{suffix}:close",
        )
        processing.callback = self._processing
        delivered.callback = self._delivered
        close.callback = self._close
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(body or "## Pedido confirmado\nAguardando atendimento da equipe."),
                discord.ui.ActionRow(processing, delivered, close),
                accent_color=DEFAULT_ACCENT,
            )
        )

    async def _processing(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        if not await can_support(interaction) and not await can_deliver(interaction):
            await interaction.response.send_message("Sem permissão.", ephemeral=True)
            return
        async with SessionLocal() as session, session.begin():
            order = await session.scalar(
                select(Order).where(Order.id == self.order_id).with_for_update()
            )
            if order is None:
                await interaction.response.send_message("Pedido não encontrado.", ephemeral=True)
                return
            if order.status == "paid":
                order.status = "processing"
                await write_audit_log(
                    session,
                    guild_id=interaction.guild.id,
                    actor_discord_id=interaction.user.id,
                    action="order.processing",
                    target_type="order",
                    target_id=str(order.id),
                    details={"channel_id": interaction.channel_id},
                )
        await interaction.response.send_message("Pedido marcado como em atendimento.", ephemeral=True)

    async def _delivered(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not await can_deliver(interaction):
            await interaction.response.send_message("Sem permissão de entrega.", ephemeral=True)
            return
        async with SessionLocal() as session, session.begin():
            before = await session.scalar(
                select(Order.status).where(Order.id == self.order_id).with_for_update()
            )
            order = await mark_order_delivered(session, order_id=self.order_id)
            config = await session.scalar(
                select(GuildConfig).where(GuildConfig.guild_id == interaction.guild.id)
            )
            delay = config.feedback_reminder_minutes if config else 5
            await schedule_feedback_reminder(session, order=order, delay_minutes=delay)
            user = await session.get(User, order.user_id)
            if before != "delivered":
                await write_audit_log(
                    session,
                    guild_id=interaction.guild.id,
                    actor_discord_id=interaction.user.id,
                    action="order.delivered",
                    target_type="order",
                    target_id=str(order.id),
                    details={
                        "channel_id": interaction.channel_id,
                        "customer_discord_id": user.discord_user_id if user else None,
                    },
                )
        await publish_delivery(interaction.guild, order_id=self.order_id)
        await interaction.response.send_message("Entrega registrada.", ephemeral=True)
        if isinstance(interaction.channel, discord.TextChannel) and user is not None:
            await interaction.channel.send(
                view=FeedbackPromptView(
                    self.order_id,
                    member_mention=f"<@{user.discord_user_id}>",
                ),
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )

    async def _close(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not await can_support(interaction):
            await interaction.response.send_message("Sem permissão.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        transcript = await render_channel_transcript(interaction.channel)
        loaded = await _load_order(self.order_id)
        config = loaded[3] if loaded else None
        if config and config.transcript_channel_id:
            target = interaction.guild.get_channel(config.transcript_channel_id)
            if isinstance(target, discord.TextChannel):
                file = discord.File(
                    io.BytesIO(transcript),
                    filename=f"transcript-{str(self.order_id)[:8]}.html",
                )
                await target.send(content="Transcript do pedido", file=file)
        if loaded:
            _, user, _, _ = loaded
            member = interaction.guild.get_member(user.discord_user_id)
            if member:
                await interaction.channel.set_permissions(member, send_messages=False, view_channel=True)
        was_closed = interaction.channel.name.startswith("closed-")
        new_name = interaction.channel.name
        if not was_closed:
            new_name = f"closed-{new_name}"[:100]
        await interaction.channel.edit(name=new_name, reason="NEXTBUY: ticket fechado")
        if not was_closed:
            async with SessionLocal() as session, session.begin():
                await write_audit_log(
                    session,
                    guild_id=interaction.guild.id,
                    actor_discord_id=interaction.user.id,
                    action="ticket.close",
                    target_type="order",
                    target_id=str(self.order_id),
                    details={
                        "channel_id": interaction.channel.id,
                        "transcript_saved": bool(config and config.transcript_channel_id),
                    },
                )
        await interaction.followup.send("Ticket fechado e transcript processado.", ephemeral=True)
