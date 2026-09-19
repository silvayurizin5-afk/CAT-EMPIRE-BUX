from decimal import Decimal

import pytest

from app.services.calculator import (
    CalculationKind,
    apply_discount,
    parse_calculation_message,
    robux_from_brl,
    robux_quote,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("R$ 10,80", Decimal("10.80")),
        ("25 reais", Decimal("25.00")),
        ("200 real", Decimal("200.00")),
    ],
)
def test_parse_explicit_money(message: str, expected: Decimal) -> None:
    request = parse_calculation_message(message)
    assert request is not None
    assert request.kind is CalculationKind.MONEY
    assert request.amount == expected


@pytest.mark.parametrize("message", ["380", "380 Robux", "Robux 380", "quero 380 robux"])
def test_parse_robux(message: str) -> None:
    request = parse_calculation_message(message)
    assert request is not None
    assert request.kind is CalculationKind.ROBUX
    assert request.amount == 380


def test_coupon_is_read_only_inside_double_quotes() -> None:
    request = parse_calculation_message('200 Robux "2026"')
    assert request is not None
    assert request.kind is CalculationKind.ROBUX
    assert request.amount == 200
    assert request.coupon_code == "2026"


@pytest.mark.parametrize(
    "message",
    [
        "",
        "0",
        "0 robux",
        "10,5 robux",
        "10,80",
        "7,5 créditos",
        "quanto custa isso?",
    ],
)
def test_ignore_invalid_or_legacy_credit_messages(message: str) -> None:
    assert parse_calculation_message(message) is None


def test_robux_quote_uses_brl_only() -> None:
    quote = robux_quote(500)
    assert quote.gamepass_brl == Decimal("14.50")
    assert quote.via_plus_brl == Decimal("14.50")
    assert quote.covering_fee_brl == Decimal("20.71")


def test_money_converts_to_robux_using_confirmed_base_price() -> None:
    assert robux_from_brl(Decimal("80")) == 2758


def test_discount_applies_to_final_brl_price() -> None:
    assert apply_discount(Decimal("10"), Decimal("10")) == Decimal("9.00")


def test_via_plus_uses_same_normal_rate_as_gamepass() -> None:
    quote = robux_quote(200)
    assert quote.gamepass_brl == Decimal("5.80")
    assert quote.via_plus_brl == Decimal("5.80")
