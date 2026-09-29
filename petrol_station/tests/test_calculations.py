from datetime import date, datetime
from decimal import Decimal

import pytest

from backend.calculations.costing import OPENING, PURCHASE, SALE, InventoryEvent, run_weighted_average, summarize
from backend.calculations.fuel import InvalidMeterReading, closing_stock, fuel_sold, refill_total_cost, revenue
from backend.calculations.periods import first_wednesday, is_first_wednesday, period_bounds, previous_period_bounds


def test_first_wednesday_is_calculated():
    assert first_wednesday(2026, 9) == date(2026, 9, 2)
    assert first_wednesday(2026, 10) == date(2026, 10, 7)
    assert first_wednesday(2026, 7) == date(2026, 7, 1)   # month starting on a Wednesday
    assert is_first_wednesday(date(2026, 10, 7))
    assert not is_first_wednesday(date(2026, 10, 14))


def test_period_bounds_follow_first_wednesday():
    assert period_bounds(date(2026, 9, 28)) == (date(2026, 9, 2), date(2026, 10, 6))
    assert period_bounds(date(2026, 10, 6)) == (date(2026, 9, 2), date(2026, 10, 6))
    assert period_bounds(date(2026, 10, 7)) == (date(2026, 10, 7), date(2026, 11, 3))
    assert period_bounds(date(2027, 1, 1)) == (date(2026, 12, 2), date(2027, 1, 5))
    assert previous_period_bounds(date(2026, 10, 7)) == (date(2026, 9, 2), date(2026, 10, 6))


def test_fuel_formulas():
    assert fuel_sold(1000, 1500) == Decimal("500.000")
    assert revenue(500, 2900) == Decimal("1450000.00")
    assert refill_total_cost(5000, 2800) == Decimal("14000000.00")
    assert closing_stock(4000, 6000, 3000) == Decimal("7000.000")   # spec example
    with pytest.raises(InvalidMeterReading):
        fuel_sold(1500, 1000)


def test_weighted_average_cost_of_sales():
    d = date(2026, 9, 10)
    events = run_weighted_average([
        InventoryEvent(datetime(2026, 9, 10, 6), d, OPENING, Decimal(4000), Decimal(2500)),
        InventoryEvent(datetime(2026, 9, 10, 9), d, PURCHASE, Decimal(6000), Decimal(2800)),
        InventoryEvent(datetime(2026, 9, 10, 18), d, SALE, Decimal(3000), revenue=Decimal(3000 * 2900)),
    ])
    s = summarize(events, d, d)
    # avg = (4000*2500 + 6000*2800) / 10000 = 2680
    assert s["cost_of_fuel_sold"] == Decimal("8040000.00")
    assert s["closing_qty"] == Decimal(7000)
    # Opening + purchases - closing = cost of sales (the refill is NOT expensed in full)
    assert s["opening_balance_value"] + s["purchases_value"] - s["closing_value"] == s["cost_of_fuel_sold"]
