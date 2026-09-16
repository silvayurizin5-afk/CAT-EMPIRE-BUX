import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from enum import StrEnum

from app.core.money import money

ROBUX_PRICE_PER_100 = Decimal("2.90")
VIA_PLUS_PRICE_PER_100 = Decimal("5.10")
ROBLOX_NET_AFTER_FEE = Decimal("0.70")


class CalculationKind(StrEnum):
    ROBUX = "robux"
    MONEY = "money"


@dataclass(slots=True, frozen=True)
class CalculationRequest:
    kind: CalculationKind
    amount: Decimal | int
    coupon_code: str | None = None


@dataclass(slots=True, frozen=True)
class RobuxQuote:
    robux: int
    gamepass_brl: Decimal
    via_plus_brl: Decimal
    covering_fee_brl: Decimal


_ROBUX_WORD = re.compile(r"\brobux\b", re.IGNORECASE)
_MONEY_WORD = re.compile(r"(?:r\$|\breais?\b)", re.IGNORECASE)
_NUMBER = re.compile(r"(?<!\d)(\d{1,9}(?:[.,]\d{1,2})?)(?!\d)")
_BARE_INTEGER = re.compile(r"^\s*(\d{1,9})\s*$")
_COUPON = re.compile(r'"([^"\n]{1,40})"')


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(without_marks.casefold().split())


def _decimal(raw: str) -> Decimal:
    try:
        return Decimal(raw.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("Número inválido") from exc


def extract_coupon_code(message: str) -> str | None:
    matches = [match.strip() for match in _COUPON.findall(message) if match.strip()]
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError("Use somente um cupom por cálculo.")
    return matches[0][:40]


def strip_coupon(message: str) -> str:
    return _COUPON.sub(" ", message)


def parse_calculation_message(message: str) -> CalculationRequest | None:
    text = message.strip()
    if not text:
        return None

    try:
        coupon_code = extract_coupon_code(text)
    except ValueError:
        return None
    clean = strip_coupon(text).strip()

    if _MONEY_WORD.search(clean):
        match = _NUMBER.search(clean)
        if match is None:
            return None
        amount = _decimal(match.group(1))
        if amount <= 0:
            return None
        return CalculationRequest(CalculationKind.MONEY, money(amount), coupon_code)

    if _ROBUX_WORD.search(clean):
        match = _NUMBER.search(clean)
        if match is None:
            return None
        amount = _decimal(match.group(1))
        if amount <= 0 or amount != amount.to_integral_value():
            return None
        return CalculationRequest(CalculationKind.ROBUX, int(amount), coupon_code)

    bare = _BARE_INTEGER.fullmatch(clean)
    if bare is None:
        return None
    amount = int(bare.group(1))
    if amount <= 0:
        return None
    return CalculationRequest(CalculationKind.ROBUX, amount, coupon_code)


def apply_discount(value: Decimal, discount_percent: Decimal | None) -> Decimal:
    total = money(value)
    if discount_percent is None:
        return total
    percent = Decimal(discount_percent)
    if percent <= 0 or percent >= 100:
        raise ValueError("Percentual de desconto inválido")
    return money(total * (Decimal("1") - (percent / Decimal("100"))))


def robux_price(robux: int, price_per_100: Decimal = ROBUX_PRICE_PER_100) -> Decimal:
    if robux <= 0:
        raise ValueError("Quantidade de Robux inválida")
    return money(Decimal(robux) * Decimal(price_per_100) / Decimal("100"))


def robux_quote(robux: int, discount_percent: Decimal | None = None) -> RobuxQuote:
    gamepass = robux_price(robux, ROBUX_PRICE_PER_100)
    via_plus = robux_price(robux, VIA_PLUS_PRICE_PER_100)
    covering_fee = money(gamepass / ROBLOX_NET_AFTER_FEE)
    return RobuxQuote(
        robux=robux,
        gamepass_brl=apply_discount(gamepass, discount_percent),
        via_plus_brl=apply_discount(via_plus, discount_percent),
        covering_fee_brl=apply_discount(covering_fee, discount_percent),
    )


def robux_from_brl(amount_brl: Decimal, price_per_100: Decimal = ROBUX_PRICE_PER_100) -> int:
    amount = money(amount_brl)
    if amount <= 0:
        raise ValueError("Valor em reais inválido")
    raw = amount * Decimal("100") / Decimal(price_per_100)
    return int(raw.to_integral_value(rounding=ROUND_DOWN))


def format_brl(value: Decimal) -> str:
    raw = f"{money(value):,.2f}"
    localized = raw.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {localized}"


def format_robux(value: int) -> str:
    return f"{int(value):,}".replace(",", ".") + " Robux"
