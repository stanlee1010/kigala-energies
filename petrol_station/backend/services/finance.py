"""Profit / loss from database records using weighted-average fuel costing.

See calculations/costing.py for the costing method.
"""
from datetime import date, datetime, timedelta

from sqlalchemy import func

from ..calculations import ZERO, D, money
from ..calculations.costing import (ADJUST, OPENING, PURCHASE, SALE, InventoryEvent,
                                    run_weighted_average, summarize)
from ..extensions import db
from ..models import (STOCK_REFILL_STATUSES, Expense, ExpenseCategory, FuelRefill, FuelType,
                      PumpReading, Shift, StockAdjustment)


def inventory_events(station_id, fuel_type_id):
    events = []
    for adj in StockAdjustment.query.filter_by(station_id=station_id, fuel_type_id=fuel_type_id).all():
        kind = OPENING if adj.adjustment_type == "OPENING_BALANCE" else ADJUST
        events.append(InventoryEvent(adj.adjusted_at, adj.adjusted_at.date(), kind, D(adj.quantity_litres),
                                     D(adj.cost_per_litre, ZERO), reference=f"adjustment:{adj.id}"))
    refills = FuelRefill.query.filter(FuelRefill.station_id == station_id,
                                      FuelRefill.fuel_type_id == fuel_type_id,
                                      FuelRefill.status.in_(STOCK_REFILL_STATUSES)).all()
    for r in refills:
        events.append(InventoryEvent(datetime.combine(r.refill_date, r.refill_time), r.refill_date, PURCHASE,
                                     D(r.quantity_litres), D(r.cost_per_litre), reference=f"refill:{r.id}"))
    sales = (db.session.query(Shift.id, Shift.shift_date, Shift.scheduled_end,
                              func.sum(PumpReading.fuel_sold), func.sum(PumpReading.revenue))
             .join(PumpReading, PumpReading.shift_id == Shift.id)
             .filter(Shift.station_id == station_id, PumpReading.fuel_type_id == fuel_type_id,
                     PumpReading.closing_meter.isnot(None))
             .group_by(Shift.id, Shift.shift_date, Shift.scheduled_end).all())
    for shift_id, shift_date, ends, sold, revenue in sales:
        if D(sold, ZERO) > 0:
            events.append(InventoryEvent(ends, shift_date, SALE, D(sold), revenue=D(revenue, ZERO),
                                         reference=f"shift:{shift_id}"))
    return run_weighted_average(events)


def _fuel_types_with_data(station_id):
    return FuelType.query.order_by(FuelType.id).all()


def expenses_total(station_id, start, end):
    total = (db.session.query(func.coalesce(func.sum(Expense.amount), 0))
             .filter(Expense.station_id == station_id, Expense.status == "APPROVED",
                     Expense.expense_date >= start, Expense.expense_date <= end).scalar())
    return money(total)


def expenses_by_category(station_id, start, end):
    rows = (db.session.query(ExpenseCategory.name, func.sum(Expense.amount))
            .join(Expense, Expense.category_id == ExpenseCategory.id)
            .filter(Expense.station_id == station_id, Expense.status == "APPROVED",
                    Expense.expense_date >= start, Expense.expense_date <= end)
            .group_by(ExpenseCategory.name).order_by(func.sum(Expense.amount).desc()).all())
    return [{"category": name, "amount": money(amount)} for name, amount in rows]


def financial_summary(station_id, start, end):
    fuels = []
    for fuel in _fuel_types_with_data(station_id):
        s = summarize(inventory_events(station_id, fuel.id), start, end)
        s.update({"fuel_type_id": fuel.id, "fuel": fuel.name, "code": fuel.code, "color": fuel.color,
                  "gross_profit": money(s["sales_revenue"] - s["cost_of_fuel_sold"])})
        fuels.append(s)

    revenue = money(sum((f["sales_revenue"] for f in fuels), ZERO))
    cogs = money(sum((f["cost_of_fuel_sold"] for f in fuels), ZERO))
    expenses = expenses_total(station_id, start, end)
    gross = money(revenue - cogs)
    net = money(gross - expenses)
    return {
        "start": start, "end": end,
        "revenue": revenue,
        "fuel_sold_litres": sum((f["sold_qty"] for f in fuels), ZERO),
        "fuel_purchased_litres": sum((f["purchases_qty"] for f in fuels), ZERO),
        "fuel_purchase_cost": money(sum((f["purchases_value"] for f in fuels), ZERO)),
        "opening_inventory_value": money(sum((f["opening_value"] for f in fuels), ZERO)),
        "closing_inventory_value": money(sum((f["closing_value"] for f in fuels), ZERO)),
        "cost_of_fuel_sold": cogs,
        "operating_expenses": expenses,
        "gross_profit": gross,
        "net_profit": net,
        "is_loss": net < 0,
        "by_fuel": fuels,
        "expenses_by_category": expenses_by_category(station_id, start, end),
        "costing_method": "Perpetual weighted-average cost",
    }


# ---------------------------------------------------------------- chart series

def buckets(granularity, today):
    """Return [(label, start, end)] oldest first."""
    result = []
    if granularity == "daily":
        for i in range(29, -1, -1):
            d = today - timedelta(days=i)
            result.append((d.strftime("%d %b"), d, d))
    elif granularity == "weekly":
        monday = today - timedelta(days=today.weekday())
        for i in range(11, -1, -1):
            s = monday - timedelta(weeks=i)
            result.append((f"Wk {s.strftime('%d %b')}", s, s + timedelta(days=6)))
    elif granularity == "monthly":
        y, m = today.year, today.month
        months = []
        for _ in range(12):
            months.append((y, m))
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        for y, m in reversed(months):
            s = date(y, m, 1)
            e = (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)
            result.append((s.strftime("%b %Y"), s, e))
    elif granularity == "yearly":
        for y in range(today.year - 4, today.year + 1):
            result.append((str(y), date(y, 1, 1), date(y, 12, 31)))
    else:
        raise ValueError("granularity must be daily, weekly, monthly or yearly")
    return result


def chart_series(station_id, granularity, today):
    spans = buckets(granularity, today)
    sales = [e for fuel in _fuel_types_with_data(station_id)
             for e in inventory_events(station_id, fuel.id) if e.kind == SALE]
    first, last = spans[0][1], spans[-1][2]
    expense_rows = (db.session.query(Expense.expense_date, func.sum(Expense.amount))
                    .filter(Expense.station_id == station_id, Expense.status == "APPROVED",
                            Expense.expense_date >= first, Expense.expense_date <= last)
                    .group_by(Expense.expense_date).all())
    series = {"labels": [], "revenue": [], "fuel_sold": [], "expenses": [], "cost_of_fuel_sold": [],
              "profit": []}
    for label, s, e in spans:
        rev = sum((ev.revenue for ev in sales if s <= ev.business_date <= e), ZERO)
        qty = sum((ev.quantity for ev in sales if s <= ev.business_date <= e), ZERO)
        cogs = sum((ev.value for ev in sales if s <= ev.business_date <= e), ZERO)
        exp = sum((D(a, ZERO) for d, a in expense_rows if s <= d <= e), ZERO)
        series["labels"].append(label)
        series["revenue"].append(money(rev))
        series["fuel_sold"].append(qty)
        series["expenses"].append(money(exp))
        series["cost_of_fuel_sold"].append(money(cogs))
        series["profit"].append(money(rev - cogs - exp))
    return series
