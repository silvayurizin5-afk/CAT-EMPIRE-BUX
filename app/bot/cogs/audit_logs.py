import logging
from decimal import Decimal, InvalidOperation

import discord
from discord.ext import commands, tasks

from app.db.session import SessionLocal
from app.services.audit import (
    PendingAuditDelivery,
    claim_pending_audit_deliveries,
    mark_audit_delivery_published,
    release_audit_delivery,
)

logger = logging.getLogger(__name__)

_ACTIONS: dict[str, tuple[str, str]] = {
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
}

_DETAIL_LABELS = {
    "amount": "Valor",
    "amount_brl": "Valor",
    "customer_discord_id": "Cliente",
    "channel_id": "Canal",
    "role_id": "Cargo",
    "reason": "Motivo",
    "transcript_saved": "Transcript salvo",
    "tx_id": "Transação",
    "txId": "Transação",
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


def _target_label(item: PendingAuditDelivery) -> tuple[str, str] | None:
    if not item.target_id:
        return None
    short = item.target_id[:8]
    if item.target_type == "order":
        return "Pedido", f"`#{short}`"
    if item.target_type == "ticket":
        return "Ticket", f"`#{short}`"
    label = (item.target_type or "Registro").replace("_", " ").title()
    return label, f"`{item.target_id}`"


def build_audit_embed(item: PendingAuditDelivery) -> discord.Embed:
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

    target = _target_label(item)
    if target is not None:
        embed.add_field(name=target[0], value=target[1], inline=True)

    known_keys: set[str] = set()
    for key, label in _DETAIL_LABELS.items():
        if key not in item.details:
            continue
        known_keys.add(key)
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

    embed.set_footer(
        text=f"NEXTBUY • Auditoria #{item.audit_log_id} • Código: {item.action}"[:2048]
    )
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

        try:
            await channel.send(
                embed=build_audit_embed(item),
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
