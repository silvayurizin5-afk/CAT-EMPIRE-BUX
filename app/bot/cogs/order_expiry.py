from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import discord
from discord.ext import commands, tasks
from sqlalchemy import exists, select

from app.core.config import settings
from app.db.models import Order
from app.db.order_payment_models import OrderPayment
from app.db.session import SessionLocal
from app.services.manual_payments import cancel_manual_pix_order

logger = logging.getLogger(__name__)


class OrderExpiryCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.worker.start()

    def cog_unload(self) -> None:
        self.worker.cancel()

    @tasks.loop(minutes=1)
    async def worker(self) -> None:
        ttl_minutes = max(5, settings.pix_order_ttl_minutes)
        cutoff = datetime.now(UTC) - timedelta(minutes=ttl_minutes)

        async with SessionLocal() as session:
            rows = list(
                (
                    await session.scalars(
                        select(Order)
                        .where(
                            Order.status == "pending",
                            Order.created_at <= cutoff,
                            ~exists(
                                select(OrderPayment.id).where(
                                    OrderPayment.order_id == Order.id
                                )
                            ),
                        )
                        .order_by(Order.created_at.asc())
                        .limit(100)
                    )
                ).all()
            )

        for pending in rows:
            channel_id = pending.ticket_channel_id
            try:
                async with SessionLocal() as session, session.begin():
                    current = await session.get(Order, pending.id)
                    if current is None or current.status != "pending":
                        continue
                    await cancel_manual_pix_order(
                        session,
                        order_id=current.id,
                        actor_discord_id=None,
                        reason="expirado automaticamente por falta de pagamento",
                    )
            except ValueError:
                continue
            except Exception:
                logger.exception("Falha ao expirar pedido PIX %s", pending.id)
                continue

            if channel_id:
                channel = self.bot.get_channel(channel_id)
                if isinstance(channel, discord.TextChannel):
                    try:
                        await channel.delete(reason="NEXTBUY: pedido PIX expirado")
                    except discord.HTTPException:
                        logger.warning(
                            "Pedido %s expirou, mas o canal %s não pôde ser removido.",
                            pending.id,
                            channel_id,
                        )

    @worker.before_loop
    async def before_worker(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OrderExpiryCog(bot))
