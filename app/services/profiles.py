from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import ZERO, money
from app.db.models import Order, OrderItem, User, Wallet

ACTIVE_SPEND_STATUSES = ("paid", "processing", "delivered")


@dataclass(slots=True)
class CustomerProfile:
    total_spent: Decimal
    balance: Decimal
    completed_orders: int
    leaderboard_position: int | None
    recent_products: tuple[str, ...] = ()
    games: tuple[str, ...] = ()


@dataclass(slots=True)
class LeaderboardEntry:
    discord_user_id: int
    total_spent: Decimal
    completed_orders: int


def _spend_subquery(guild_id: int):
    return (
        select(
            Order.user_id.label("user_id"),
            func.sum(Order.total_credits).label("total_spent"),
            func.count(Order.id).label("completed_orders"),
        )
        .where(Order.guild_id == guild_id, Order.status.in_(ACTIVE_SPEND_STATUSES))
        .group_by(Order.user_id)
        .subquery()
    )


async def get_customer_profile(
    session: AsyncSession, *, guild_id: int, discord_user_id: int
) -> CustomerProfile:
    user = await session.scalar(select(User).where(User.discord_user_id == discord_user_id))
    if user is None:
        return CustomerProfile(ZERO, ZERO, 0, None)

    balance = await session.scalar(select(Wallet.balance).where(Wallet.user_id == user.id))
    spend = _spend_subquery(guild_id)
    row = (
        await session.execute(
            select(spend.c.total_spent, spend.c.completed_orders).where(spend.c.user_id == user.id)
        )
    ).one_or_none()
    total_spent = money(row.total_spent if row else ZERO)
    completed_orders = int(row.completed_orders if row else 0)

    if total_spent > ZERO:
        position = await session.scalar(
            select(func.count()).select_from(spend).where(spend.c.total_spent > total_spent)
        )
        leaderboard_position: int | None = int(position or 0) + 1
    else:
        leaderboard_position = None

    item_rows = (
        await session.execute(
            select(OrderItem.name_snapshot, OrderItem.metadata_json)
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                Order.guild_id == guild_id,
                Order.user_id == user.id,
                Order.status.in_(ACTIVE_SPEND_STATUSES),
            )
            .order_by(Order.created_at.desc(), OrderItem.id.desc())
            .limit(30)
        )
    ).all()

    recent_products: list[str] = []
    games: list[str] = []
    for item_name, metadata in item_rows:
        if item_name not in recent_products:
            recent_products.append(item_name)
        game_name = (metadata or {}).get("game_name")
        if game_name and game_name not in games:
            games.append(str(game_name))

    return CustomerProfile(
        total_spent=total_spent,
        balance=money(balance or ZERO),
        completed_orders=completed_orders,
        leaderboard_position=leaderboard_position,
        recent_products=tuple(recent_products[:5]),
        games=tuple(games[:5]),
    )


async def list_leaderboard(
    session: AsyncSession, *, guild_id: int, limit: int = 20
) -> list[LeaderboardEntry]:
    spend = _spend_subquery(guild_id)
    rows = (
        await session.execute(
            select(User.discord_user_id, spend.c.total_spent, spend.c.completed_orders)
            .join(spend, spend.c.user_id == User.id)
            .where(spend.c.total_spent > 0)
            .order_by(spend.c.total_spent.desc(), spend.c.completed_orders.desc(), User.id.asc())
            .limit(max(1, min(limit, 100)))
        )
    ).all()
    return [
        LeaderboardEntry(
            discord_user_id=int(row.discord_user_id),
            total_spent=money(row.total_spent),
            completed_orders=int(row.completed_orders),
        )
        for row in rows
    ]
