"""Pure business calculations (no Flask request context required)."""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

ZERO = Decimal("0")


def D(value, default=None):
    """Convert any numeric input to Decimal. Returns `default` for blanks."""
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"'{value}' is not a valid number")


def money(value):
    return D(value, ZERO).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def litres(value):
    return D(value, ZERO).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
