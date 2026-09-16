from decimal import Decimal

import pytest

from app.core.money import money
from app.services.catalog import DEFAULT_ROBUX_PRICE_PER_ROBUX


@pytest.mark.parametrize(
    ("robux", "expected"),
    [
        (100, Decimal("2.90")),
        (1_000, Decimal("29.00")),
        (2_000, Decimal("58.00")),
        (5_000, Decimal("145.00")),
        (10_000, Decimal("290.00")),
    ],
)
def test_default_robux_price_scales_from_290_per_100(
    robux: int, expected: Decimal
) -> None:
    assert money(Decimal(robux) * DEFAULT_ROBUX_PRICE_PER_ROBUX) == expected
