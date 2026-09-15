from decimal import Decimal

import pytest

from app.core.money import money, require_positive


def test_money_uses_two_decimal_places() -> None:
    assert money("10.805") == Decimal("10.81")


def test_money_rejects_float() -> None:
    with pytest.raises(TypeError):
        money(10.8)  # type: ignore[arg-type]


def test_positive_rejects_zero() -> None:
    with pytest.raises(ValueError):
        require_positive("0")
