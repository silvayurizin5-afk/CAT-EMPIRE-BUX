import asyncio
import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import AuditLog, CreditTopUp, User, WalletTransaction
from app.db.payment_models import TopUpNotification
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
async def test_orders_api_confirmation_is_idempotent_under_concurrency() -> None:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    provider_order_id = f"ORD{uuid4().hex.upper()}"

    async with sessions() as session, session.begin():
        user = await get_or_create_user(session, _discord_id())
        user_id = user.id
        topup = CreditTopUp(
            user_id=user_id,
            guild_id=123,
            amount_brl=Decimal("10.80"),
            credits_amount=Decimal("10.80"),
            status="pending",
            provider_preference_id=provider_order_id,
        )
        session.add(topup)
        await session.flush()
        topup_id = topup.id

    provider_order = {
        "id": provider_order_id,
        "external_reference": f"topup:{topup_id}",
        "status": "processed",
        "status_detail": "accredited",
        "currency": "BRL",
        "total_amount": "10.80",
        "total_paid_amount": "10.80",
    }

    async def confirm() -> str:
        async with sessions() as session, session.begin():
            approved = await process_order_update(session, order=provider_order)
            assert approved is not None
            return approved.status

    try:
        statuses = await asyncio.gather(confirm(), confirm())
        assert statuses == ["approved", "approved"]

        async with sessions() as session:
            assert await get_balance(session, user_id) == Decimal("10.80")
            tx_count = await session.scalar(
                select(func.count(WalletTransaction.id)).where(
                    WalletTransaction.reference == f"mercado_pago:order:{provider_order_id}"
                )
            )
            notification_count = await session.scalar(
                select(func.count(TopUpNotification.id)).where(
                    TopUpNotification.topup_id == topup_id
                )
            )
            audit_count = await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.action == "topup.approved",
                    AuditLog.target_type == "topup",
                    AuditLog.target_id == str(topup_id),
                )
            )
            assert tx_count == 1
            assert notification_count == 1
            assert audit_count == 1
    finally:
        async with sessions() as session, session.begin():
            await session.execute(delete(AuditLog).where(AuditLog.target_id == str(topup_id)))
            await session.execute(delete(CreditTopUp).where(CreditTopUp.id == topup_id))
            user = await session.get(User, user_id)
            if user is not None:
                await session.delete(user)
        await engine.dispose()
