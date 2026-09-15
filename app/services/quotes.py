from decimal import Decimal, ROUND_DOWN

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money, require_positive
from app.db.models import RobuxRate


async def quote_robux_from_credits(
    session: AsyncSession, *, guild_id: int, credits: Decimal
) -> list[tuple[RobuxRate, int]]:
    amount = require_positive(credits)
    rates = (
        await session.scalars(
            select(RobuxRate)
            .where(RobuxRate.guild_id == guild_id, RobuxRate.active.is_(True))
            .order_by(RobuxRate.sort_order, RobuxRate.id)
        )
    ).all()
    result: list[tuple[RobuxRate, int]] = []
    for rate in rates:
        robux = int((amount / rate.price_per_robux).to_integral_value(rounding=ROUND_DOWN))
        result.append((rate, robux))
    return result


async def quote_credits_for_robux(
    session: AsyncSession,
    *,
    guild_id: int,
    robux: int,
) -> list[tuple[RobuxRate, Decimal]]:
    if robux <= 0:
        raise ValueError("Quantidade de Robux precisa ser positiva")
    rates = (
        await session.scalars(
            select(RobuxRate)
            .where(RobuxRate.guild_id == guild_id, RobuxRate.active.is_(True))
            .order_by(RobuxRate.sort_order, RobuxRate.id)
        )
    ).all()
    return [(rate, money(Decimal(robux) * rate.price_per_robux)) for rate in rates]
