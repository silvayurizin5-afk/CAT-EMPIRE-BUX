from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.db.models import Order, OrderItem, Product, User
from app.db.order_payment_models import OrderPayment
from app.db.store_models import StoreCoupon
from app.integrations.stripe_gateway import StripeGateway
from app.services.audit import write_audit_log


class StripeOrderValidationError(ValueError):
    pass


async def create_order_checkout(
    session: AsyncSession,
    *,
    order: Order,
    product_name: str,
    stripe_gateway: StripeGateway,
) -> OrderPayment:
    if order.status != "pending":
        raise StripeOrderValidationError("Pedido não está aguardando pagamento")

    payment = await session.scalar(
        select(OrderPayment).where(OrderPayment.order_id == order.id).with_for_update()
    )
    if payment is not None and payment.checkout_url and payment.status in {"pending", "creating"}:
        return payment
    if payment is None:
        payment = OrderPayment(order_id=order.id, provider="stripe", status="creating")
        session.add(payment)
        await session.flush()

    checkout = await stripe_gateway.create_order_checkout(
        order_id=order.id,
        amount_brl=money(order.total_credits),
        product_name=product_name,
    )
    payment.checkout_session_id = checkout.session_id
    payment.checkout_url = checkout.checkout_url
    payment.status = "pending"
    await session.flush()
    return payment


def _extract_order_id(resource: dict) -> UUID | None:
    metadata = resource.get("metadata") or {}
    candidate = metadata.get("order_id") if isinstance(metadata, dict) else None
    candidate = candidate or resource.get("client_reference_id")
    if not candidate:
        return None
    try:
        return UUID(str(candidate))
    except ValueError:
        return None


def _extract_payment_intent_id(resource: dict) -> str | None:
    value = resource.get("payment_intent")
    if isinstance(value, dict):
        value = value.get("id")
    text = str(value or "").strip()
    return text or None


async def _restore_reserved_resources(session: AsyncSession, order: Order) -> None:
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
        code = str((item.metadata_json or {}).get("coupon_code") or "").strip()
        if code:
            coupon_codes.add(code)

    for code in coupon_codes:
        coupon = await session.scalar(
            select(StoreCoupon)
            .where(StoreCoupon.guild_id == order.guild_id, StoreCoupon.code == code)
            .with_for_update()
        )
        if coupon is not None and coupon.uses > 0:
            coupon.uses -= 1


async def process_stripe_order_checkout_event(
    session: AsyncSession,
    *,
    event_type: str,
    checkout: dict,
) -> Order | None:
    order_id = _extract_order_id(checkout)
    if order_id is None:
        return None

    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    payment = await session.scalar(
        select(OrderPayment).where(OrderPayment.order_id == order_id).with_for_update()
    )
    if order is None or payment is None:
        raise StripeOrderValidationError("Pedido Stripe não encontrado")

    session_id = str(checkout.get("id") or "").strip()
    if not session_id:
        raise StripeOrderValidationError("Checkout Session sem ID")
    if payment.checkout_session_id and payment.checkout_session_id != session_id:
        raise StripeOrderValidationError("Checkout Session não corresponde ao pedido")
    payment.checkout_session_id = session_id

    if event_type in {"checkout.session.expired", "checkout.session.async_payment_failed"}:
        if order.status == "pending":
            await _restore_reserved_resources(session, order)
            order.status = "cancelled"
        payment.status = "expired" if event_type.endswith("expired") else "failed"
        await session.flush()
        return order

    currency = str(checkout.get("currency") or "").lower()
    if currency != "brl":
        raise StripeOrderValidationError("Moeda inesperada no pagamento")
    try:
        paid_amount = money(Decimal(int(checkout.get("amount_total"))) / Decimal("100"))
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise StripeOrderValidationError("Valor inválido no Checkout Stripe") from exc
    if paid_amount != money(order.total_credits):
        raise StripeOrderValidationError("Valor pago não corresponde ao pedido")

    if str(checkout.get("payment_status") or "").lower() != "paid":
        payment.status = "pending"
        await session.flush()
        return order

    if order.status in {"paid", "processing", "delivered"}:
        payment.status = "paid"
        await session.flush()
        return order
    if order.status != "pending":
        raise StripeOrderValidationError("Pedido não está mais aguardando pagamento")

    user = await session.scalar(select(User).where(User.id == order.user_id).with_for_update())
    if user is None:
        raise StripeOrderValidationError("Cliente do pedido não encontrado")

    payment_intent_id = _extract_payment_intent_id(checkout)
    if payment_intent_id:
        duplicate = await session.scalar(
            select(OrderPayment.id).where(
                OrderPayment.payment_intent_id == payment_intent_id,
                OrderPayment.id != payment.id,
            )
        )
        if duplicate is not None:
            raise StripeOrderValidationError("PaymentIntent já usado em outro pedido")
        payment.payment_intent_id = payment_intent_id

    order.status = "paid"
    order.paid_at = datetime.now(UTC)
    user.total_spent = money(user.total_spent + order.total_credits)
    payment.status = "paid"
    await write_audit_log(
        session,
        guild_id=order.guild_id,
        actor_discord_id=user.discord_user_id,
        action="order.payment.approved",
        target_type="order",
        target_id=str(order.id),
        details={
            "amount_brl": str(money(order.total_credits)),
            "provider": "stripe",
            "checkout_session_id": session_id,
            "payment_intent_id": payment_intent_id,
        },
    )
    await session.flush()
    return order


async def get_order_payment(session: AsyncSession, *, order_id: UUID) -> OrderPayment | None:
    return await session.scalar(select(OrderPayment).where(OrderPayment.order_id == order_id))
