import asyncio
import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import CreditTopUp, User, WalletTransaction
from app.services.commerce_locks import get_active_commerce_lock
from app.services.stripe_topups import (
    process_stripe_checkout_event,
    process_stripe_incident_event,
)
from app.services.users import get_or_create_user
from app.services.wallets import get_balance


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="integration database tests are disabled",
)


async def _seed_stripe_topup(sessions, *, amount: Decimal = Decimal("10.80")):
    discord_user_id = 10_000_000_000_000_000 + (uuid4().int % 8_000_000_000_000_000)
    checkout_id = f"cs_test_{uuid4().hex}"
    payment_intent_id = f"pi_test_{uuid4().hex}"
    guild_id = 123456 + (uuid4().int % 100000)
    async with sessions() as session, session.begin():
        user = await get_or_create_user(session, discord_user_id)
        topup = CreditTopUp(
            user_id=user.id,
            guild_id=guild_id,
            amount_brl=amount,
            credits_amount=amount,
            provider="stripe",
            status="pending",
            provider_preference_id=checkout_id,
        )
        session.add(topup)
        await session.flush()
        return user.id, topup.id, guild_id, checkout_id, payment_intent_id


async def _cleanup(sessions, *, user_id: int, topup_id) -> None:
    async with sessions() as session, session.begin():
        await session.execute(delete(CreditTopUp).where(CreditTopUp.id == topup_id))
        user = await session.get(User, user_id)
        if user is not None:
            await session.delete(user)


@pytest.mark.asyncio
async def test_stripe_checkout_confirmation_is_idempotent_under_concurrency() -> None:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    user_id, topup_id, _, checkout_id, payment_intent_id = await _seed_stripe_topup(sessions)

    checkout = {
        "id": checkout_id,
        "client_reference_id": str(topup_id),
        "metadata": {"topup_id": str(topup_id)},
        "currency": "brl",
        "amount_total": 1080,
        "payment_status": "paid",
        "payment_intent": payment_intent_id,
    }

    async def confirm() -> str:
        async with sessions() as session, session.begin():
            result = await process_stripe_checkout_event(
                session,
                event_type="checkout.session.completed",
                checkout=checkout,
            )
            assert result is not None
            return result.status

    try:
        statuses = await asyncio.gather(confirm(), confirm())
        assert statuses == ["approved", "approved"]
        async with sessions() as session:
            assert await get_balance(session, user_id) == Decimal("10.80")
            count = await session.scalar(
                select(func.count(WalletTransaction.id)).where(
                    WalletTransaction.reference == f"stripe:checkout:{checkout_id}"
                )
            )
            assert count == 1
            stored = await session.get(CreditTopUp, topup_id)
            assert stored is not None
            assert stored.provider_payment_id == payment_intent_id
    finally:
        await _cleanup(sessions, user_id=user_id, topup_id=topup_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_stripe_refund_locks_commerce_without_negative_wallet() -> None:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    user_id, topup_id, guild_id, checkout_id, payment_intent_id = await _seed_stripe_topup(sessions)
    checkout = {
        "id": checkout_id,
        "client_reference_id": str(topup_id),
        "metadata": {"topup_id": str(topup_id)},
        "currency": "brl",
        "amount_total": 1080,
        "payment_status": "paid",
        "payment_intent": payment_intent_id,
    }

    try:
        async with sessions() as session, session.begin():
            await process_stripe_checkout_event(
                session,
                event_type="checkout.session.completed",
                checkout=checkout,
            )

        async with sessions() as session, session.begin():
            result = await process_stripe_incident_event(
                session,
                event_type="charge.refunded",
                resource={
                    "id": f"ch_test_{uuid4().hex}",
                    "payment_intent": payment_intent_id,
                    "amount": 1080,
                    "amount_refunded": 1080,
                },
            )
            assert result is not None
            assert result.status == "refunded"

        async with sessions() as session:
            assert await get_balance(session, user_id) == Decimal("10.80")
            lock = await get_active_commerce_lock(
                session,
                guild_id=guild_id,
                user_id=user_id,
            )
            assert lock is not None
            assert lock.source_topup_id == topup_id
    finally:
        await _cleanup(sessions, user_id=user_id, topup_id=topup_id)
        await engine.dispose()
