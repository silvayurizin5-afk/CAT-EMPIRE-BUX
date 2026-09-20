"""PostgreSQL integration: real locking, settings merges and ticket limits."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.ticket_models import SupportTicket, TicketSettings
from app.services.support_tickets import (
    SupportOptions,
    check_open_limit,
    get_support_options,
    save_support_options,
    ticket_lock,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1", reason="PostgreSQL integration disabled"
)


async def test_concurrent_openings_obey_customer_limit():
    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    guild_id = 10**16 + uuid4().int % 10**16
    customer_id = guild_id + 1

    async def create():
        async with sessions() as session, session.begin():
            await ticket_lock(session, "customer", guild_id, customer_id)
            await check_open_limit(
                session, guild_id, customer_id, SupportOptions(cooldown_seconds=0)
            )
            session.add(
                SupportTicket(
                    guild_id=guild_id,
                    customer_id=customer_id,
                    subject="Suporte",
                    description="Teste",
                )
            )

    try:
        results = await asyncio.gather(create(), create(), return_exceptions=True)
        assert sum(isinstance(r, ValueError) for r in results) == 1
        assert sum(r is None for r in results) == 1
        async with sessions() as session:
            tickets = list(
                (
                    await session.scalars(
                        select(SupportTicket).where(SupportTicket.guild_id == guild_id)
                    )
                ).all()
            )
            assert len(tickets) == 1
    finally:
        async with sessions() as session, session.begin():
            await session.execute(delete(SupportTicket).where(SupportTicket.guild_id == guild_id))
        await engine.dispose()


async def test_concurrent_settings_edits_preserve_other_fields():
    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    guild_id = 10**16 + uuid4().int % 10**16

    async def patch(values):
        async with sessions() as session, session.begin():
            await save_support_options(session, guild_id, values)

    try:
        await asyncio.gather(patch({"color": "FF0000"}), patch({"max_open": 3}))
        async with sessions() as session:
            options = await get_support_options(session, guild_id)
        assert options.color == "FF0000"
        assert options.max_open == 3
        assert options.transcript_required
    finally:
        async with sessions() as session, session.begin():
            await session.execute(delete(TicketSettings).where(TicketSettings.guild_id == guild_id))
        await engine.dispose()


async def test_closed_tickets_dont_count_but_cooldown_still_applies():
    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    guild_id = 10**16 + uuid4().int % 10**16
    try:
        async with sessions() as session, session.begin():
            session.add(
                SupportTicket(
                    guild_id=guild_id,
                    customer_id=123,
                    subject="Resolvido",
                    description="Teste",
                    state="closed",
                )
            )
        async with sessions() as session:
            await check_open_limit(session, guild_id, 123, SupportOptions(), reopening=True)
            with pytest.raises(ValueError, match="Aguarde"):
                await check_open_limit(session, guild_id, 123, SupportOptions())
    finally:
        async with sessions() as session, session.begin():
            await session.execute(delete(SupportTicket).where(SupportTicket.guild_id == guild_id))
        await engine.dispose()
