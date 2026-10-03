"""Shift and daily money: online payments, cash and balance.

Per shift:
    Total        = pump sales (revenue from the meter readings)
    Online money = what staff typed, e.g. "4000+60000+5000" = 69,000
    Total cash   = Total - Online money
    Balance      = Total cash - Expenses paid during the shift   (cash to hand in)

Per day D - MANAGER ONLY. The Morning shift opens the day, then any added shift (Afternoon...),
then the Night shift of D:
    Each shift's Total cash    = that shift's Total - that shift's OWN online money
    Total money online per day = sum of every shift's online money
    Total cash of the day      = sum of each shift's Total cash
    Balance of the day         = Total cash of the day - Expenses of the day

Staff view of online money (online_view): the opening (Morning) shift sees the online money of the
night shift that just ended as a fixed figure, plus its own:
    Total money online = Online (night shift, fixed) + Online (this shift)
It is only a sum - it never changes this shift's cash - and staff never see the day's total sales.

Online money is removed once (in Total cash); it is not subtracted again from the balance.
Expenses count here as soon as they are recorded (PENDING or APPROVED) because the cash has left the
drawer; the profit/loss report only counts APPROVED expenses.
"""
import re
from decimal import Decimal, InvalidOperation

from sqlalchemy import func

from ..calculations import ZERO, D, money
from ..extensions import db
from ..models import Expense, PumpReading, Shift, ShiftCash

CASH_EXPENSE_STATUSES = ("PENDING", "APPROVED")
_ALLOWED = re.compile(r"^[\d\s.,+]*$")
MAX_TERMS = 200


def parse_amount_expression(text):
    """'4000+60000+5000' -> (Decimal('69000'), '4000+60000+5000'). Commas and spaces are ignored."""
    text = (text or "").strip()
    if not text:
        return ZERO, ""
    if not _ALLOWED.match(text):
        raise ValueError("Use only numbers and + (for example 4000+60000+5000)")
    terms = [t.replace(",", "").replace(" ", "") for t in text.split("+")]
    if any(t == "" for t in terms):
        raise ValueError("There is a + without a number next to it")
    if len(terms) > MAX_TERMS:
        raise ValueError(f"Please enter at most {MAX_TERMS} amounts")
    try:
        values = [Decimal(t) for t in terms]
    except InvalidOperation:
        raise ValueError("One of the amounts is not a valid number")
    return money(sum(values, ZERO)), "+".join(terms)


def _sales(shift_ids):
    if not shift_ids:
        return ZERO
    return money(db.session.query(func.coalesce(func.sum(PumpReading.revenue), 0))
                 .filter(PumpReading.shift_id.in_(shift_ids)).scalar())


def shift_money(shift):
    cash = ShiftCash.query.filter_by(shift_id=shift.id).first()
    expenses = (Expense.query.filter(Expense.shift_id == shift.id, Expense.status.in_(CASH_EXPENSE_STATUSES))
                .order_by(Expense.id).all())
    total = _sales([shift.id])
    online = money(cash.online_total) if cash else ZERO
    spent = money(sum((D(e.amount) for e in expenses), ZERO))
    total_cash = money(total - online)
    return {
        "total": total,
        "online_expression": cash.online_expression if cash else "",
        "online": online,
        "total_cash": total_cash,
        "expenses": spent,
        "expense_items": [{"id": e.id, "category": e.category.name, "description": e.description,
                           "amount": e.amount, "status": e.status, "reference": e.reference,
                           "expense_date": e.expense_date, "entered_by": e.entered_by} for e in expenses],
        "balance": money(total_cash - spent),
    }


def shifts_of_day(station_id, day):
    """The shifts of day D in order: Morning (opening) first, then any added shift, then Night."""
    return (Shift.query.filter(Shift.station_id == station_id, Shift.status != "CANCELLED",
                               Shift.shift_date == day)
            .order_by(Shift.sequence).all())


def online_view(shift):
    """Staff: the PREVIOUS shift's online money exactly as its staff typed it (fixed, read-only)
    + this shift's own online money. Only online money - never sales figures."""
    from .shifts import previous_shift
    mine = ShiftCash.query.filter_by(shift_id=shift.id).first()
    own = money(mine.online_total) if mine else ZERO
    previous = None
    before = previous_shift(shift)
    if before is not None:
        row = ShiftCash.query.filter_by(shift_id=before.id).first()
        previous = {"shift_id": before.id, "name": before.type_name, "shift_date": before.shift_date,
                    "online_expression": row.online_expression if row else "",   # e.g. "4000+60000+5000"
                    "online": money(row.online_total) if row else ZERO}
    carried = previous["online"] if previous else ZERO
    return {"previous": previous, "own_expression": mine.online_expression if mine else "", "own": own,
            "total": money(carried + own)}


def day_money(station_id, day):
    """MANAGER ONLY: every shift's sales, online money and cash for day D, and the day totals."""
    shifts = shifts_of_day(station_id, day)
    per_shift = []
    for s in shifts:
        m = shift_money(s)
        per_shift.append({"shift_id": s.id, "shift_type": s.shift_type, "name": s.type_name, "label": s.label,
                          "shift_date": s.shift_date, "status": s.status,
                          "total": m["total"], "online": m["online"], "online_expression": m["online_expression"],
                          # this shift's cash uses ONLY this shift's own online money
                          "total_cash": m["total_cash"], "expenses": m["expenses"], "balance": m["balance"]})
    total = money(sum((p["total"] for p in per_shift), ZERO))
    online = money(sum((p["online"] for p in per_shift), ZERO))
    total_cash = money(sum((p["total_cash"] for p in per_shift), ZERO))
    # Expenses paid during these shifts, plus any expense not tied to a shift that is dated on this day.
    shift_expenses = money(sum((p["expenses"] for p in per_shift), ZERO))
    other_expenses = money(db.session.query(func.coalesce(func.sum(Expense.amount), 0))
                           .filter(Expense.station_id == station_id, Expense.shift_id.is_(None),
                                   Expense.expense_date == day,
                                   Expense.status.in_(CASH_EXPENSE_STATUSES)).scalar())
    expenses = money(shift_expenses + other_expenses)
    return {
        "date": day,
        "shifts": per_shift,
        "total": total,
        "online_total": online,        # = sum of every shift's online money that day
        "total_cash": total_cash,      # = sum of each shift's (its total - its own online money)
        "expenses": expenses,
        "balance": money(total_cash - expenses),
    }
