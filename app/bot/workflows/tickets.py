import io
import re
import unicodedata
from uuid import UUID

import discord
from sqlalchemy import select

from app.bot.checks import can_deliver, can_support
from app.bot.components_v2 import CardLayout, add_action_row, add_select_row
from app.bot.workflows.transcripts import render_channel_transcript
from app.db.models import GuildConfig, Order, OrderItem, Product, User
from app.db.session import SessionLocal
from app.services.audit import write_audit_log
from app.services.calculator import format_brl
from app.services.feedback import submit_feedback
from app.services.feedback_cards import render_feedback_card
from app.services.manual_payments import cancel_manual_pix_order
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


async def resolve_order_image(
    items: list[OrderItem],
    *,
    game_products_only: bool = False,
) -> str | None:
    """Usa primeiro a foto congelada no pedido e, para pedidos antigos, cai na foto atual do produto."""
    game_types = {"item", "gamepass", "game_pass", "gift"}
    candidate_ids: list[int] = []
    for item in items:
        metadata = dict(item.metadata_json or {})
        product_type = str(metadata.get("product_type") or "").strip().lower().replace(" ", "_")
        if game_products_only and product_type not in game_types:
            continue
        if item.image_url_snapshot:
            return item.image_url_snapshot
        if item.product_id:
            candidate_ids.append(int(item.product_id))

    if not candidate_ids:
        return None
    async with SessionLocal() as session:
        products = list(
            (
                await session.scalars(
                    select(Product).where(Product.id.in_(candidate_ids)).order_by(Product.id)
                )
            ).all()
        )
    by_id = {product.id: product for product in products}
    for item in items:
        if not item.product_id:
            continue
        product = by_id.get(int(item.product_id))
        if product is None:
            continue
        product_type = str(product.product_type or "").strip().lower().replace(" ", "_")
        if game_products_only and product_type not in game_types:
            continue
        if product.image_url:
            return product.image_url
    return None


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


def _item_lines(items: list[OrderItem]) -> list[str]:
    if not items:
        return ["Pedido sem itens."]
    return [f"- **{item.name_snapshot}** × `{item.quantity}`" for item in items]


async def _save_ticket_transcript(
    channel: discord.TextChannel,
    *,
    order_id: UUID,
    config: GuildConfig | None,
) -> bool:
    if not config or not config.transcript_channel_id:
        return False
    target = channel.guild.get_channel(config.transcript_channel_id)
    if not isinstance(target, discord.TextChannel):
        return False
    transcript = await render_channel_transcript(channel)
    file = discord.File(
        io.BytesIO(transcript),
        filename=f"transcript-{str(order_id)[:8]}.html",
    )
    await target.send(
        content=f"Transcript do pedido `{str(order_id)[:8]}` • canal {channel.name}",
        file=file,
    )
    return True


async def delete_order_ticket(
    guild: discord.Guild,
    *,
    order_id: UUID,
    actor_discord_id: int,
    expected_channel_id: int | None = None,
) -> tuple[bool, str]:
    loaded = await _load_order(order_id)
    if loaded is None:
        return False, "Pedido não encontrado."

    order, user, items, config = loaded
    channel_id = int(order.ticket_channel_id or 0)
    if expected_channel_id and channel_id and expected_channel_id != channel_id:
        return False, "Esse ticket não corresponde mais ao canal selecionado."
    if not channel_id:
        return False, "Esse pedido não possui ticket ativo."

    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            fetched = await guild.fetch_channel(channel_id)
            channel = fetched if isinstance(fetched, discord.TextChannel) else None
        except discord.NotFound:
            channel = None
        except discord.Forbidden:
            return False, "O bot não tem permissão para acessar esse canal."
        except discord.HTTPException:
            return False, "Não consegui consultar o canal no Discord."

    transcript_saved = False
    channel_name = f"canal-{channel_id}"
    if isinstance(channel, discord.TextChannel):
        channel_name = channel.name
        try:
            transcript_saved = await _save_ticket_transcript(
                channel,
                order_id=order_id,
                config=config,
            )
        except discord.HTTPException:
            transcript_saved = False

        try:
            await channel.delete(
                reason=f"NEXTBUY: ticket excluído por {actor_discord_id}"
            )
        except discord.Forbidden:
            return False, "O bot não tem permissão para excluir esse canal."
        except discord.HTTPException:
            return False, "O Discord recusou a exclusão do canal."

    product_summary = _order_name(items)
    async with SessionLocal() as session, session.begin():
        db_order = await session.scalar(
            select(Order).where(Order.id == order_id).with_for_update()
        )
        if db_order is None:
            return False, "Pedido não encontrado."
        if db_order.ticket_channel_id and int(db_order.ticket_channel_id) != channel_id:
            return False, "O ticket mudou enquanto a exclusão era processada."

        if db_order.status == "pending":
            await cancel_manual_pix_order(
                session,
                order_id=order_id,
                actor_discord_id=actor_discord_id,
                reason="ticket de pagamento excluído",
            )
            db_order = await session.get(Order, order_id)
            if db_order is None:
                return False, "Pedido não encontrado após cancelamento."

        db_order.ticket_channel_id = None
        await write_audit_log(
            session,
            guild_id=guild.id,
            actor_discord_id=actor_discord_id,
            action="ticket.delete",
            target_type="order",
            target_id=str(order_id),
            details={
                "channel_id": channel_id,
                "channel_name": channel_name,
                "customer_discord_id": user.discord_user_id,
                "product_name": product_summary,
                "amount_brl": str(order.total_credits),
                "order_status": db_order.status,
                "transcript_saved": transcript_saved,
            },
        )

    return True, f"Ticket **{channel_name}** excluído."


class TicketDeleteConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        order_id: UUID,
        owner_id: int,
        channel_id: int | None = None,
    ) -> None:
        super().__init__(timeout=90)
        self.order_id = order_id
        self.owner_id = owner_id
        self.channel_id = channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Essa confirmação pertence a outra pessoa.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="Confirmar exclusão", style=discord.ButtonStyle.danger)
    async def confirm(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        if interaction.guild is None or not await can_support(interaction):
            await interaction.response.send_message("Sem permissão.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, message = await delete_order_ticket(
            interaction.guild,
            order_id=self.order_id,
            actor_discord_id=interaction.user.id,
            expected_channel_id=self.channel_id,
        )
        try:
            await interaction.edit_original_response(
                content=("Ticket excluído com segurança." if ok else message),
                view=None,
            )
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            content="Exclusão cancelada.",
            view=None,
        )


class TicketStaffLayout(discord.ui.LayoutView):
    def __init__(
        self,
        order_id: UUID,
        *,
        title: str,
        lines: list[str],
        image_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        card = CardLayout(
            title=title,
            lines=lines,
            footer="NEXTBUY • Atendimento",
            image_url=image_url,
            timeout=timeout,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)

        legacy = TicketStaffView(order_id)
        buttons = list(legacy.children)
        for item in buttons:
            legacy.remove_item(item)
        if buttons:
            add_action_row(self.container, *buttons)


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

    product_name = _order_name(items)
    channel = await guild.create_text_channel(
        name=f"pedido-{_channel_slug(product_name)}",
        category=category,
        overwrites=overwrites,
        topic=f"NEXTBUY order={order.id} customer={user.discord_user_id}",
        reason="NEXTBUY: pedido confirmado",
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

    lines = [
        f"**Cliente:** {member.mention}",
        *_item_lines(items),
        f"**Total:** `{format_brl(order.total_credits)}`",
        "**Status:** `Confirmado`",
    ]
    image_url = await resolve_order_image(items)
    await channel.send(
        view=TicketStaffLayout(
            order.id,
            title=product_name,
            lines=lines,
            image_url=image_url,
            timeout=None,
        ),
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
    return channel


async def publish_delivery(guild: discord.Guild, *, order_id: UUID) -> None:
    """Publica sempre pelo layout V2 especializado de entrega.

    Import tardio evita ciclo de importação e garante que nenhum caminho legado volte a
    transformar imagem/emoji do produto em banner.
    """
    from app.bot.cogs.delivery_runtime import publish_delivery as publish_delivery_runtime

    await publish_delivery_runtime(guild, order_id=order_id)


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
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            user = await session.scalar(
                select(User).where(User.discord_user_id == interaction.user.id)
            )
            if user is None:
                await interaction.edit_original_response(content="Compra não encontrada.")
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
                published = await channel.send(
                    view=CardLayout(
                        title="Feedback de compra verificada",
                        description=interaction.user.mention,
                        lines=[f"**Avaliação:** `{'★' * feedback.stars}{'☆' * (5 - feedback.stars)}`"],
                        image_url=f"attachment://{filename}",
                        footer="NEXTBUY • Feedback",
                        timeout=None,
                    ),
                    file=file,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
                async with SessionLocal() as session, session.begin():
                    db_feedback = await session.get(type(feedback), feedback.id)
                    if db_feedback is not None:
                        db_feedback.published_message_id = published.id
        await interaction.edit_original_response(content="Feedback salvo. Obrigado pela avaliação.")


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
    def __init__(self, order_id: UUID, *, user_mention: str | None = None) -> None:
        super().__init__(timeout=300)
        self.order_id = order_id
        card = CardLayout(
            title="Avaliar compra",
            description="Como foi sua experiência com a NEXTBUY?",
            lines=[user_mention] if user_mention else [],
            timeout=300,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)
        add_select_row(self.container, StarSelect(order_id))


class TicketStaffView(discord.ui.View):
    def __init__(self, order_id: UUID) -> None:
        super().__init__(timeout=None)
        self.order_id = order_id
        suffix = str(order_id)
        self.processing.custom_id = f"nextbuy:ticket:{suffix}:processing"
        self.delivered.custom_id = f"nextbuy:ticket:{suffix}:delivered"
        self.close.custom_id = f"nextbuy:ticket:{suffix}:close"
        self.delete.custom_id = f"nextbuy:ticket:{suffix}:delete"

    @discord.ui.button(
        label="Em atendimento",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket:processing",
    )
    async def processing(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        if not await can_support(interaction) and not await can_deliver(interaction):
            await interaction.response.send_message("Sem permissão.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            order = await session.scalar(
                select(Order).where(Order.id == self.order_id).with_for_update()
            )
            if order is None:
                await interaction.edit_original_response(content="Pedido não encontrado.")
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
        await interaction.edit_original_response(content="Pedido marcado como em atendimento.")

    @discord.ui.button(
        label="Marcar entregue",
        style=discord.ButtonStyle.success,
        custom_id="ticket:delivered",
    )
    async def delivered(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None or not await can_deliver(interaction):
            await interaction.response.send_message("Sem permissão de entrega.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with SessionLocal() as session, session.begin():
            before = await session.scalar(
                select(Order.status).where(Order.id == self.order_id).with_for_update()
            )
            order = await mark_order_delivered(session, order_id=self.order_id)
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
        await interaction.edit_original_response(content="Entrega registrada.")
        if isinstance(interaction.channel, discord.TextChannel) and user is not None:
            await interaction.channel.send(
                view=FeedbackPromptView(
                    self.order_id,
                    user_mention=f"<@{user.discord_user_id}>",
                ),
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
        loaded = await _load_order(self.order_id)
        config = loaded[3] if loaded else None
        transcript_saved = False
        try:
            transcript_saved = await _save_ticket_transcript(
                interaction.channel,
                order_id=self.order_id,
                config=config,
            )
        except discord.HTTPException:
            transcript_saved = False
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
                        "transcript_saved": transcript_saved,
                    },
                )
        await interaction.followup.send("Ticket fechado e transcript processado.", ephemeral=True)

    @discord.ui.button(
        label="Excluir ticket",
        style=discord.ButtonStyle.danger,
        custom_id="ticket:delete",
    )
    async def delete(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None or not await can_support(interaction):
            await interaction.response.send_message("Sem permissão.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Canal inválido.", ephemeral=True)
            return
        await interaction.response.send_message(
            (
                "**Excluir permanentemente este ticket?**\n"
                "O transcript será salvo antes da exclusão quando houver canal de transcript "
                "configurado. Esta ação remove o canal do Discord."
            ),
            view=TicketDeleteConfirmView(
                order_id=self.order_id,
                owner_id=interaction.user.id,
                channel_id=interaction.channel.id,
            ),
            ephemeral=True,
        )
