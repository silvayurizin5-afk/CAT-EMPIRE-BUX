import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.audit_models import AuditDelivery
from app.db.models import AuditLog, GuildConfig
from app.services.audit import (
    claim_pending_audit_deliveries,
    mark_audit_delivery_published,
    write_audit_log,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="integration database tests are disabled",
)


def _guild_id() -> int:
    return 1_000_000_000_000_000 + (uuid4().int % 7_000_000_000_000_000)


@pytest.mark.asyncio
async def test_audit_log_creates_and_publishes_persistent_outbox() -> None:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    guild_id = _guild_id()
    audit_id: int | None = None

    try:
        async with sessions() as session, session.begin():
            session.add(GuildConfig(guild_id=guild_id, logs_channel_id=987654321))
            log = await write_audit_log(
                session,
                guild_id=guild_id,
                actor_discord_id=123456789,
                action="test.audit",
                target_type="test",
                target_id="abc",
                details={"ok": True},
            )
            audit_id = log.id

        async with sessions() as session:
            delivery = await session.scalar(
                select(AuditDelivery).where(AuditDelivery.audit_log_id == audit_id)
            )
            assert delivery is not None
            assert delivery.published_at is None

        async with sessions() as session, session.begin():
            pending = await claim_pending_audit_deliveries(session)
        matching = [item for item in pending if item.audit_log_id == audit_id]
        assert len(matching) == 1
        assert matching[0].logs_channel_id == 987654321
        assert matching[0].action == "test.audit"

        async with sessions() as session, session.begin():
            await mark_audit_delivery_published(
                session,
                delivery_id=matching[0].delivery_id,
            )

        async with sessions() as session:
            delivery = await session.scalar(
                select(AuditDelivery).where(AuditDelivery.audit_log_id == audit_id)
            )
            assert delivery is not None
            assert delivery.published_at is not None
            assert delivery.claimed_at is None
    finally:
        async with sessions() as session, session.begin():
            if audit_id is not None:
                await session.execute(delete(AuditLog).where(AuditLog.id == audit_id))
            await session.execute(delete(GuildConfig).where(GuildConfig.guild_id == guild_id))
        await engine.dispose()
