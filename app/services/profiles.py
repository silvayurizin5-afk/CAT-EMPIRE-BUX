from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import ZERO, money
from app.db.models import Order, OrderItem, Product, User
from app.services.calculator import ROBUX_PRICE_PER_100

ACTIVE_SPEND_STATUSES = ("paid", "processing", "delivered")
ROBUX_TRACKED_PRODUCT_TYPES = {"robux", "gamepass", "game_pass"}


@dataclass(slots=True)
class CustomerProfile:
    total_spent: Decimal
    completed_orders: int
    leaderboard_position: int | None
    robux_purchased: int = 0
    recent_products: tuple[str, ...] = ()
    games: tuple[str, ...] = ()


@dataclass(slots=True)
class LeaderboardEntry:
    discord_user_id: int
    total_spent: Decimal
    completed_orders: int
    robux_purchased: int


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


def _normalize_product_type(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _positive_int(value: object) -> int:
    if value in {None, ""}:
        return 0
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return 0
    if parsed <= 0 or parsed != parsed.to_integral_value():
        return 0
    return int(parsed)


def _robux_from_order_item(
    metadata: dict | None,
    *,
    quantity: int = 1,
    current_product_type: str | None = None,
    current_product_metadata: dict | None = None,
    unit_price: object | None = None,
    current_product_price: object | None = None,
) -> int:
    """Conta Robux somente para Game Pass e compras diretas de Robux.

    Itens comuns atualizam apenas o valor gasto em reais, mesmo que algum metadata
    antigo contenha acidentalmente a chave robux_amount.
    """

    snapshot = dict(metadata or {})
    product_type = _normalize_product_type(
        snapshot.get("product_type") or current_product_type
    )
    if product_type not in ROBUX_TRACKED_PRODUCT_TYPES:
        return 0

    raw_amount = snapshot.get("robux_amount")
    if raw_amount in {None, ""}:
        raw_amount = dict(current_product_metadata or {}).get("robux_amount")

    amount = _positive_int(raw_amount)

    # Produtos diretos do tipo "robux" mais antigos podem não ter robux_amount
    # congelado no metadata. Nesses casos, o preço unitário normal da NEXTBUY
    # permite reconstruir a quantidade: R$ 2,90 = 100 Robux.
    if amount <= 0 and product_type == "robux":
        raw_price = unit_price if unit_price not in {None, ""} else current_product_price
        try:
            price = Decimal(str(raw_price))
            inferred = price * Decimal("100") / ROBUX_PRICE_PER_100
        except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
            inferred = Decimal("0")
        if inferred > 0 and inferred == inferred.to_integral_value():
            amount = int(inferred)

    if amount <= 0:
        return 0

    try:
        safe_quantity = max(1, int(quantity or 1))
    except (TypeError, ValueError):
        safe_quantity = 1
    return amount * safe_quantity


async def get_customer_profile(
    session: AsyncSession, *, guild_id: int, discord_user_id: int
) -> CustomerProfile:
    user = await session.scalar(select(User).where(User.discord_user_id == discord_user_id))
    if user is None:
        return CustomerProfile(ZERO, 0, None)

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
            select(
                OrderItem.name_snapshot,
                OrderItem.metadata_json,
                OrderItem.quantity,
                OrderItem.unit_price,
                Product.product_type,
                Product.metadata_json.label("current_product_metadata"),
                Product.price_credits.label("current_product_price"),
            )
            .join(Order, Order.id == OrderItem.order_id)
            .outerjoin(Product, Product.id == OrderItem.product_id)
            .where(
                Order.guild_id == guild_id,
                Order.user_id == user.id,
                Order.status.in_(ACTIVE_SPEND_STATUSES),
            )
            .order_by(Order.created_at.desc(), OrderItem.id.desc())
            .limit(200)
        )
    ).all()

    recent_products: list[str] = []
    games: list[str] = []
    robux_purchased = 0
    for (
        item_name,
        metadata,
        quantity,
        unit_price,
        product_type,
        product_metadata,
        product_price,
    ) in item_rows:
        robux_purchased += _robux_from_order_item(
            metadata,
            quantity=quantity,
            current_product_type=product_type,
            current_product_metadata=product_metadata,
            unit_price=unit_price,
            current_product_price=product_price,
        )
        if item_name not in recent_products:
            recent_products.append(item_name)
        game_name = (metadata or {}).get("game_name")
        if game_name and game_name not in games:
            games.append(str(game_name))

    return CustomerProfile(
        total_spent=total_spent,
        completed_orders=completed_orders,
        leaderboard_position=leaderboard_position,
        robux_purchased=robux_purchased,
        recent_products=tuple(recent_products[:5]),
        games=tuple(games[:5]),
    )


async def list_leaderboard(
    session: AsyncSession, *, guild_id: int, limit: int = 20
) -> list[LeaderboardEntry]:
    spend = _spend_subquery(guild_id)
    rows = (
        await session.execute(
            select(User.id, User.discord_user_id, spend.c.total_spent, spend.c.completed_orders)
            .join(spend, spend.c.user_id == User.id)
            .where(spend.c.total_spent > 0)
            .order_by(spend.c.total_spent.desc(), spend.c.completed_orders.desc(), User.id.asc())
            .limit(max(1, min(limit, 100)))
        )
    ).all()
    if not rows:
        return []

    user_ids = [int(row.id) for row in rows]
    item_rows = (
        await session.execute(
            select(
                Order.user_id,
                OrderItem.metadata_json,
                OrderItem.quantity,
                OrderItem.unit_price,
                Product.product_type,
                Product.metadata_json.label("current_product_metadata"),
                Product.price_credits.label("current_product_price"),
            )
            .join(OrderItem, OrderItem.order_id == Order.id)
            .outerjoin(Product, Product.id == OrderItem.product_id)
            .where(
                Order.guild_id == guild_id,
                Order.user_id.in_(user_ids),
                Order.status.in_(ACTIVE_SPEND_STATUSES),
            )
        )
    ).all()
    robux_by_user: dict[int, int] = {user_id: 0 for user_id in user_ids}
    for (
        user_id,
        metadata,
        quantity,
        unit_price,
        product_type,
        product_metadata,
        product_price,
    ) in item_rows:
        robux_by_user[int(user_id)] = robux_by_user.get(
            int(user_id), 0
        ) + _robux_from_order_item(
            metadata,
            quantity=quantity,
            current_product_type=product_type,
            current_product_metadata=product_metadata,
            unit_price=unit_price,
            current_product_price=product_price,
        )

    return [
        LeaderboardEntry(
            discord_user_id=int(row.discord_user_id),
            total_spent=money(row.total_spent),
            completed_orders=int(row.completed_orders),
            robux_purchased=robux_by_user.get(int(row.id), 0),
        )
        for row in rows
    ]
