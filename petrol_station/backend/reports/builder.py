"""Build report data from the database.

Every report is a dict: title, subtitle, summary [(label, value, kind)] and
tables [{title, columns [(key, label, kind)], rows [dict]}]. The same structure
feeds the JSON preview, the PDF export and the Excel export.
kind is one of: text, money, litres, number, date.
"""
from collections import Counter
from datetime import date, timedelta

from sqlalchemy import func

from ..calculations import ZERO, D, money
from ..calculations.periods import period_bounds, period_label
from ..extensions import db
from ..models import (AuditLog, Expense, FuelRefill, FuelStock, FuelType, Pump, PumpReading, Shift,
                      ShiftDeposit, ShiftStaff, User)
from ..services.clock import today_local
from ..services.cash import day_money
from ..services.finance import financial_summary

REPORT_TYPES = {
    "daily": "Daily Report",
    "shift": "Shift Report",
    "weekly": "Weekly Report",
    "monthly": "Monthly Report",
    "yearly": "Yearly Report",
    "fuel_sales": "Fuel Sales Report",
    "fuel_stock": "Fuel Stock Report",
    "refills": "Refill Report",
    "expenses": "Expense Report",
    "profit_loss": "Profit / Loss Report",
    "staff_shifts": "Staff Shift Report",
    "daily_money": "Daily Money (online, cash, balance)",
    "audit": "Audit Report",
}


def resolve_range(kind, station_id, start=None, end=None):
    today = today_local(station_id)
    anchor = start or today
    if kind == "daily":
        return anchor, anchor
    if kind == "weekly":
        monday = anchor - timedelta(days=anchor.weekday())
        return monday, monday + timedelta(days=6)
    if kind == "monthly":
        return period_bounds(anchor)          # first-Wednesday reporting period
    if kind == "yearly":
        return date(anchor.year, 1, 1), date(anchor.year, 12, 31)
    if start and end:
        return start, end
    s, e = period_bounds(today)
    return start or s, end or e


def _names(station_id):
    return {u.id: u.full_name for u in User.query}


def _pl_summary(s):
    rows = [
        ("Fuel sold", s["fuel_sold_litres"], "litres"),
        ("Revenue", s["revenue"], "money"),
        ("Fuel purchased", s["fuel_purchased_litres"], "litres"),
        ("Fuel purchase cost", s["fuel_purchase_cost"], "money"),
        ("Opening inventory value", s["opening_inventory_value"], "money"),
        ("Closing inventory value", s["closing_inventory_value"], "money"),
        ("Cost of fuel sold", s["cost_of_fuel_sold"], "money"),
        ("Gross profit", s["gross_profit"], "money"),
        ("Operating expenses", s["operating_expenses"], "money"),
        ("Net loss" if s["is_loss"] else "Net profit", abs(s["net_profit"]), "money"),
    ]
    return rows


def _fuel_table(s):
    return {
        "title": "By fuel type (weighted-average costing)",
        "columns": [("fuel", "Fuel", "text"), ("opening_qty", "Opening L", "litres"),
                    ("purchases_qty", "Purchased L", "litres"), ("sold_qty", "Sold L", "litres"),
                    ("closing_qty", "Closing L", "litres"), ("sales_revenue", "Revenue", "money"),
                    ("cost_of_fuel_sold", "Cost of sales", "money"), ("gross_profit", "Gross profit", "money"),
                    ("average_cost", "Avg cost/L", "money")],
        "rows": s["by_fuel"],
    }


def _expense_category_table(s):
    return {"title": "Expenses by category", "columns": [("category", "Category", "text"), ("amount", "Amount", "money")],
            "rows": s["expenses_by_category"]}


def _breakdown(station_id, start, end, step):
    rows = []
    cursor = start
    while cursor <= end:
        if step == "day":
            s, e, label = cursor, cursor, cursor.strftime("%a %d %b %Y")
            nxt = cursor + timedelta(days=1)
        else:
            s = cursor
            nxt = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
            e, label = min(nxt - timedelta(days=1), end), cursor.strftime("%B %Y")
        f = financial_summary(station_id, s, e)
        rows.append({"period": label, "litres": f["fuel_sold_litres"], "revenue": f["revenue"],
                     "cogs": f["cost_of_fuel_sold"], "expenses": f["operating_expenses"], "net": f["net_profit"]})
        cursor = nxt
    return {
        "title": "Daily breakdown" if step == "day" else "Monthly breakdown",
        "columns": [("period", "Period", "text"), ("litres", "Fuel sold L", "litres"), ("revenue", "Revenue", "money"),
                    ("cogs", "Cost of fuel", "money"), ("expenses", "Expenses", "money"), ("net", "Profit/Loss", "money")],
        "rows": rows,
    }


def _shift_table(station_id, start, end):
    names = _names(station_id)
    rows = []
    for shift in (Shift.query.filter(Shift.station_id == station_id, Shift.status != "CANCELLED",
                                     Shift.shift_date >= start, Shift.shift_date <= end)
                  .order_by(Shift.shift_date, Shift.sequence).all()):
        rev, litres = (db.session.query(func.coalesce(func.sum(PumpReading.revenue), 0),
                                        func.coalesce(func.sum(PumpReading.fuel_sold), 0))
                       .filter(PumpReading.shift_id == shift.id).one())
        dep = ShiftDeposit.query.filter_by(shift_id=shift.id).first()
        rows.append({"shift": shift.label, "status": shift.status,
                     "staff": ", ".join(names.get(a.user_id, "?") for a in shift.staff),
                     "litres": D(litres, ZERO), "expected": money(rev),
                     "deposited": dep.actual_amount if dep else None, "difference": dep.difference if dep else None})
    return {"title": "Shifts", "columns": [("shift", "Shift", "text"), ("status", "Status", "text"),
                                           ("staff", "Staff", "text"), ("litres", "Litres", "litres"),
                                           ("expected", "Expected money", "money"), ("deposited", "Deposited", "money"),
                                           ("difference", "Difference", "money")], "rows": rows}


def _sales_table(station_id, start, end, shift_id=None):
    q = (db.session.query(PumpReading, Shift, Pump, FuelType)
         .join(Shift, Shift.id == PumpReading.shift_id).join(Pump, Pump.id == PumpReading.pump_id)
         .join(FuelType, FuelType.id == PumpReading.fuel_type_id).filter(Shift.station_id == station_id))
    q = q.filter(Shift.id == shift_id) if shift_id else q.filter(Shift.shift_date >= start, Shift.shift_date <= end)
    rows = [{"shift": s.label, "pump": p.pump_name, "fuel": f.name, "opening": r.opening_meter,
             "closing": r.closing_meter, "sold": r.fuel_sold, "price": r.price_per_litre, "revenue": r.revenue}
            for r, s, p, f in q.order_by(Shift.shift_date, Shift.sequence, FuelType.id, Pump.pump_number).all()]
    return {"title": "Pump readings and fuel sales",
            "columns": [("shift", "Shift", "text"), ("pump", "Pump", "text"), ("fuel", "Fuel", "text"),
                        ("opening", "Opening meter", "number"), ("closing", "Closing meter", "number"),
                        ("sold", "Fuel sold L", "litres"), ("price", "Price/L", "money"), ("revenue", "Revenue", "money")],
            "rows": rows}


def _stock_table(station_id, start, end, shift_id=None):
    q = (db.session.query(FuelStock, Shift, FuelType).join(Shift, Shift.id == FuelStock.shift_id)
         .join(FuelType, FuelType.id == FuelStock.fuel_type_id).filter(Shift.station_id == station_id))
    q = q.filter(Shift.id == shift_id) if shift_id else q.filter(Shift.shift_date >= start, Shift.shift_date <= end)
    rows = [{"shift": s.label, "fuel": f.name, "expected_opening": r.expected_opening_stock, "opening": r.opening_stock,
             "refilled": r.refilled_quantity, "sold": r.fuel_sold, "adjustments": r.adjustments,
             "calculated": r.calculated_closing_stock, "actual": r.actual_closing_stock,
             "difference": r.stock_difference, "status": r.status}
            for r, s, f in q.order_by(Shift.shift_date, Shift.sequence, FuelType.id).all()]
    return {"title": "Fuel stock (Closing = Opening + Refilled - Sold)",
            "columns": [("shift", "Shift", "text"), ("fuel", "Fuel", "text"),
                        ("expected_opening", "Prev. closing", "litres"), ("opening", "Opening", "litres"),
                        ("refilled", "Refilled", "litres"), ("sold", "Sold", "litres"),
                        ("adjustments", "Adjust.", "litres"), ("calculated", "Calc. closing", "litres"),
                        ("actual", "Actual closing", "litres"), ("difference", "Difference", "litres"),
                        ("status", "Status", "text")],
            "rows": rows}


def _refill_table(station_id, start, end, shift_id=None):
    names = _names(station_id)
    q = FuelRefill.query.filter(FuelRefill.station_id == station_id)
    q = q.filter(FuelRefill.shift_id == shift_id) if shift_id else q.filter(
        FuelRefill.refill_date >= start, FuelRefill.refill_date <= end)
    rows = [{"date": r.refill_date, "time": r.refill_time.strftime("%H:%M"), "fuel": r.fuel_type.name,
             "supplier": r.supplier.name if r.supplier else "", "invoice": r.invoice_number, "qty": r.quantity_litres,
             "cost": r.cost_per_litre, "total": r.total_cost, "status": r.status,
             "recorded_by": names.get(r.recorded_by), "confirmed_by": names.get(r.confirmed_by),
             "approved_by": names.get(r.approved_by)}
            for r in q.order_by(FuelRefill.refill_date, FuelRefill.refill_time).all()]
    return {"title": "Fuel refills",
            "columns": [("date", "Date", "date"), ("time", "Time", "text"), ("fuel", "Fuel", "text"),
                        ("supplier", "Supplier", "text"), ("invoice", "Invoice", "text"), ("qty", "Quantity L", "litres"),
                        ("cost", "Cost/L", "money"), ("total", "Total cost", "money"), ("status", "Status", "text"),
                        ("recorded_by", "Recorded by", "text"), ("confirmed_by", "Confirmed by", "text"),
                        ("approved_by", "Approved by", "text")],
            "rows": rows}


def _expense_table(station_id, start, end):
    names = _names(station_id)
    rows = [{"date": e.expense_date, "category": e.category.name, "description": e.description,
             "reference": e.reference, "amount": e.amount, "status": e.status,
             "entered_by": names.get(e.entered_by), "approved_by": names.get(e.approved_by)}
            for e in Expense.query.filter(Expense.station_id == station_id, Expense.expense_date >= start,
                                          Expense.expense_date <= end).order_by(Expense.expense_date).all()]
    return {"title": "Expenses",
            "columns": [("date", "Date", "date"), ("category", "Category", "text"), ("description", "Description", "text"),
                        ("reference", "Reference", "text"), ("amount", "Amount", "money"), ("status", "Status", "text"),
                        ("entered_by", "Entered by", "text"), ("approved_by", "Approved by", "text")],
            "rows": rows}


def _staff_table(station_id, start, end):
    rows = []
    for user in User.query.filter_by(station_id=station_id).order_by(User.full_name).all():
        shifts = (db.session.query(Shift).join(ShiftStaff, ShiftStaff.shift_id == Shift.id)
                    .filter(ShiftStaff.user_id == user.id, Shift.status != "CANCELLED",
                          Shift.shift_date >= start, Shift.shift_date <= end).all())
        if not shifts:
            continue
        ids = [s.id for s in shifts]
        rev, litres = (db.session.query(func.coalesce(func.sum(PumpReading.revenue), 0),
                                        func.coalesce(func.sum(PumpReading.fuel_sold), 0))
                       .filter(PumpReading.shift_id.in_(ids)).one())
        diff = (db.session.query(func.coalesce(func.sum(ShiftDeposit.difference), 0))
                .filter(ShiftDeposit.shift_id.in_(ids)).scalar())
        refills = FuelRefill.query.filter(FuelRefill.recorded_by == user.id, FuelRefill.refill_date >= start,
                                          FuelRefill.refill_date <= end).count()
        rows.append({"staff": user.full_name, "username": user.username, "shifts": len(shifts),
                     "by_type": ", ".join(f"{t.replace('_', ' ').title()} {n}" for t, n in
                                          sorted(Counter(s.shift_type for s in shifts).items())),
                     "flagged": sum(s.status == "FLAGGED" for s in shifts), "litres": D(litres, ZERO),
                     "revenue": money(rev), "deposit_difference": money(diff), "refills": refills})
    return {"title": "Staff shifts",
            "columns": [("staff", "Staff", "text"), ("username", "Username", "text"), ("shifts", "Shifts", "number"),
                        ("by_type", "By shift type", "text"), ("flagged", "Flagged", "number"),
                        ("litres", "Litres sold", "litres"), ("revenue", "Revenue", "money"),
                        ("deposit_difference", "Deposit diff.", "money"), ("refills", "Refills entered", "number")],
            "rows": rows}


def _money_table(station_id, start, end):
    """One row per day: online per shift, total online, total, total cash, expenses, balance of the day."""
    days, names = [], []
    d = start
    while d <= end:
        m = day_money(station_id, d)
        for s in m["shifts"]:
            if s["name"] not in names:
                names.append(s["name"])
        days.append(m)
        d += timedelta(days=1)
    rows = []
    for m in days:
        row = {"date": m["date"], "total": m["total"], "online_total": m["online_total"],
               "total_cash": m["total_cash"], "expenses": m["expenses"], "balance": m["balance"]}
        for s in m["shifts"]:
            row[f"online_{s['name']}"] = s["online"]
            row[f"cash_{s['name']}"] = s["total_cash"]
        rows.append(row)
    columns = ([("date", "Date", "date")]
               + [(f"online_{n}", f"Online ({n.lower()} shift)", "money") for n in names]
               + [("online_total", "Total online per day", "money"), ("total", "Total (sales)", "money")]
               + [(f"cash_{n}", f"Cash ({n.lower()} shift) = its total − its online", "money") for n in names]
               + [("total_cash", "Total cash of the day", "money"), ("expenses", "Expenses", "money"),
                  ("balance", "Balance of the day", "money")])
    return {"title": "Daily money", "columns": columns, "rows": rows}, days


def _audit_table(station_id, start, end):
    names = _names(station_id)
    logs = (AuditLog.query.filter(AuditLog.station_id == station_id, AuditLog.created_at >= start,
                                  AuditLog.created_at < end + timedelta(days=1))
            .order_by(AuditLog.created_at).limit(5000).all())
    rows = [{"when": l.created_at.strftime("%Y-%m-%d %H:%M UTC"), "user": names.get(l.user_id, "System"),
             "action": l.action, "record": f"{l.table_name or ''} #{l.record_id or ''}",
             "old": l.old_values or "", "new": l.new_values or "", "reason": l.reason or ""} for l in logs]
    return {"title": "Audit trail",
            "columns": [("when", "When", "text"), ("user", "User", "text"), ("action", "Action", "text"),
                        ("record", "Record", "text"), ("old", "Previous value", "text"), ("new", "New value", "text"),
                        ("reason", "Reason", "text")],
            "rows": rows}


def build(kind, station_id, start=None, end=None, shift_id=None):
    if kind not in REPORT_TYPES:
        raise ValueError("Unknown report type")
    report = {"type": kind, "title": REPORT_TYPES[kind], "summary": [], "tables": []}

    if kind == "shift":
        shift = db.session.get(Shift, shift_id) if shift_id else None
        if shift is None or shift.station_id != station_id:
            raise ValueError("Choose a shift for the shift report")
        start = end = shift.shift_date
        report["subtitle"] = f"{shift.label} — status {shift.status}"
        sales = _sales_table(station_id, start, end, shift.id)
        expected = money(sum((D(r["revenue"], ZERO) for r in sales["rows"]), ZERO))
        dep = ShiftDeposit.query.filter_by(shift_id=shift.id).first()
        report["summary"] = [
            ("Fuel sold", sum((D(r["sold"], ZERO) for r in sales["rows"]), ZERO), "litres"),
            ("Money expected from this shift", expected, "money"),
            ("Actual amount deposited", dep.actual_amount if dep else None, "money"),
            ("Difference", dep.difference if dep else None, "money"),
            ("Staff", ", ".join(a.user.full_name for a in shift.staff) or "-", "text"),
        ]
        report["tables"] = [sales, _stock_table(station_id, start, end, shift.id),
                            _refill_table(station_id, start, end, shift.id)]
        report["start"], report["end"] = start, end
        return report

    start, end = resolve_range(kind, station_id, start, end)
    report["start"], report["end"] = start, end
    label = period_label(start) + " reporting period" if kind == "monthly" else None
    report["subtitle"] = label or (start.strftime("%d %b %Y") if start == end else
                                   f"{start:%d %b %Y} – {end:%d %b %Y}")
    s = financial_summary(station_id, start, end)

    if kind in ("daily", "weekly", "monthly", "yearly", "profit_loss"):
        report["summary"] = _pl_summary(s)
        report["tables"].append(_fuel_table(s))
        if kind == "daily":
            m = day_money(station_id, start)
            report["summary"] += [(f"Online ({s['name'].lower()} shift)", s["online"], "money") for s in m["shifts"]]
            report["summary"] += [("Total money online per day", m["online_total"], "money"),
                                  ("Total cash = Total − Online", m["total_cash"], "money"),
                                  ("Balance of the day = Total cash − Expenses", m["balance"], "money")]
            report["tables"] += [_shift_table(station_id, start, end), _refill_table(station_id, start, end),
                                 _expense_table(station_id, start, end)]
        elif kind in ("weekly", "monthly"):
            report["tables"] += [_breakdown(station_id, start, end, "day"), _expense_category_table(s)]
        elif kind == "yearly":
            report["tables"] += [_breakdown(station_id, start, end, "month"), _expense_category_table(s)]
        else:
            report["tables"].append(_expense_category_table(s))
    elif kind == "fuel_sales":
        report["summary"] = [("Fuel sold", s["fuel_sold_litres"], "litres"), ("Revenue", s["revenue"], "money")]
        report["tables"] = [_sales_table(station_id, start, end)]
    elif kind == "fuel_stock":
        report["summary"] = [(f"{f['fuel']} closing stock", f["closing_qty"], "litres") for f in s["by_fuel"]]
        report["tables"] = [_stock_table(station_id, start, end)]
    elif kind == "refills":
        report["summary"] = [("Fuel purchased", s["fuel_purchased_litres"], "litres"),
                             ("Purchase cost", s["fuel_purchase_cost"], "money")]
        report["tables"] = [_refill_table(station_id, start, end)]
    elif kind == "expenses":
        report["summary"] = [("Approved expenses", s["operating_expenses"], "money")]
        report["tables"] = [_expense_category_table(s), _expense_table(station_id, start, end)]
    elif kind == "daily_money":
        table, days = _money_table(station_id, start, end)
        total = lambda k: money(sum((D(m[k], ZERO) for m in days), ZERO))  # noqa: E731
        report["summary"] = [("Total money online", total("online_total"), "money"),
                             ("Total (sales)", total("total"), "money"),
                             ("Total cash = Total − Online", total("total_cash"), "money"),
                             ("Expenses", total("expenses"), "money"),
                             ("Balance", total("balance"), "money")]
        report["tables"] = [table]
    elif kind == "staff_shifts":
        report["tables"] = [_staff_table(station_id, start, end)]
    elif kind == "audit":
        report["tables"] = [_audit_table(station_id, start, end)]
    return report
