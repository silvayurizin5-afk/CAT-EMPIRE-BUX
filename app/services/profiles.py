import re
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import ZERO, money
from app.db.models import (
    Order,
    OrderItem,
    Product,
    User,
    UserEconomyAdjustment,
)
from app.services.calculator import ROBUX_PRICE_PER_100
from app.services.users import get_or_create_user

ACTIVE_SPEND_STATUSES = ("paid", "processing", "delivered")
ROBUX_TRACKED_PRODUCT_TYPES = {"robux", "gamepass", "game_pass"}
_ROBUX_IN_NAME_RE = re.compile(r"(?<!\d)(\d[\d\s.,]*)\s*robux\b", re.IGNORECASE)


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


@dataclass(slots=True)
class EconomyBase:
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


def _robux_from_name(value: object) -> int:
    match = _ROBUX_IN_NAME_RE.search(str(value or ""))
    if match is None:
        return 0
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else 0


def _robux_equivalent_from_brl(value: object, *, quantity: int = 1) -> int:
    """Converte preço em R$ para Robux usando R$ 2,90 = 100 Robux.

    O cálculo usa o preço histórico do item e arredonda para baixo porque
    Robux é inteiro e o ranking não deve creditar mais Robux do que o valor
    pago/configurado representa.
    """

    try:
        price = Decimal(str(value))
        safe_quantity = max(1, int(quantity or 1))
    except (InvalidOperation, TypeError, ValueError):
        return 0

    if not price.is_finite() or price <= 0:
        return 0

    equivalent = (
        price * Decimal(safe_quantity) * Decimal("100") / ROBUX_PRICE_PER_100
    )
    return int(equivalent.to_integral_value(rounding=ROUND_DOWN))


def _robux_from_order_item(
    metadata: dict | None,
    *,
    quantity: int = 1,
    item_name: str | None = None,
    current_product_type: str | None = None,
    current_product_metadata: dict | None = None,
    unit_price: object | None = None,
    current_product_price: object | None = None,
) -> int:
    """Calcula os Robux do ranking conforme o tipo real da compra.

    - Robux direto: usa a quantidade exata comprada.
    - Game Pass: converte o preço em R$ pela regra R$ 2,90 = 100 Robux.
    - Item/conta/outros tipos: não adicionam Robux.
    """

    snapshot = dict(metadata or {})
    current_metadata = dict(current_product_metadata or {})
    product_type = _normalize_product_type(
        snapshot.get("product_type") or current_product_type
    )

    # Tipo explícito que não seja Robux/Game Pass nunca contabiliza Robux.
    # Isso cobre item, conta/account e qualquer outro produto comum.
    if product_type and product_type not in ROBUX_TRACKED_PRODUCT_TYPES:
        return 0

    # Recuperação de pedidos antigos sem product_type.
    if not product_type:
        if snapshot.get("price_per_robux") not in {None, "", 0, "0"}:
            product_type = "robux"
        elif _robux_from_name(item_name) > 0:
            product_type = "robux"
        elif snapshot.get("robux_amount") not in {None, "", 0, "0"}:
            product_type = "robux"

    if product_type not in ROBUX_TRACKED_PRODUCT_TYPES:
        return 0

    # Game Pass: o ranking NÃO usa robux_amount manual. O equivalente vem do
    # preço em reais congelado no pedido; só cai para o preço atual se o pedido
    # legado não tiver unit_price.
    if product_type in {"gamepass", "game_pass"}:
        price = unit_price if unit_price not in {None, ""} else current_product_price
        return _robux_equivalent_from_brl(price, quantity=quantity)

    # Robux direto: usa primeiro a quantidade exata congelada no pedido.
    raw_amount = snapshot.get("robux_amount")
    if raw_amount in {None, "", 0, "0"}:
        raw_amount = current_metadata.get("robux_amount")
    amount = _positive_int(raw_amount)

    # Pedidos diretos antigos podem ter apenas a cotação histórica.
    if amount <= 0:
        raw_rate = snapshot.get("price_per_robux")
        if raw_rate not in {None, "", 0, "0"} and unit_price not in {None, ""}:
            try:
                rate = Decimal(str(raw_rate))
                price = Decimal(str(unit_price))
                inferred = price / rate
            except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
                inferred = Decimal("0")
            if inferred > 0 and inferred == inferred.to_integral_value():
                amount = int(inferred)

    if amount <= 0:
        amount = _robux_from_name(item_name)

    # Último fallback para produtos Robux antigos sem snapshot da quantidade.
    if amount <= 0:
        price = unit_price if unit_price not in {None, ""} else current_product_price
        amount = _robux_equivalent_from_brl(price, quantity=1)

    if amount <= 0:
        return 0

    try:
        safe_quantity = max(1, int(quantity or 1))
    except (TypeError, ValueError):
        safe_quantity = 1
    return amount * safe_quantity


async def _adjusted_spend_rows(session: AsyncSession, guild_id: int):
    spend = _spend_subquery(guild_id)
    rows = (
        await session.execute(
            select(
                User.id.label("user_id"),
                User.discord_user_id,
                func.coalesce(spend.c.total_spent, 0).label("base_spent"),
                func.coalesce(spend.c.completed_orders, 0).label("base_orders"),
                func.coalesce(UserEconomyAdjustment.spent_adjustment, 0).label(
                    "spent_adjustment"
                ),
                func.coalesce(UserEconomyAdjustment.robux_adjustment, 0).label(
                    "robux_adjustment"
                ),
                func.coalesce(UserEconomyAdjustment.orders_adjustment, 0).label(
                    "orders_adjustment"
                ),
            )
            .outerjoin(spend, spend.c.user_id == User.id)
            .outerjoin(
                UserEconomyAdjustment,
                and_(
                    UserEconomyAdjustment.user_id == User.id,
                    UserEconomyAdjustment.guild_id == guild_id,
                ),
            )
            .where(
                or_(
                    spend.c.user_id.is_not(None),
                    UserEconomyAdjustment.id.is_not(None),
                )
            )
        )
    ).mappings().all()
    return rows


async def _load_item_rows(
    session: AsyncSession,
    *,
    guild_id: int,
    user_ids: list[int],
    newest_first: bool = False,
):
    if not user_ids:
        return []
    statement = (
        select(
            Order.user_id.label("user_id"),
            OrderItem.name_snapshot.label("item_name"),
            OrderItem.metadata_json.label("metadata"),
            OrderItem.quantity,
            OrderItem.unit_price,
            Product.product_type.label("current_product_type"),
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
    if newest_first:
        statement = statement.order_by(Order.created_at.desc(), OrderItem.id.desc())
    return list((await session.execute(statement)).mappings().all())


async def _slug_product_map(
    session: AsyncSession,
    *,
    guild_id: int,
    item_rows,
) -> dict[str, Product]:
    slugs = {
        str(dict(row["metadata"] or {}).get("product_slug") or "").strip()
        for row in item_rows
        if row["current_product_type"] is None
    }
    slugs.discard("")
    if not slugs:
        return {}

    products = list(
        (
            await session.scalars(
                select(Product).where(
                    Product.guild_id == guild_id,
                    Product.slug.in_(slugs),
                )
            )
        ).all()
    )
    return {product.slug: product for product in products}


async def _robux_totals_for_users(
    session: AsyncSession,
    *,
    guild_id: int,
    user_ids: list[int],
    newest_first: bool = False,
) -> tuple[dict[int, int], list]:
    """Soma somente Robux realmente associados a compras de Robux/Game Pass."""

    rows = await _load_item_rows(
        session,
        guild_id=guild_id,
        user_ids=user_ids,
        newest_first=newest_first,
    )
    slug_products = await _slug_product_map(
        session,
        guild_id=guild_id,
        item_rows=rows,
    )
    totals = {user_id: 0 for user_id in user_ids}

    for row in rows:
        metadata = dict(row["metadata"] or {})
        current_type = row["current_product_type"]
        current_metadata = row["current_product_metadata"]
        current_price = row["current_product_price"]

        # Pedidos antigos podem ter perdido o product_id, mas ainda ter o slug.
        if current_type is None:
            slug = str(metadata.get("product_slug") or "").strip()
            fallback_product = slug_products.get(slug)
            if fallback_product is not None:
                current_type = fallback_product.product_type
                current_metadata = fallback_product.metadata_json
                current_price = fallback_product.price_credits

        user_id = int(row["user_id"])
        totals[user_id] = totals.get(user_id, 0) + _robux_from_order_item(
            metadata,
            quantity=row["quantity"],
            item_name=row["item_name"],
            current_product_type=current_type,
            current_product_metadata=current_metadata,
            unit_price=row["unit_price"],
            current_product_price=current_price,
        )

    return totals, rows


async def get_economy_base(
    session: AsyncSession,
    *,
    guild_id: int,
    user_id: int,
) -> EconomyBase:
    spend = _spend_subquery(guild_id)
    row = (
        await session.execute(
            select(spend.c.total_spent, spend.c.completed_orders).where(
                spend.c.user_id == user_id
            )
        )
    ).one_or_none()
    base_spent = money(row.total_spent if row else ZERO)
    base_orders = int(row.completed_orders if row else 0)
    robux_by_user, _ = await _robux_totals_for_users(
        session,
        guild_id=guild_id,
        user_ids=[user_id],
    )
    return EconomyBase(
        total_spent=base_spent,
        completed_orders=base_orders,
        robux_purchased=robux_by_user.get(user_id, 0),
    )


async def set_user_economy_target(
    session: AsyncSession,
    *,
    guild_id: int,
    discord_user_id: int,
    total_spent: Decimal,
    completed_orders: int,
) -> CustomerProfile:
    target_spent = money(total_spent)
    if target_spent < ZERO:
        raise ValueError("O total gasto não pode ser negativo.")
    if completed_orders < 0:
        raise ValueError("A quantidade de compras não pode ser negativa.")

    user = await get_or_create_user(session, discord_user_id)
    base = await get_economy_base(session, guild_id=guild_id, user_id=user.id)

    adjustment = await session.scalar(
        select(UserEconomyAdjustment).where(
            UserEconomyAdjustment.guild_id == guild_id,
            UserEconomyAdjustment.user_id == user.id,
        )
    )
    if adjustment is None:
        adjustment = UserEconomyAdjustment(guild_id=guild_id, user_id=user.id)
        session.add(adjustment)

    adjustment.spent_adjustment = money(target_spent - base.total_spent)
    # O ajuste manual de R$ não altera Robux; Robux vem das compras elegíveis.
    adjustment.robux_adjustment = 0
    adjustment.orders_adjustment = int(completed_orders - base.completed_orders)
    await session.flush()
    return await get_customer_profile(
        session,
        guild_id=guild_id,
        discord_user_id=discord_user_id,
    )


async def clear_user_economy_adjustment(
    session: AsyncSession,
    *,
    guild_id: int,
    discord_user_id: int,
) -> CustomerProfile:
    user = await session.scalar(
        select(User).where(User.discord_user_id == discord_user_id)
    )
    if user is None:
        return CustomerProfile(ZERO, 0, None)

    adjustment = await session.scalar(
        select(UserEconomyAdjustment).where(
            UserEconomyAdjustment.guild_id == guild_id,
            UserEconomyAdjustment.user_id == user.id,
        )
    )
    if adjustment is not None:
        await session.delete(adjustment)
        await session.flush()

    return await get_customer_profile(
        session,
        guild_id=guild_id,
        discord_user_id=discord_user_id,
    )


async def get_customer_profile(
    session: AsyncSession, *, guild_id: int, discord_user_id: int
) -> CustomerProfile:
    user = await session.scalar(
        select(User).where(User.discord_user_id == discord_user_id)
    )
    if user is None:
        return CustomerProfile(ZERO, 0, None)

    economy_rows = await _adjusted_spend_rows(session, guild_id)
    user_economy = next(
        (row for row in economy_rows if int(row["user_id"]) == user.id),
        None,
    )

    if user_economy is None:
        total_spent = ZERO
        completed_orders = 0
        robux_adjustment = 0
    else:
        total_spent = max(
            ZERO,
            money(
                Decimal(str(user_economy["base_spent"]))
                + Decimal(str(user_economy["spent_adjustment"]))
            ),
        )
        completed_orders = max(
            0,
            int(user_economy["base_orders"])
            + int(user_economy["orders_adjustment"]),
        )
        robux_adjustment = int(user_economy["robux_adjustment"])

    robux_by_user, item_rows = await _robux_totals_for_users(
        session,
        guild_id=guild_id,
        user_ids=[user.id],
        newest_first=True,
    )
    robux_purchased = max(0, robux_by_user.get(user.id, 0) + robux_adjustment)

    leaderboard_position: int | None = None
    if total_spent > ZERO or robux_purchased > 0:
        higher = 0
        for row in economy_rows:
            candidate_spent = max(
                ZERO,
                money(
                    Decimal(str(row["base_spent"]))
                    + Decimal(str(row["spent_adjustment"]))
                ),
            )
            if candidate_spent > total_spent:
                higher += 1
        leaderboard_position = higher + 1

    recent_products: list[str] = []
    games: list[str] = []
    for row in item_rows:
        item_name = str(row["item_name"] or "").strip()
        if item_name and item_name not in recent_products:
            recent_products.append(item_name)
        game_name = str(dict(row["metadata"] or {}).get("game_name") or "").strip()
        if game_name and game_name not in games:
            games.append(game_name)
        if len(recent_products) >= 5 and len(games) >= 5:
            break

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
    economy_rows = await _adjusted_spend_rows(session, guild_id)
    if not economy_rows:
        return []

    user_ids = [int(row["user_id"]) for row in economy_rows]
    robux_by_user, _ = await _robux_totals_for_users(
        session,
        guild_id=guild_id,
        user_ids=user_ids,
    )

    entries: list[tuple[int, LeaderboardEntry]] = []
    for row in economy_rows:
        user_id = int(row["user_id"])
        total_spent = max(
            ZERO,
            money(
                Decimal(str(row["base_spent"]))
                + Decimal(str(row["spent_adjustment"]))
            ),
        )
        completed_orders = max(
            0,
            int(row["base_orders"]) + int(row["orders_adjustment"]),
        )
        robux_purchased = max(
            0,
            robux_by_user.get(user_id, 0) + int(row["robux_adjustment"]),
        )
        if total_spent <= ZERO and robux_purchased <= 0 and completed_orders <= 0:
            continue
        entries.append(
            (
                user_id,
                LeaderboardEntry(
                    discord_user_id=int(row["discord_user_id"]),
                    total_spent=total_spent,
                    completed_orders=completed_orders,
                    robux_purchased=robux_purchased,
                ),
            )
        )

    entries.sort(
        key=lambda item: (
            -item[1].total_spent,
            -item[1].completed_orders,
            -item[1].robux_purchased,
            item[0],
        )
    )
    safe_limit = max(1, min(limit, 100))
    return [entry for _, entry in entries[:safe_limit]]
