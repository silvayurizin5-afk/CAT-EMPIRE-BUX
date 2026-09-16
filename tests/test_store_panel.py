from decimal import Decimal

import pytest

from app.db.store_models import StorePanelConfig
from app.services.store_panel import discounted_total, normalize_coupon_code


def test_discounted_total_uses_money_precision() -> None:
    assert discounted_total(Decimal("29.00"), Decimal("10")) == Decimal("26.10")
    assert discounted_total(Decimal("145.00"), Decimal("15")) == Decimal("123.25")


def test_coupon_code_is_normalized() -> None:
    assert normalize_coupon_code(" next 10 ") == "NEXT10"


def test_invalid_discount_is_rejected() -> None:
    with pytest.raises(ValueError):
        discounted_total(Decimal("10.00"), Decimal("0"))


def test_store_panel_defaults_are_single_embed_friendly() -> None:
    config = StorePanelConfig(guild_id=123, selected_product_ids=[])
    assert config.guild_id == 123
    assert config.selected_product_ids == []
