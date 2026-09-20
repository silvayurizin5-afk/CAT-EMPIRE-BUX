import logging
from decimal import Decimal, InvalidOperation
from uuid import UUID

import discord
from sqlalchemy import select
from discord.ext import commands, tasks

from app.db.models import Order, OrderItem, User
from app.db.session import SessionLocal
from app.services.audit import (
    PendingAuditDelivery,
    claim_pending_audit_deliveries,
    mark_audit_delivery_published,
    release_audit_delivery,
)

logger = logging.getLogger(__name__)

_ACTIONS: dict[str, tuple[str, str]] = {
    "pix.payment_ticket.open": (
        "Ticket de pagamento PIX aberto",
        "Um canal privado de pagamento PIX foi criado.",
    ),
    "order.payment_confirmed_manual_pix": (
        "Pagamento PIX confirmado",
        "O pagamento foi confirmado manualmente pela equipe.",
    ),
    "order.cancelled_manual_pix": (
        "Pedido cancelado",
        "Um pedido aguardando pagamento foi cancelado.",
    ),
    "order.purchase": ("Compra concluída", "A compra foi registrada com sucesso."),
    "order.processing": ("Pedido em atendimento", "A equipe iniciou o atendimento do pedido."),
    "order.delivered": ("Pedido entregue", "O pedido foi marcado como entregue."),
    "ticket.open": ("Ticket aberto", "Um canal privado de atendimento foi criado."),
    "ticket.close": ("Ticket fechado", "O atendimento foi encerrado e o ticket foi fechado."),
    "ticket.delete": (
        "Ticket excluído",
        "O canal do ticket foi excluído permanentemente.",
    ),
}

_DETAIL_LABELS = {
    "amount": "Valor",
    "amount_brl": "Valor",
    "customer_discord_id": "Cliente",
    "channel_id": "Canal",
    "role_id": "Cargo",
    "reason": "Motivo",
    "transcript_saved": "Transcript salvo",
    "tx_id": "Transação PIX",
    "txId": "Transação PIX",
    "txid": "Transação PIX",
    "payment_id": "Pagamento",
    "order_id": "Pedido",
    "product_id": "Produto",
    "product_name": "Produto",
    "quantity": "Quantidade",
    "stock": "Estoque",
    "coupon_code": "Cupom",
    "discount_percent": "Desconto",
    "robux": "Robux",
    "robux_amount": "Robux",
    "rate_code": "Cotação",
    "price_per_robux": "Preço por Robux",
    "old_status": "Status anterior",
    "new_status": "Novo status",
    "automatic_reminders": "Lembretes automáticos",
    "emoji": "Emoji",
    "channel_name": "Canal",
    "order_status": "Status",
}


def _format_money(value) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    return f"R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _format_detail(key: str, value) -> str:
    if key in {"amount", "amount_brl"}:
        return _format_money(value)
    if key == "customer_discord_id" and str(value).isdigit():
        return f"<@{value}>"
    if key == "channel_id" and str(value).isdigit():
        return f"<#{value}>"
    if key == "role_id" and str(value).isdigit():
        return f"<@&{value}>"
    if key == "discount_percent":
        try:
            return f"{Decimal(str(value)):g}%"
        except (InvalidOperation, ValueError):
            return str(value)
    if isinstance(value, bool):
        return "Sim" if value else "Não"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value) or "—"
    if isinstance(value, dict):
        return ", ".join(f"{sub_key}: {sub_value}" for sub_key, sub_value in value.items()) or "—"
    return str(value)


def _status_label(status: str) -> str:
    return {
        "pending": "Aguardando pagamento",
        "paid": "Pago",
        "processing": "Em atendimento",
        "delivered": "Entregue",
        "cancelled": "Cancelado",
    }.get(status, status.replace("_", " ").title())


async def _load_order_context(
    item: PendingAuditDelivery,
    guild: discord.Guild,
) -> dict[str, object] | None:
    if item.target_type != "order" or not item.target_id:
        return None
    try:
        order_id = UUID(item.target_id)
    except ValueError:
        return None

    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(Order, User)
                .join(User, User.id == Order.user_id)
                .where(Order.id == order_id, Order.guild_id == guild.id)
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

    member = guild.get_member(user.discord_user_id)
    display_name = member.display_name if member is not None else "Usuário"
    account_created_at = int(discord.utils.snowflake_time(user.discord_user_id).timestamp())
    products = [
        f"{order_item.name_snapshot} × {order_item.quantity}"
        for order_item in items
    ] or ["Pedido sem itens"]
    return {
        "customer_id": user.discord_user_id,
        "customer_name": display_name,
        "account_created_at": account_created_at,
        "products": products,
        "total": order.total_credits,
        "status": order.status,
        "channel_id": order.ticket_channel_id or item.details.get("channel_id"),
    }


def _target_label(item: PendingAuditDelivery) -> tuple[str, str] | None:
    if not item.target_id:
        return None
    if item.target_type in {"order", "ticket"}:
        return None
    label = (item.target_type or "Registro").replace("_", " ").title()
    return label, f"`{item.target_id}`"


def build_audit_embed(
    item: PendingAuditDelivery,
    *,
    order_context: dict[str, object] | None = None,
) -> discord.Embed:
    title, description = _ACTIONS.get(
        item.action,
        (item.action.replace("_", " ").replace(".", " • ").title(), "Evento registrado pela NEXTBUY."),
    )
    embed = discord.Embed(
        title=f"NEXTBUY • {title}",
        description=description,
        timestamp=item.created_at,
        color=discord.Color.from_rgb(43, 45, 49),
    )

    embed.add_field(
        name="Responsável",
        value=f"<@{item.actor_discord_id}>" if item.actor_discord_id else "Sistema",
        inline=True,
    )

    if order_context is not None:
        customer_id = int(order_context["customer_id"])
        customer_name = str(order_context["customer_name"])
        products = list(order_context.get("products") or [])
        channel_id = order_context.get("channel_id")
        channel_text = f"<#{channel_id}>" if channel_id else "Canal já removido"
        created_at = int(order_context.get("account_created_at") or 0)
        user_lines = [
            f"<@{customer_id}> `{customer_name} ({customer_id})`",
            f"**ID:** `{customer_id}`",
        ]
        if created_at:
            user_lines.append(f"**Conta criada em:** <t:{created_at}:F>")
        embed.add_field(
            name="Usuário",
            value="\n".join(user_lines)[:1024],
            inline=False,
        )
        detail_lines = [
            f"**Produto(s):** {', '.join(str(product) for product in products)}",
            f"**Valor:** {_format_money(order_context['total'])}",
            f"**Status:** {_status_label(str(order_context['status']))}",
            f"**Canal:** {channel_text}",
        ]
        embed.add_field(
            name="Detalhes",
            value="\n".join(detail_lines)[:1024],
            inline=False,
        )

    target = _target_label(item)
    if target is not None:
        embed.add_field(name=target[0], value=target[1], inline=True)

    known_keys: set[str] = set()
    contextual_keys = (
        {"customer_discord_id", "channel_id", "channel_name", "amount", "amount_brl", "order_status", "product_name"}
        if order_context is not None
        else set()
    )
    for key, label in _DETAIL_LABELS.items():
        if key not in item.details:
            continue
        known_keys.add(key)
        if key in contextual_keys:
            continue
        embed.add_field(
            name=label,
            value=_format_detail(key, item.details[key])[:1024],
            inline=key not in {"reason"},
        )

    extras = [
        f"**{key.replace('_', ' ').replace('.', ' ').title()}:** {_format_detail(key, value)}"
        for key, value in item.details.items()
        if key not in known_keys
    ]
    if extras:
        embed.add_field(name="Informações adicionais", value="\n".join(extras)[:1024], inline=False)

    embed.set_footer(text="NEXTBUY • Auditoria")
    return embed


class AuditLogsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.worker.start()

    def cog_unload(self) -> None:
        self.worker.cancel()

    async def _deliver(self, item: PendingAuditDelivery) -> None:
        guild = self.bot.get_guild(item.guild_id)
        if guild is None:
            async with SessionLocal() as session, session.begin():
                await release_audit_delivery(
                    session,
                    delivery_id=item.delivery_id,
                    error="Servidor não encontrado no cache do bot",
                )
            return

        channel = guild.get_channel(item.logs_channel_id)
        if not isinstance(channel, discord.TextChannel):
            async with SessionLocal() as session, session.begin():
                await release_audit_delivery(
                    session,
                    delivery_id=item.delivery_id,
                    error="Canal de logs não encontrado ou não é textual",
                )
            return

        order_context = await _load_order_context(item, guild)

        try:
            await channel.send(
                embed=build_audit_embed(item, order_context=order_context),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as exc:
            async with SessionLocal() as session, session.begin():
                await release_audit_delivery(
                    session,
                    delivery_id=item.delivery_id,
                    error=f"Falha ao publicar no Discord: {exc}",
                )
            return

        async with SessionLocal() as session, session.begin():
            await mark_audit_delivery_published(
                session,
                delivery_id=item.delivery_id,
            )

    @tasks.loop(seconds=20)
    async def worker(self) -> None:
        async with SessionLocal() as session, session.begin():
            pending = await claim_pending_audit_deliveries(session)
        for item in pending:
            try:
                await self._deliver(item)
            except Exception:
                logger.exception("Erro inesperado ao publicar audit log %s", item.audit_log_id)
                async with SessionLocal() as session, session.begin():
                    await release_audit_delivery(
                        session,
                        delivery_id=item.delivery_id,
                        error="Erro inesperado no worker de auditoria",
                    )

    @worker.before_loop
    async def before_worker(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AuditLogsCog(bot))
