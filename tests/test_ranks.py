from decimal import Decimal

from app.db.models import RankTier
from app.services.ranks import choose_rank_tier


def tier(name: str, min_spend: str, role_id: int) -> RankTier:
    item = RankTier(
        guild_id=1,
        name=name,
        min_spend=Decimal(min_spend),
        role_id=role_id,
        dm_message="",
        active=True,
    )
    item.id = role_id
    return item


def test_choose_highest_eligible_rank() -> None:
    tiers = [tier("Bronze", "10", 1), tier("Prata", "50", 2), tier("Ouro", "100", 3)]
    assert choose_rank_tier(tiers, Decimal("80")).name == "Prata"


def test_no_rank_when_below_first_threshold() -> None:
    tiers = [tier("Bronze", "10", 1)]
    assert choose_rank_tier(tiers, Decimal("5")) is None
