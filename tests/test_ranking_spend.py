import os
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Order, OrderItem, RobuxRate, UserEconomyAdjustment
from app.services import profiles
from app.services.users import get_or_create_user


@pytest.mark.parametrize(('spent', 'rate', 'expected'), [
    ('0.00', '0.029', 0), ('0.03', '0.029', 1),
    ('365.50', '0.029', 12603), ('29.00', '0.029', 1000),
    ('29.00', '0.04', 725), ('-1.00', '0.029', 0),
])
def test_convert_accumulated_spend(spent, rate, expected):
    assert profiles._robux_from_total_spent(Decimal(spent), Decimal(rate)) == expected


async def test_leaderboard_uses_money_adjustments_and_ignores_legacy_robux(monkeypatch):
    monkeypatch.setattr(profiles, '_adjusted_spend_rows', AsyncMock(return_value=[{
        'user_id': 1, 'discord_user_id': 123, 'base_spent': Decimal('0.02'),
        'spent_adjustment': Decimal('0.01'), 'base_orders': 2, 'orders_adjustment': 0,
        'robux_adjustment': 99999,
    }]))
    monkeypatch.setattr(profiles, '_ranking_robux_rate', AsyncMock(return_value=Decimal('0.029')))
    entries = await profiles.list_leaderboard(None, guild_id=1)
    assert entries[0].robux_purchased == 1
    assert entries[0].total_spent == Decimal('0.03')


@pytest.mark.skipif(os.getenv('RUN_DB_TESTS') != '1', reason='integration database tests are disabled')
async def test_ranking_profile_rates_statuses_and_economy_adjustments():
    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    guild_id = 10_000_000_000_000_000 + uuid4().int % 8_000_000_000_000_000
    try:
        async with sessions() as session:
            async with session.begin():
                user = await get_or_create_user(session, guild_id)
                rates = [RobuxRate(guild_id=guild_id, code=code, label=code,
                                   price_per_robux=Decimal(price), active=active, sort_order=sort)
                         for code, price, active, sort in [
                             ('other', '0.05', True, -10), ('padrao', '0.029', True, 0),
                             ('inactive', '0.001', False, -20)]]
                session.add_all(rates)
                for status, amount in [('paid', '0.01'), ('processing', '0.01'),
                                       ('delivered', '0.01'), ('pending', '99'),
                                       ('cancelled', '99'), ('refunded', '99')]:
                    order = Order(guild_id=guild_id, user_id=user.id, status=status,
                                  total_credits=Decimal(amount))
                    session.add(order)
                    await session.flush()
                    session.add(OrderItem(order_id=order.id, name_snapshot='Item comum',
                                          quantity=1, unit_price=Decimal(amount),
                                          metadata_json={'product_type': 'item', 'robux_amount': 99999}))
                session.add(Order(guild_id=guild_id + 1, user_id=user.id,
                                  status='delivered', total_credits=Decimal('99')))
                adjustment = UserEconomyAdjustment(guild_id=guild_id, user_id=user.id,
                                                   spent_adjustment=0, orders_adjustment=0,
                                                   robux_adjustment=50000)
                session.add(adjustment)
                await session.flush()
                async def profile():
                    return await profiles.get_customer_profile(session, guild_id=guild_id,
                                                               discord_user_id=user.discord_user_id)
                result = await profile()
                assert (result.total_spent, result.robux_purchased, result.completed_orders) == (Decimal('0.03'), 1, 3)
                assert result.recent_products == ('Item comum',)
                assert (await profiles.list_leaderboard(session, guild_id=guild_id))[0].robux_purchased == 1
                result = await profiles.set_user_economy_target(
                    session, guild_id=guild_id, discord_user_id=user.discord_user_id,
                    total_spent=Decimal('0.09'), completed_orders=3)
                assert result.robux_purchased == 3
                assert adjustment.robux_adjustment == 0
                rates[1].active = False
                await session.flush()
                assert (await profile()).robux_purchased == 1  # first active: 0.05
                rates[0].active = False
                await session.flush()
                assert (await profile()).robux_purchased == 3  # fallback: 0.029
                result = await profiles.set_user_economy_target(
                    session, guild_id=guild_id, discord_user_id=user.discord_user_id,
                    total_spent=Decimal('0'), completed_orders=0)
                assert (result.total_spent, result.robux_purchased) == (Decimal('0'), 0)
                result = await profiles.clear_user_economy_adjustment(
                    session, guild_id=guild_id, discord_user_id=user.discord_user_id)
                assert (result.total_spent, result.robux_purchased) == (Decimal('0.03'), 1)
                await session.rollback()
    finally:
        await engine.dispose()
