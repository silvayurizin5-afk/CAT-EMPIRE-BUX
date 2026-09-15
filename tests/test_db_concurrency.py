import asyncio
import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import CreditTopUp, User, WalletTransaction
from app.db.payment_models import TopUpNotification
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
