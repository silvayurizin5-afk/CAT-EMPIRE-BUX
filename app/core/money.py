from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value: Decimal | str | int) -> Decimal:
    """Normaliza valores monetários para duas casas sem usar float."""
    if isinstance(value, float):
        raise TypeError("Valores monetários não podem usar float")
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def require_positive(value: Decimal | str | int) -> Decimal:
    normalized = money(value)
    if normalized <= ZERO:
        raise ValueError("O valor precisa ser maior que zero")
    return normalized
