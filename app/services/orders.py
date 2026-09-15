from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money, require_positive
from app.db.models import Order, OrderItem, Product, User
from app.services.wallets import apply_wallet_transaction


class OrderStateError(ValueError):
    pass


async def create_product_order(
    session: AsyncSession,
    *,
    guild_id: int,
    user_id: int,
    product: Product,
    quantity: int = 1,
) -> Order:
    if not product.active:
        raise ValueError("Produto indisponível")
    if product.price_credits is None:
        raise ValueError("Produto exige cotação antes da compra")
    if quantity <= 0:
        raise ValueError("Quantidade inválida")

    unit_price = require_positive(product.price_credits)
    total = money(unit_price * quantity)
    order = Order(guild_id=guild_id, user_id=user_id, total_credits=total, status="pending")
    session.add(order)
    await session.flush()

    metadata = dict(product.metadata_json or {})
    metadata.setdefault("game_name", product.game_name)
    metadata.setdefault("product_type", product.product_type)
    metadata.setdefault("product_slug", product.slug)

    session.add(
        OrderItem(
            order_id=order.id,
            product_id=product.id,
            name_snapshot=product.name,
            unit_price=unit_price,
            quantity=quantity,
            image_url_snapshot=product.image_url,
            metadata_json=metadata,
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
    order.status = "refunded"
    await session.flush()
    return order
