import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import AuditLog, Order, OrderItem, Product, User
from app.db.order_payment_models import OrderPayment
from app.services.stripe_orders import process_stripe_order_incident_event
from app.services.users import get_or_create_user

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="integration database tests are disabled",
)


def _discord_id() -> int:
    return 10_000_000_000_000_000 + (uuid4().int % 8_000_000_000_000_000)


@pytest.mark.asyncio
async def test_full_stripe_refund_updates_order_without_restocking_delivered_item() -> None:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    guild_id = 888001
    payment_intent_id = f"pi_{uuid4().hex}"

    async with sessions() as session, session.begin():
        user = await get_or_create_user(session, _discord_id())
        user_id = user.id
        user.total_spent = Decimal("25.00")
        product = Product(
            guild_id=guild_id,
            name="Produto Stripe",
            slug=f"stripe-{uuid4().hex[:8]}",
            product_type="digital",
            price_credits=Decimal("25.00"),
            stock_quantity=4,
            active=True,
        )
        session.add(product)
        await session.flush()
        product_id = product.id

        order = Order(
            guild_id=guild_id,
            user_id=user_id,
            status="delivered",
            total_credits=Decimal("25.00"),
        )
        session.add(order)
        await session.flush()
        order_id = order.id
        session.add(
            OrderItem(
                order_id=order_id,
                product_id=product_id,
                name_snapshot=product.name,
                unit_price=Decimal("25.00"),
                quantity=1,
                metadata_json={},
            )
        )
        session.add(
            OrderPayment(
                order_id=order_id,
                provider="stripe",
                status="paid",
                payment_intent_id=payment_intent_id,
            )
        )

    resource = {
        "id": f"ch_{uuid4().hex}",
        "payment_intent": payment_intent_id,
        "amount": 2500,
        "amount_refunded": 2500,
        "metadata": {},
    }

    try:
        async with sessions() as session, session.begin():
            processed = await process_stripe_order_incident_event(
                session,
                event_type="charge.refunded",
                resource=resource,
            )
            assert processed is not None
            assert processed.status == "refunded"

        async with sessions() as session:
            order = await session.get(Order, order_id)
            product = await session.get(Product, product_id)
            user = await session.get(User, user_id)
            payment = await session.scalar(
                select(OrderPayment).where(OrderPayment.order_id == order_id)
            )
            assert order is not None and order.status == "refunded"
            assert product is not None and product.stock_quantity == 4
            assert user is not None and user.total_spent == Decimal("0.00")
            assert payment is not None and payment.status == "refunded"

        async with sessions() as session, session.begin():
            await process_stripe_order_incident_event(
                session,
                event_type="charge.refunded",
                resource=resource,
            )

        async with sessions() as session:
            audits = list(
                (
                    await session.scalars(
                        select(AuditLog).where(
                            AuditLog.action == "order.payment_incident",
                            AuditLog.target_id == str(order_id),
                        )
                    )
                ).all()
            )
            assert len(audits) == 1
    finally:
        async with sessions() as session, session.begin():
            await session.execute(
                delete(AuditLog).where(AuditLog.target_id == str(order_id))
            )
            await session.execute(delete(OrderItem).where(OrderItem.order_id == order_id))
            await session.execute(delete(Order).where(Order.id == order_id))
            await session.execute(delete(Product).where(Product.id == product_id))
            user = await session.get(User, user_id)
            if user is not None:
                await session.delete(user)
        await engine.dispose()
