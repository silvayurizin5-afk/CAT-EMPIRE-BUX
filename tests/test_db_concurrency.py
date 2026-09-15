import asyncio
import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import CreditTopUp, Order, OrderItem, RobuxRate, User, WalletTransaction
from app.db.payment_models import TopUpNotification
from app.services.orders import create_robux_order, pay_order_with_credits, refund_order
from app.services.topups import process_approved_payment
from app.services.users import get_or_create_user
from app.services.wallets import InsufficientCreditsError, apply_wallet_transaction, get_balance

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="integration database tests are disabled",
)


def _discord_id() -> int:
    return 10_000_000_000_000_000 + (uuid4().int % 8_000_000_000_000_000)


def _database():
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, sessions


async def _delete_user(sessions, user_id: int) -> None:
    async with sessions() as session, session.begin():
        await session.execute(delete(CreditTopUp).where(CreditTopUp.user_id == user_id))
        user = await session.get(User, user_id)
        if user is not None:
            await session.delete(user)


@pytest.mark.asyncio
async def test_wallet_prevents_concurrent_overspend() -> None:
    engine, sessions = _database()
    reference_prefix = f"test:{uuid4()}"
    async with sessions() as session, session.begin():
        user = await get_or_create_user(session, _discord_id())
        user_id = user.id
        await apply_wallet_transaction(
            session,
            user_id=user_id,
            amount=Decimal("100.00"),
            kind="test_credit",
            reference=f"{reference_prefix}:credit",
        )

    async def debit(suffix: str):
        async with sessions() as session, session.begin():
            return await apply_wallet_transaction(
                session,
                user_id=user_id,
                amount=Decimal("-80.00"),
                kind="test_debit",
                reference=f"{reference_prefix}:{suffix}",
            )

    try:
        results = await asyncio.gather(debit("a"), debit("b"), return_exceptions=True)
        failures = [item for item in results if isinstance(item, InsufficientCreditsError)]
        successes = [item for item in results if isinstance(item, WalletTransaction)]
        assert len(successes) == 1
        assert len(failures) == 1

        async with sessions() as session:
            assert await get_balance(session, user_id) == Decimal("20.00")
    finally:
        await _delete_user(sessions, user_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_wallet_reference_is_idempotent_across_sessions() -> None:
    engine, sessions = _database()
    shared_reference = f"test:{uuid4()}:same"
    async with sessions() as session, session.begin():
        user = await get_or_create_user(session, _discord_id())
        user_id = user.id
        await apply_wallet_transaction(
            session,
            user_id=user_id,
            amount=Decimal("25.00"),
            kind="test_credit",
            reference=f"{shared_reference}:credit",
        )

    async def debit_same_reference():
        async with sessions() as session, session.begin():
            tx = await apply_wallet_transaction(
                session,
                user_id=user_id,
                amount=Decimal("-5.00"),
                kind="test_debit",
                reference=shared_reference,
            )
            return tx.id

    try:
        tx_ids = await asyncio.gather(debit_same_reference(), debit_same_reference())
        assert tx_ids[0] == tx_ids[1]
        async with sessions() as session:
            assert await get_balance(session, user_id) == Decimal("20.00")
            count = await session.scalar(
                select(func.count(WalletTransaction.id)).where(
                    WalletTransaction.reference == shared_reference
                )
            )
            assert count == 1
    finally:
        await _delete_user(sessions, user_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_payment_confirmation_is_idempotent_under_concurrency() -> None:
    engine, sessions = _database()
    payment_id = f"payment-{uuid4()}"
    async with sessions() as session, session.begin():
        user = await get_or_create_user(session, _discord_id())
        user_id = user.id
        topup = CreditTopUp(
            user_id=user_id,
            guild_id=123,
            amount_brl=Decimal("10.80"),
            credits_amount=Decimal("10.80"),
            status="pending",
        )
        session.add(topup)
        await session.flush()
        topup_id = topup.id

    payment = {
        "id": payment_id,
        "external_reference": f"topup:{topup_id}",
        "status": "approved",
        "currency_id": "BRL",
        "transaction_amount": "10.80",
    }

    async def confirm_payment():
        async with sessions() as session, session.begin():
            approved = await process_approved_payment(session, payment=payment)
            assert approved is not None
            return approved.status

    try:
        statuses = await asyncio.gather(confirm_payment(), confirm_payment())
        assert statuses == ["approved", "approved"]
        async with sessions() as session:
            assert await get_balance(session, user_id) == Decimal("10.80")
            tx_count = await session.scalar(
                select(func.count(WalletTransaction.id)).where(
                    WalletTransaction.reference == f"mercado_pago:payment:{payment_id}"
                )
            )
            notification_count = await session.scalar(
                select(func.count(TopUpNotification.id)).where(
                    TopUpNotification.topup_id == topup_id
                )
            )
            assert tx_count == 1
            assert notification_count == 1
    finally:
        await _delete_user(sessions, user_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_order_payment_and_refund_are_idempotent_under_concurrency() -> None:
    engine, sessions = _database()
    seed_reference = f"test:{uuid4()}:seed"
    async with sessions() as session, session.begin():
        user = await get_or_create_user(session, _discord_id())
        user_id = user.id
        await apply_wallet_transaction(
            session,
            user_id=user_id,
            amount=Decimal("100.00"),
            kind="test_credit",
            reference=seed_reference,
        )
        order = Order(
            guild_id=123,
            user_id=user_id,
            status="pending",
            total_credits=Decimal("30.00"),
        )
        session.add(order)
        await session.flush()
        order_id = order.id

    async def pay():
        async with sessions() as session, session.begin():
            paid = await pay_order_with_credits(session, order_id=order_id)
            return paid.status

    async def refund():
        async with sessions() as session, session.begin():
            refunded = await refund_order(session, order_id=order_id, reason="teste concorrente")
            return refunded.status

    try:
        assert await asyncio.gather(pay(), pay()) == ["paid", "paid"]
        async with sessions() as session:
            assert await get_balance(session, user_id) == Decimal("70.00")
            user = await session.get(User, user_id)
            assert user is not None
            assert user.total_spent == Decimal("30.00")
            purchase_count = await session.scalar(
                select(func.count(WalletTransaction.id)).where(
                    WalletTransaction.reference == f"order:{order_id}:purchase"
                )
            )
            assert purchase_count == 1

        assert await asyncio.gather(refund(), refund()) == ["refunded", "refunded"]
        async with sessions() as session:
            assert await get_balance(session, user_id) == Decimal("100.00")
            user = await session.get(User, user_id)
            assert user is not None
            assert user.total_spent == Decimal("0.00")
            refund_count = await session.scalar(
                select(func.count(WalletTransaction.id)).where(
                    WalletTransaction.reference == f"order:{order_id}:refund"
                )
            )
            assert refund_count == 1
    finally:
        async with sessions() as session, session.begin():
            await session.execute(delete(Order).where(Order.id == order_id))
        await _delete_user(sessions, user_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_robux_order_uses_current_locked_rate_not_stale_object() -> None:
    engine, sessions = _database()
    guild_id = 987654321
    rate_code = f"test-{uuid4().hex[:12]}"
    async with sessions() as session, session.begin():
        user = await get_or_create_user(session, _discord_id())
        user_id = user.id
        rate = RobuxRate(
            guild_id=guild_id,
            code=rate_code,
            label="Teste Robux",
            price_per_robux=Decimal("0.050000"),
            delivery_label="Teste",
            active=True,
        )
        session.add(rate)
        await session.flush()
        rate_id = rate.id
        stale_rate = rate

    async with sessions() as session, session.begin():
        await session.execute(
            update(RobuxRate)
            .where(RobuxRate.id == rate_id)
            .values(price_per_robux=Decimal("0.060000"), label="Teste atualizado")
        )

    order_id = None
    try:
        async with sessions() as session, session.begin():
            order = await create_robux_order(
                session,
                guild_id=guild_id,
                user_id=user_id,
                rate=stale_rate,
                robux=100,
            )
            order_id = order.id

        async with sessions() as session:
            order = await session.get(Order, order_id)
            item = await session.scalar(select(OrderItem).where(OrderItem.order_id == order_id))
            assert order is not None
            assert item is not None
            assert order.total_credits == Decimal("6.00")
            assert item.name_snapshot == "Teste atualizado • 100 Robux"
            assert item.metadata_json["price_per_robux"] == "0.060000"
    finally:
        async with sessions() as session, session.begin():
            if order_id is not None:
                await session.execute(delete(OrderItem).where(OrderItem.order_id == order_id))
                await session.execute(delete(Order).where(Order.id == order_id))
            await session.execute(delete(RobuxRate).where(RobuxRate.id == rate_id))
        await _delete_user(sessions, user_id)
        await engine.dispose()
