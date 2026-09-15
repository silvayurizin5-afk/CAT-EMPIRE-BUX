import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import AuditLog, CreditTopUp, Order, User
from app.db.risk_models import CommerceLock
from app.services.commerce_locks import (
    CommerceLockedError,
    resolve_commerce_lock,
)
from app.services.orders import pay_order_with_credits
from app.services.topups import process_order_update
from app.services.users import get_or_create_user
from app.services.wallets import get_balance

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="integration database tests are disabled",
)


def _discord_id() -> int:
    return 10_000_000_000_000_000 + (uuid4().int % 8_000_000_000_000_000)


@pytest.mark.asyncio
async def test_refunded_credited_topup_locks_purchases_without_auto_debit() -> None:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    guild_id = 9_876_543_210
    provider_order_id = f"ORD{uuid4().hex.upper()}"
    order_id = None

    async with sessions() as session, session.begin():
        user = await get_or_create_user(session, _discord_id())
        user_id = user.id
        topup = CreditTopUp(
            user_id=user_id,
            guild_id=guild_id,
            amount_brl=Decimal("10.80"),
            credits_amount=Decimal("10.80"),
            status="pending",
            provider_preference_id=provider_order_id,
        )
        session.add(topup)
        await session.flush()
        topup_id = topup.id

    approved_order = {
        "id": provider_order_id,
        "external_reference": f"topup:{topup_id}",
        "status": "processed",
        "status_detail": "accredited",
        "currency": "BRL",
        "total_amount": "10.80",
        "total_paid_amount": "10.80",
    }
    refunded_order = {
        **approved_order,
        "status": "refunded",
        "status_detail": "refunded",
    }

    try:
        async with sessions() as session, session.begin():
            approved = await process_order_update(session, order=approved_order)
            assert approved is not None
            assert approved.status == "approved"

        async with sessions() as session:
            assert await get_balance(session, user_id) == Decimal("10.80")

        async with sessions() as session, session.begin():
            refunded = await process_order_update(session, order=refunded_order)
            assert refunded is not None
            assert refunded.status == "refunded"

        async with sessions() as session:
            assert await get_balance(session, user_id) == Decimal("10.80")
            lock = await session.scalar(
                select(CommerceLock).where(
                    CommerceLock.guild_id == guild_id,
                    CommerceLock.user_id == user_id,
                    CommerceLock.active.is_(True),
                )
            )
            assert lock is not None
            lock_id = lock.id
            incident_count = await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.action == "topup.payment_incident",
                    AuditLog.target_id == str(topup_id),
                )
            )
            assert incident_count == 1

        # Webhook repetido não pode criar outro incidente nem mexer no saldo.
        async with sessions() as session, session.begin():
            await process_order_update(session, order=refunded_order)
        async with sessions() as session:
            assert await get_balance(session, user_id) == Decimal("10.80")
            incident_count = await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.action == "topup.payment_incident",
                    AuditLog.target_id == str(topup_id),
                )
            )
            assert incident_count == 1

        async with sessions() as session, session.begin():
            order = Order(
                guild_id=guild_id,
                user_id=user_id,
                status="pending",
                total_credits=Decimal("5.00"),
            )
            session.add(order)
            await session.flush()
            order_id = order.id

        with pytest.raises(CommerceLockedError, match="temporariamente bloqueada"):
            async with sessions() as session, session.begin():
                await pay_order_with_credits(session, order_id=order_id)

        async with sessions() as session:
            assert await get_balance(session, user_id) == Decimal("10.80")
            order = await session.get(Order, order_id)
            assert order is not None
            assert order.status == "pending"

        async with sessions() as session, session.begin():
            await resolve_commerce_lock(
                session,
                lock_id=lock_id,
                admin_discord_id=999_999_999,
            )
            paid = await pay_order_with_credits(session, order_id=order_id)
            assert paid.status == "paid"

        async with sessions() as session:
            assert await get_balance(session, user_id) == Decimal("5.80")
            lock = await session.get(CommerceLock, lock_id)
            assert lock is not None
            assert lock.active is False
            assert lock.resolved_by_discord_id == 999_999_999
    finally:
        async with sessions() as session, session.begin():
            await session.execute(
                delete(AuditLog).where(
                    AuditLog.target_id.in_([str(topup_id), str(order_id) if order_id else ""])
                )
            )
            if order_id is not None:
                await session.execute(delete(Order).where(Order.id == order_id))
            await session.execute(delete(CreditTopUp).where(CreditTopUp.id == topup_id))
            user = await session.get(User, user_id)
            if user is not None:
                await session.delete(user)
        await engine.dispose()
