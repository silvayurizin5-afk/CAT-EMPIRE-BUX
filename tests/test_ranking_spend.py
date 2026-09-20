import os
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Order, OrderItem, UserEconomyAdjustment
from app.services import profiles
from app.services.users import get_or_create_user


@pytest.mark.parametrize(
    ("price_brl", "quantity", "expected"),
    [
        ("0.00", 1, 0),
        ("2.90", 1, 100),
        ("5.80", 1, 200),
        ("22.00", 1, 758),
        ("2.90", 3, 300),
        ("-1.00", 1, 0),
    ],
)
def test_gamepass_brl_equivalent_uses_fixed_store_rate(
    price_brl: str,
    quantity: int,
    expected: int,
) -> None:
    assert (
        profiles._robux_equivalent_from_brl(
            Decimal(price_brl),
            quantity=quantity,
        )
        == expected
    )


async def test_leaderboard_uses_product_robux_and_ignores_legacy_adjustment(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        profiles,
        "_adjusted_spend_rows",
        AsyncMock(
            return_value=[
                {
                    "user_id": 1,
                    "discord_user_id": 123,
                    "base_spent": Decimal("100.00"),
                    "spent_adjustment": Decimal("0.00"),
                    "base_orders": 2,
                    "orders_adjustment": 0,
                    "robux_adjustment": 99999,
                }
            ]
        ),
    )
    monkeypatch.setattr(
        profiles,
        "_robux_totals_for_users",
        AsyncMock(return_value=({1: 1250}, [])),
    )

    entries = await profiles.list_leaderboard(None, guild_id=1)

    assert entries[0].robux_purchased == 1250
    assert entries[0].total_spent == Decimal("100.00")


@pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="integration database tests are disabled",
)
async def test_ranking_counts_robux_gamepass_and_ignores_items_accounts() -> None:
    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    guild_id = 10_000_000_000_000_000 + uuid4().int % 8_000_000_000_000_000

    try:
        async with sessions() as session:
            async with session.begin():
                user = await get_or_create_user(session, guild_id)

                orders = [
                    (
                        "paid",
                        Decimal("10.00"),
                        "Item comum",
                        Decimal("10.00"),
                        {"product_type": "item", "robux_amount": 99999},
                    ),
                    (
                        "processing",
                        Decimal("5.80"),
                        "Game Pass VIP",
                        Decimal("5.80"),
                        {"product_type": "gamepass", "robux_amount": 99999},
                    ),
                    (
                        "delivered",
                        Decimal("29.00"),
                        "Robux flexível • 1000 Robux",
                        Decimal("29.00"),
                        {
                            "product_type": "robux",
                            "robux_amount": 1000,
                            "price_per_robux": "0.029",
                        },
                    ),
                    (
                        "delivered",
                        Decimal("50.00"),
                        "Conta Roblox",
                        Decimal("50.00"),
                        {"product_type": "account", "robux_amount": 99999},
                    ),
                    (
                        "pending",
                        Decimal("290.00"),
                        "Robux pendente • 10000 Robux",
                        Decimal("290.00"),
                        {"product_type": "robux", "robux_amount": 10000},
                    ),
                ]

                for status, total, name, unit_price, metadata in orders:
                    order = Order(
                        guild_id=guild_id,
                        user_id=user.id,
                        status=status,
                        total_credits=total,
                    )
                    session.add(order)
                    await session.flush()
                    session.add(
                        OrderItem(
                            order_id=order.id,
                            name_snapshot=name,
                            quantity=1,
                            unit_price=unit_price,
                            metadata_json=metadata,
                        )
                    )

                adjustment = UserEconomyAdjustment(
                    guild_id=guild_id,
                    user_id=user.id,
                    spent_adjustment=0,
                    orders_adjustment=0,
                    robux_adjustment=50000,
                )
                session.add(adjustment)
                await session.flush()

                profile = await profiles.get_customer_profile(
                    session,
                    guild_id=guild_id,
                    discord_user_id=user.discord_user_id,
                )

                # R$: item 10 + gamepass 5,80 + robux 29 + conta 50.
                assert profile.total_spent == Decimal("94.80")
                assert profile.completed_orders == 4
                # Robux: Game Pass 5,80 = 200 + compra direta de 1000.
                assert profile.robux_purchased == 1200

                leaderboard = await profiles.list_leaderboard(
                    session,
                    guild_id=guild_id,
                )
                assert leaderboard[0].robux_purchased == 1200

                # Ajustar R$/compras manualmente não inventa Robux.
                profile = await profiles.set_user_economy_target(
                    session,
                    guild_id=guild_id,
                    discord_user_id=user.discord_user_id,
                    total_spent=Decimal("100.00"),
                    completed_orders=5,
                )
                assert profile.total_spent == Decimal("100.00")
                assert profile.completed_orders == 5
                assert profile.robux_purchased == 1200
                assert adjustment.robux_adjustment == 0

                # Remover ajustes volta ao histórico real sem mudar o total de Robux.
                profile = await profiles.clear_user_economy_adjustment(
                    session,
                    guild_id=guild_id,
                    discord_user_id=user.discord_user_id,
                )
                assert profile.total_spent == Decimal("94.80")
                assert profile.completed_orders == 4
                assert profile.robux_purchased == 1200

                await session.rollback()
    finally:
        await engine.dispose()
