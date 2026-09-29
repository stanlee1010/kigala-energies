"""Fuel inventory costing.

SELECTED METHOD: PERPETUAL (MOVING) WEIGHTED-AVERAGE COST, per fuel type.

A refill is NOT charged as an expense on the day it arrives. Instead it enters
inventory, and only the litres actually sold are charged as cost of fuel sold:

  * Opening balance / purchase of q litres at cost c:
        avg = (qty * avg + q * c) / (qty + q);   qty += q
  * Sale of s litres (closed pump readings of a shift):
        cost_of_sale = s * avg;                  qty -= s      (avg unchanged)
  * Other stock adjustment of a litres (dip correction, loss):
        value change = a * avg;                  qty += a      (avg unchanged)

For any date range this satisfies the textbook identity

    Opening Inventory Cost + Fuel Purchases - Closing Inventory Cost
        = Cost of Fuel Sold + Adjustment Value

and profit is then

    Gross Profit = Fuel Sales Revenue - Cost of Fuel Sold
    Net Profit   = Gross Profit - Operating Expenses

Events are processed in chronological order: refills at their recorded
date/time, shift sales at the shift's scheduled end.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from . import ZERO, money

OPENING, PURCHASE, SALE, ADJUST = "OPENING", "PURCHASE", "SALE", "ADJUST"
_ORDER = {OPENING: 0, PURCHASE: 1, ADJUST: 2, SALE: 3}


@dataclass
class InventoryEvent:
    when: datetime
    business_date: date
    kind: str
    quantity: Decimal
    unit_cost: Decimal = ZERO
    revenue: Decimal = ZERO
    reference: str = ""
    # Filled in by run_weighted_average():
    value: Decimal = field(default=ZERO)
    avg_cost_after: Decimal = field(default=ZERO)
    qty_after: Decimal = field(default=ZERO)


def run_weighted_average(events):
    """Process events in time order; sets value/avg/qty on each event and returns them."""
    events = sorted(events, key=lambda e: (e.when, _ORDER[e.kind]))
    qty, avg = ZERO, ZERO
    for ev in events:
        q = Decimal(ev.quantity)
        if ev.kind in (OPENING, PURCHASE):
            cost = Decimal(ev.unit_cost or 0)
            if qty <= 0:
                avg = cost
            else:
                avg = (qty * avg + q * cost) / (qty + q)
            qty += q
            ev.value = q * cost
        elif ev.kind == SALE:
            ev.value = q * avg
            qty -= q
        else:  # ADJUST (signed)
            ev.value = q * avg
            qty += q
        ev.avg_cost_after = avg
        ev.qty_after = qty
    return events


def summarize(events, start, end):
    """Summarize processed events for business dates start..end (inclusive)."""
    before = [e for e in events if e.business_date < start]
    inside = [e for e in events if start <= e.business_date <= end]
    upto_end = [e for e in events if e.business_date <= end]

    def state(evts):
        if not evts:
            return ZERO, ZERO, ZERO
        last = max(evts, key=lambda e: (e.when, _ORDER[e.kind]))
        return last.qty_after, last.avg_cost_after, last.qty_after * last.avg_cost_after

    open_qty, _, open_value = state(before)
    close_qty, close_avg, close_value = state(upto_end)

    def total(kind, attr):
        return sum((getattr(e, attr) for e in inside if e.kind == kind), ZERO)

    return {
        "opening_qty": open_qty,
        "opening_value": money(open_value),
        "opening_balance_qty": total(OPENING, "quantity"),
        "opening_balance_value": money(total(OPENING, "value")),
        "purchases_qty": total(PURCHASE, "quantity"),
        "purchases_value": money(total(PURCHASE, "value")),
        "sold_qty": total(SALE, "quantity"),
        "sales_revenue": money(total(SALE, "revenue")),
        "cost_of_fuel_sold": money(total(SALE, "value")),
        "adjustments_qty": total(ADJUST, "quantity"),
        "adjustments_value": money(total(ADJUST, "value")),
        "closing_qty": close_qty,
        "closing_value": money(close_value),
        "average_cost": money(close_avg),
    }
