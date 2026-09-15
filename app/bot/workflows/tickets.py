import io
from datetime import UTC, datetime
from uuid import UUID

import discord
from sqlalchemy import select

from app.bot.checks import can_deliver, can_support
from app.bot.workflows.transcripts import render_channel_transcript
from app.db.models import GuildConfig, Order, OrderItem, User
from app.db.session import SessionLocal
from app.services.feedback import schedule_feedback_reminder, submit_feedback
from app.services.feedback_cards import render_feedback_card
from app.services.orders import mark_order_delivered


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
        name=f"pedido-{str(order.id)[:8]}",
        category=category,
        overwrites=overwrites,
        topic=f"NEXTBUY order={order.id} customer={user.discord_user_id}",
        reason="NEXTBUY: pedido pago",
    )
    async with SessionLocal() as session, session.begin():
        db_order = await session.get(Order, order.id)
        if db_order is not None:
            db_order.ticket_channel_id = channel.id

    lines = "\n".join(f"• {item.name_snapshot} × {item.quantity}" for item in items)
    embed = discord.Embed(
        title=f"Pedido {str(order.id)[:8]}",
        description=lines or "Pedido sem itens.",
    )
    embed.add_field(name="Cliente", value=member.mention)
    embed.add_field(name="Total", value=f"{order.total_credits:.2f} créditos")
    embed.add_field(name="Status", value="Pago")
    await channel.send(
        content=member.mention,
        embed=embed,
        view=TicketStaffView(order.id),
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
    embed = discord.Embed(
        title="Entrega realizada",
        description="\n".join(f"• {item.name_snapshot} × {item.quantity}" for item in items),
        timestamp=order.delivered_at or datetime.now(UTC),
    )
    embed.add_field(name="Cliente", value=member.mention if member else f"<@{user.discord_user_id}>")
    embed.add_field(name="Pedido", value=f"`{str(order.id)[:8]}`")
    if items and items[0].image_url_snapshot:
        embed.set_image(url=items[0].image_url_snapshot)
    message = await channel.send(embed=embed)
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
                embed = discord.Embed(
                    title="Feedback de compra verificada",
                    description=f"{interaction.user.mention} • {'⭐' * feedback.stars}",
                )
                embed.set_image(url=f"attachment://{filename}")
                published = await channel.send(embed=embed, file=file)
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


class FeedbackPromptView(discord.ui.View):
    def __init__(self, order_id: UUID) -> None:
        super().__init__(timeout=300)
        self.order_id = order_id
        self.add_item(StarSelect(order_id))

    @discord.ui.button(label="Avaliar mais tarde", style=discord.ButtonStyle.secondary)
    async def later(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "Beleza. Se continuar pendente, eu te lembro no canal de feedbacks.",
            ephemeral=True,
        )


class TicketStaffView(discord.ui.View):
    def __init__(self, order_id: UUID) -> None:
        super().__init__(timeout=None)
        self.order_id = order_id
        suffix = str(order_id)
        self.processing.custom_id = f"nextbuy:ticket:{suffix}:processing"
        self.delivered.custom_id = f"nextbuy:ticket:{suffix}:delivered"
        self.close.custom_id = f"nextbuy:ticket:{suffix}:close"

    @discord.ui.button(
        label="Em atendimento",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket:processing",
    )
    async def processing(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await can_support(interaction) and not await can_deliver(interaction):
            await interaction.response.send_message("Sem permissão.", ephemeral=True)
            return
        async with SessionLocal() as session, session.begin():
            order = await session.get(Order, self.order_id)
            if order is None:
                await interaction.response.send_message("Pedido não encontrado.", ephemeral=True)
                return
            if order.status == "paid":
                order.status = "processing"
        await interaction.response.send_message("Pedido marcado como em atendimento.", ephemeral=True)

    @discord.ui.button(
        label="Marcar entregue",
        style=discord.ButtonStyle.success,
        custom_id="ticket:delivered",
    )
    async def delivered(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None or not await can_deliver(interaction):
            await interaction.response.send_message("Sem permissão de entrega.", ephemeral=True)
            return
        async with SessionLocal() as session, session.begin():
            order = await mark_order_delivered(session, order_id=self.order_id)
            config = await session.scalar(
                select(GuildConfig).where(GuildConfig.guild_id == interaction.guild.id)
            )
            delay = config.feedback_reminder_minutes if config else 5
            await schedule_feedback_reminder(session, order=order, delay_minutes=delay)
            user = await session.get(User, order.user_id)
        await publish_delivery(interaction.guild, order_id=self.order_id)
        await interaction.response.send_message("Entrega registrada.", ephemeral=True)
        if isinstance(interaction.channel, discord.TextChannel) and user is not None:
            await interaction.channel.send(
                content=f"<@{user.discord_user_id}> sua entrega foi concluída. Quer avaliar agora?",
                view=FeedbackPromptView(self.order_id),
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )

    @discord.ui.button(
        label="Fechar ticket",
        style=discord.ButtonStyle.danger,
        custom_id="ticket:close",
    )
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
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
                await target.send(
                    content=f"Transcript do pedido `{str(self.order_id)[:8]}`",
                    file=file,
                )
        if loaded:
            _, user, _, _ = loaded
            member = interaction.guild.get_member(user.discord_user_id)
            if member:
                await interaction.channel.set_permissions(member, send_messages=False, view_channel=True)
        new_name = interaction.channel.name
        if not new_name.startswith("closed-"):
            new_name = f"closed-{new_name}"[:100]
        await interaction.channel.edit(name=new_name, reason="NEXTBUY: ticket fechado")
        await interaction.followup.send("Ticket fechado e transcript processado.", ephemeral=True)
