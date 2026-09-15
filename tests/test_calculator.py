from decimal import Decimal

import pytest

from app.services.calculator import CalculationKind, parse_calculation_message


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("10,80", Decimal("10.80")),
        ("R$ 10.80", Decimal("10.80")),
        ("25 reais", Decimal("25")),
        ("7,5 créditos", Decimal("7.5")),
    ],
)
def test_parse_money_only(message: str, expected: Decimal) -> None:
    request = parse_calculation_message(message)
    assert request is not None
    assert request.kind is CalculationKind.CREDITS_TO_ROBUX
    assert request.amount == expected


@pytest.mark.parametrize("message", ["380 Robux", "Robux 380", "quero 380 robux"])
def test_parse_robux(message: str) -> None:
    request = parse_calculation_message(message)
    assert request is not None
    assert request.kind is CalculationKind.ROBUX_TO_CREDITS
    assert request.amount == 380


@pytest.mark.parametrize(
    "message",
    [
        "",
        "0",
        "0 robux",
        "10,5 robux",
        "quanto custa isso?",
        "tenho 10 reais e quero saber",
    ],
)
def test_ignore_invalid_calculator_messages(message: str) -> None:
    assert parse_calculation_message(message) is None
