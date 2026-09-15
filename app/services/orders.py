from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money, require_positive
from app.db.models import FeedbackReminder, Order, OrderItem, Product, RobuxRate, User
from app.services.wallets import apply_wallet_transaction


class OrderStateError(ValueError):
    pass


class OutOfStockError(ValueError):
    pass


async def create_product_order(
    session: AsyncSession,
    *,
    guild_id: int,
    user_id: int,
    product: Product,
    quantity: int = 1,
) -> Order:
    if quantity <= 0:
        raise ValueError("Quantidade inválida")

    locked_product = await session.scalar(
        select(Product).where(Product.id == product.id).with_for_update()
    )
    if locked_product is None or locked_product.guild_id != guild_id or not locked_product.active:
        raise ValueError("Produto indisponível")
    if locked_product.price_credits is None:
        raise ValueError("Produto exige cotação antes da compra")
    if locked_product.stock_quantity is not None:
        if locked_product.stock_quantity < quantity:
            raise OutOfStockError("Estoque insuficiente para esta compra")
        locked_product.stock_quantity -= quantity

    unit_price = require_positive(locked_product.price_credits)
    total = money(unit_price * quantity)
    order = Order(guild_id=guild_id, user_id=user_id, total_credits=total, status="pending")
    session.add(order)
    await session.flush()

    metadata = dict(locked_product.metadata_json or {})
    metadata.setdefault("game_name", locked_product.game_name)
    metadata.setdefault("product_type", locked_product.product_type)
    metadata.setdefault("product_slug", locked_product.slug)

    session.add(
        OrderItem(
            order_id=order.id,
            product_id=locked_product.id,
            name_snapshot=locked_product.name,
            unit_price=unit_price,
            quantity=quantity,
            image_url_snapshot=locked_product.image_url,
            metadata_json=metadata,
        )
    )
    await session.flush()
    return order


async def create_robux_order(
    session: AsyncSession,
    *,
    guild_id: int,
    user_id: int,
    rate: RobuxRate,
    robux: int,
) -> Order:
    if not rate.active or rate.guild_id != guild_id:
        raise ValueError("Cotação de Robux indisponível")
    if robux <= 0 or robux > 1_000_000:
        raise ValueError("Quantidade de Robux inválida")

    total = require_positive(money(Decimal(robux) * rate.price_per_robux))
    order = Order(guild_id=guild_id, user_id=user_id, total_credits=total, status="pending")
    session.add(order)
    await session.flush()
    session.add(
        OrderItem(
            order_id=order.id,
            product_id=None,
            name_snapshot=f"{rate.label} • {robux} Robux",
            unit_price=total,
            quantity=1,
            metadata_json={
                "product_type": "robux",
                "robux_amount": robux,
                "rate_code": rate.code,
                "rate_label": rate.label,
                "price_per_robux": str(rate.price_per_robux),
                "delivery_label": rate.delivery_label,
            },
        )
    )
    await session.flush()
    return order


async def pay_order_with_credits(session: AsyncSession, *, order_id: UUID) -> Order:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise ValueError("Pedido não encontrado")
    if order.status in {"paid", "processing", "delivered"}:
        return order
    if order.status != "pending":
        raise OrderStateError(f"Pedido não pode ser pago no estado {order.status}")

    await apply_wallet_transaction(
        session,
        user_id=order.user_id,
        amount=-money(order.total_credits),
        kind="purchase",
        reference=f"order:{order.id}:purchase",
        details={"order_id": str(order.id)},
    )

    user = await session.scalar(select(User).where(User.id == order.user_id).with_for_update())
    if user is None:
        raise RuntimeError("Usuário do pedido não encontrado")
    user.total_spent = money(user.total_spent + order.total_credits)
    order.status = "paid"
    order.paid_at = datetime.now(UTC)
    await session.flush()
    return order


async def mark_order_delivered(session: AsyncSession, *, order_id: UUID) -> Order:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise ValueError("Pedido não encontrado")
    if order.status == "delivered":
        return order
    if order.status not in {"paid", "processing"}:
        raise OrderStateError("Somente pedidos pagos podem ser entregues")
    order.status = "delivered"
    order.delivered_at = datetime.now(UTC)
    await session.flush()
    return order


async def refund_order(session: AsyncSession, *, order_id: UUID, reason: str) -> Order:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise ValueError("Pedido não encontrado")
    if order.status == "refunded":
        return order
    if order.status not in {"paid", "processing", "delivered"}:
        raise OrderStateError("Pedido não está elegível para reembolso")

    await apply_wallet_transaction(
        session,
        user_id=order.user_id,
        amount=money(order.total_credits),
        kind="refund",
        reference=f"order:{order.id}:refund",
        details={"order_id": str(order.id), "reason": reason[:500]},
    )
    user = await session.scalar(select(User).where(User.id == order.user_id).with_for_update())
    if user is not None:
        user.total_spent = max(money("0"), money(user.total_spent - order.total_credits))

    items = list(
        (
            await session.scalars(
                select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.id)
            )
        ).all()
    )
    for item in items:
        if item.product_id is None:
            continue
        product = await session.scalar(
            select(Product).where(Product.id == item.product_id).with_for_update()
        )
        if product is not None and product.stock_quantity is not None:
            product.stock_quantity += item.quantity

    reminder = await session.scalar(
        select(FeedbackReminder).where(FeedbackReminder.order_id == order.id).with_for_update()
    )
    if reminder is not None and reminder.completed_at is None:
        reminder.completed_at = datetime.now(UTC)

    order.status = "refunded"
    await session.flush()
    return order
