from decimal import Decimal

from app.services.calculator import CalculationKind, parse_calculation_message


def test_plain_decimal_is_brl_to_robux() -> None:
    request = parse_calculation_message("10,80")
    assert request is not None
    assert request.kind == CalculationKind.CREDITS_TO_ROBUX
    assert request.amount == Decimal("10.80")


def test_plain_reais_text_is_brl_to_robux() -> None:
    request = parse_calculation_message("R$ 25,50 reais")
    assert request is not None
    assert request.kind == CalculationKind.CREDITS_TO_ROBUX
    assert request.amount == Decimal("25.50")


def test_robux_message_is_robux_to_credits() -> None:
    request = parse_calculation_message("quanto fica 380 Robux?")
    assert request is not None
    assert request.kind == CalculationKind.ROBUX_TO_CREDITS
    assert request.amount == 380


def test_fractional_robux_is_rejected() -> None:
    assert parse_calculation_message("10,5 robux") is None


def test_random_text_is_ignored() -> None:
    assert parse_calculation_message("aceitam pix?") is None
