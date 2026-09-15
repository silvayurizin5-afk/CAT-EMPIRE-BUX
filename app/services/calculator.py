import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class CalculationKind(StrEnum):
    CREDITS_TO_ROBUX = "credits_to_robux"
    ROBUX_TO_CREDITS = "robux_to_credits"


@dataclass(slots=True, frozen=True)
class CalculationRequest:
    kind: CalculationKind
    amount: Decimal | int


_ROBUX_WORD = re.compile(r"\brobux\b", re.IGNORECASE)
_NUMBER = re.compile(r"(?<!\d)(\d{1,9}(?:[.,]\d{1,6})?)(?!\d)")
_MONEY_ONLY = re.compile(
    r"^\s*(?:r\$\s*)?(\d{1,9}(?:[.,]\d{1,2})?)\s*(?:reais?|creditos?|créditos?)?\s*$",
    re.IGNORECASE,
)


def _decimal(raw: str) -> Decimal:
    try:
        return Decimal(raw.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("Número inválido") from exc


def parse_calculation_message(message: str) -> CalculationRequest | None:
    text = message.strip()
    if not text:
        return None

    if _ROBUX_WORD.search(text):
        match = _NUMBER.search(text)
        if match is None:
            return None
        amount = _decimal(match.group(1))
        if amount <= 0 or amount != amount.to_integral_value():
            return None
        return CalculationRequest(CalculationKind.ROBUX_TO_CREDITS, int(amount))

    money_match = _MONEY_ONLY.fullmatch(text)
    if money_match is None:
        return None
    amount = _decimal(money_match.group(1))
    if amount <= 0:
        return None
    return CalculationRequest(CalculationKind.CREDITS_TO_ROBUX, amount)
