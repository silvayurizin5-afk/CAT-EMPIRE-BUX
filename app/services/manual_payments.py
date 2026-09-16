from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.db.models import Order, OrderItem, Product, User
from app.services.audit import write_audit_log
from app.services.orders import OrderStateError


async def confirm_manual_pix_payment(
    session: AsyncSession,
    *,
    order_id: UUID,
    actor_discord_id: int,
) -> Order:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise ValueError("Pedido não encontrado")
    if order.status in {"paid", "processing", "delivered"}:
        return order
    if order.status != "pending":
        raise OrderStateError(f"Pedido não pode ser confirmado no estado {order.status}")

    user = await session.scalar(select(User).where(User.id == order.user_id).with_for_update())
    if user is None:
        raise RuntimeError("Usuário do pedido não encontrado")

    order.status = "paid"
    order.paid_at = datetime.now(UTC)
    user.total_spent = money(user.total_spent + order.total_credits)
    await write_audit_log(
        session,
        guild_id=order.guild_id,
        actor_discord_id=actor_discord_id,
        action="order.payment_confirmed_manual_pix",
        target_type="order",
        target_id=str(order.id),
        details={
            "amount_brl": str(money(order.total_credits)),
            "customer_discord_id": user.discord_user_id,
        },
    )
    await session.flush()
    return order


async def cancel_manual_pix_order(
    session: AsyncSession,
    *,
    order_id: UUID,
    actor_discord_id: int,
    reason: str = "manual",
) -> Order:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise ValueError("Pedido não encontrado")
    if order.status == "cancelled":
        return order
    if order.status != "pending":
        raise OrderStateError("Somente pedidos aguardando pagamento podem ser cancelados")

    items = list(
        (
            await session.scalars(
                select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.id)
            )
        ).all()
    )
    coupon_codes: set[str] = set()
    for item in items:
        if item.product_id is not None:
            product = await session.scalar(
                select(Product).where(Product.id == item.product_id).with_for_update()
            )
            if product is not None and product.stock_quantity is not None:
                product.stock_quantity += item.quantity
        coupon_code = str((item.metadata_json or {}).get("coupon_code") or "").strip()
        if coupon_code:
            coupon_codes.add(coupon_code)

    if coupon_codes:
        from app.db.store_models import StoreCoupon

        coupons = list(
            (
                await session.scalars(
                    select(StoreCoupon)
                    .where(
                        StoreCoupon.guild_id == order.guild_id,
                        StoreCoupon.code.in_(coupon_codes),
                    )
                    .with_for_update()
                )
            ).all()
        )
        for coupon in coupons:
            coupon.uses = max(0, coupon.uses - 1)

    order.status = "cancelled"
    await write_audit_log(
        session,
        guild_id=order.guild_id,
        actor_discord_id=actor_discord_id,
        action="order.cancelled_manual_pix",
        target_type="order",
        target_id=str(order.id),
        details={"reason": reason[:200]},
    )
    await session.flush()
    return order
