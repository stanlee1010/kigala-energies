"""Pump and tank formulas."""
from . import ZERO, D, litres, money


class InvalidMeterReading(ValueError):
    title = "INVALID METER READING"
    message = "Closing meter reading cannot be lower than opening meter reading."

    def __init__(self):
        super().__init__(self.message)


def fuel_sold(opening_meter, closing_meter):
    """Fuel Sold = Closing Meter Reading - Opening Meter Reading"""
    opening, closing = D(opening_meter), D(closing_meter)
    if opening is None or closing is None:
        return ZERO
    if closing < opening:
        raise InvalidMeterReading()
    return litres(closing - opening)


def revenue(sold_litres, price_per_litre):
    """Revenue = Fuel Sold x Selling Price Per Litre"""
    return money(D(sold_litres, ZERO) * D(price_per_litre, ZERO))


def refill_total_cost(quantity, cost_per_litre):
    """Total Cost = Quantity x Cost Per Litre"""
    return money(D(quantity, ZERO) * D(cost_per_litre, ZERO))


def closing_stock(opening, refilled, sold, adjustments=ZERO):
    """Closing Stock = Opening Stock + Fuel Refilled - Fuel Sold (+ manager adjustments)"""
    return litres(D(opening, ZERO) + D(refilled, ZERO) - D(sold, ZERO) + D(adjustments, ZERO))


def expected_shift_money(revenues):
    """Petrol Revenue + Diesel Revenue (+ any other fuel) = Expected Shift Revenue"""
    return money(sum((D(r, ZERO) for r in revenues), ZERO))
