from decimal import Decimal

from app.bot.workflows.feedback_permissions import allowed_feedback_role_ids
from app.db.models import GuildConfig, RankTier


def test_feedback_permissions_include_customer_staff_and_active_tiers() -> None:
    config = GuildConfig(
        guild_id=123,
        customer_role_id=10,
        admin_role_id=11,
        support_role_id=12,
        delivery_role_id=13,
    )
    tiers = [
        RankTier(
            guild_id=123,
            name="Bronze",
            min_spend=Decimal("100.00"),
            role_id=20,
            active=True,
        ),
        RankTier(
            guild_id=123,
            name="Antiga",
            min_spend=Decimal("50.00"),
            role_id=21,
            active=False,
        ),
    ]

    assert allowed_feedback_role_ids(config, tiers) == {10, 11, 12, 13, 20}


def test_feedback_permissions_ignore_missing_roles() -> None:
    config = GuildConfig(guild_id=123)
    assert allowed_feedback_role_ids(config, []) == set()
