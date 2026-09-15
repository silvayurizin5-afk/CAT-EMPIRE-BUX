from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import ZERO, money
from app.db.models import Order, User, Wallet


@dataclass(slots=True)
class CustomerProfile:
    total_spent: Decimal
    balance: Decimal
    completed_orders: int
    leaderboard_position: int | None


async def get_customer_profile(
    session: AsyncSession, *, guild_id: int, discord_user_id: int
) -> CustomerProfile:
    user = await session.scalar(select(User).where(User.discord_user_id == discord_user_id))
    if user is None:
        return CustomerProfile(ZERO, ZERO, 0, None)

    balance = await session.scalar(select(Wallet.balance).where(Wallet.user_id == user.id))
    completed_orders = await session.scalar(
        select(func.count(Order.id)).where(
            Order.guild_id == guild_id,
            Order.user_id == user.id,
            Order.status.in_(["paid", "processing", "delivered"]),
        )
    )
    position = await session.scalar(
        select(func.count(User.id) + 1).where(User.total_spent > user.total_spent)
    )
    return CustomerProfile(
        total_spent=money(user.total_spent),
        balance=money(balance or ZERO),
        completed_orders=int(completed_orders or 0),
        leaderboard_position=int(position or 1),
    )
