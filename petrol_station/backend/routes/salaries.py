"""SALARIES: the manager sets each staff member's monthly salary and records money they take.

The same salary shows every month until the manager changes it. Money taken is deducted from the
salary of the month it was taken in. If a month ends BELOW zero (more taken than the salary), the
shortfall is deducted from the next month's salary too:
    remaining = salary - owed from last month - money taken (active entries only)
A positive balance is not carried (it was paid out). Staff see their own figures only.
"""
import re
from datetime import date

from flask import Blueprint, jsonify, request

from ..auth import current_user, login_required, manager_required
from ..calculations import ZERO, D, money
from ..extensions import db
from ..models import SalaryAdvance, SalaryMonth, User, utcnow
from ..services import audit
from ..services.clock import today_local
from .common import ApiError, body, date_field, decimal_field, get_or_404, optional_text, require_text

bp = Blueprint("salaries", __name__, url_prefix="/api")

MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
MAX_AMOUNT = D("1000000000")   # a typing mistake guard (1 billion)


def _month(value, station_id):
    if value in (None, ""):
        return today_local(station_id).strftime("%Y-%m")
    value = str(value).strip()
    if not MONTH.match(value):
        raise ApiError("Month must look like 2026-10")
    return value


def _salary(station_id, user_id, month):
    """(amount, own row?) - a month without its own salary uses the latest earlier one."""
    row = (SalaryMonth.query.filter(SalaryMonth.station_id == station_id, SalaryMonth.user_id == user_id,
                                    SalaryMonth.month <= month)
           .order_by(SalaryMonth.month.desc()).first())
    if row is None:
        return None, False
    return D(row.amount), row.month == month


def _next_month(month):
    year, mon = int(month[:4]), int(month[5:])
    return f"{year + mon // 12}-{mon % 12 + 1:02d}"


def owed_from_before(station_id, user_id, month):
    """What the staff member still owes from earlier months (0 if nothing): each month's shortfall
    (remaining below zero) is deducted from the next month."""
    salaries = (SalaryMonth.query.filter(SalaryMonth.station_id == station_id, SalaryMonth.user_id == user_id,
                                         SalaryMonth.month < month).order_by(SalaryMonth.month).all())
    taken = {}
    for m, amount in (db.session.query(SalaryAdvance.month, SalaryAdvance.amount)
                      .filter(SalaryAdvance.station_id == station_id, SalaryAdvance.user_id == user_id,
                              SalaryAdvance.status == "ACTIVE", SalaryAdvance.month < month)):
        taken[m] = taken.get(m, ZERO) + D(amount)
    starts = [r.month for r in salaries[:1]] + sorted(taken)[:1]
    if not starts:
        return ZERO
    owed, salary, current, i = ZERO, ZERO, min(starts), 0
    for _ in range(1200):                      # at most 100 years of months
        if current >= month:
            break
        while i < len(salaries) and salaries[i].month <= current:
            salary, i = D(salaries[i].amount), i + 1
        remaining = salary - owed - taken.get(current, ZERO)
        owed = -remaining if remaining < 0 else ZERO
        current = _next_month(current)
    return money(owed)


def _advances(station_id, user_id, month):
    return (SalaryAdvance.query.filter_by(station_id=station_id, user_id=user_id, month=month)
            .order_by(SalaryAdvance.taken_on, SalaryAdvance.id).all())


def salary_row(user, station_id, month):
    salary, own = _salary(station_id, user.id, month)
    entries = _advances(station_id, user.id, month)
    taken = money(sum((D(a.amount) for a in entries if a.status == "ACTIVE"), ZERO))
    owed = owed_from_before(station_id, user.id, month)
    names = {u.id: u.full_name for u in User.query.filter(
        User.id.in_(list({a.recorded_by for a in entries} | {a.voided_by for a in entries if a.voided_by})))}
    return {
        "user_id": user.id, "full_name": user.full_name, "username": user.username, "is_active": user.is_active,
        "month": month,
        "salary": money(salary) if salary is not None else None,
        "salary_carried": salary is not None and not own,     # same as an earlier month
        "owed_before": owed,                                  # shortfall from last month, deducted now
        "taken": taken,
        "remaining": money((salary or ZERO) - owed - taken),
        "advances": [{
            "id": a.id, "taken_on": a.taken_on, "amount": money(a.amount), "note": a.note, "status": a.status,
            "recorded_by": names.get(a.recorded_by), "void_reason": a.void_reason,
            "voided_by": names.get(a.voided_by),
        } for a in entries],
    }


def _staff_member(user_id, me):
    user = get_or_404(User, user_id, me.station_id, "Staff member")
    if user.role != "STAFF":
        raise ApiError("Salaries are kept for staff members only")
    return user


@bp.get("/salaries")
@manager_required
def list_salaries():
    me = current_user()
    month = _month(request.args.get("month"), me.station_id)
    staff = User.query.filter_by(station_id=me.station_id, role="STAFF").order_by(User.full_name).all()
    rows = [salary_row(u, me.station_id, month) for u in staff]
    # Disabled staff are listed only for months that still concern them.
    rows = [r for r in rows if r["is_active"] or r["advances"] or r["owed_before"]
            or (r["salary"] and not r["salary_carried"])]
    totals = {k: money(sum((D(r[k] or 0) for r in rows), ZERO)) for k in ("salary", "owed_before", "taken", "remaining")}
    return jsonify({"month": month, "rows": rows, "totals": totals})


@bp.put("/salaries/<int:user_id>")
@manager_required
def set_salary(user_id):
    me = current_user()
    user = _staff_member(user_id, me)
    data = body()
    month = _month(data.get("month"), me.station_id)
    amount = money(decimal_field(data, "salary", "Salary", minimum=ZERO))
    if amount > MAX_AMOUNT:
        raise ApiError("That salary is too large - please check the amount")
    row = SalaryMonth.query.filter_by(station_id=me.station_id, user_id=user.id, month=month).first()
    old, _ = _salary(me.station_id, user.id, month)
    if row is None:
        row = SalaryMonth(station_id=me.station_id, user_id=user.id, month=month, amount=amount, updated_by=me.id)
        db.session.add(row)
    else:
        row.amount, row.updated_by = amount, me.id
    db.session.flush()
    audit.log(me.station_id, me.id, "SALARY SET", "salary_months", row.id,
              {"staff": user.full_name, "month": month, "salary": money(old) if old is not None else None},
              {"staff": user.full_name, "month": month, "salary": amount})
    db.session.commit()
    return jsonify({"row": salary_row(user, me.station_id, month)})


@bp.post("/salaries/<int:user_id>/advances")
@manager_required
def add_advance(user_id):
    me = current_user()
    user = _staff_member(user_id, me)
    data = body()
    today = today_local(me.station_id)
    taken_on = date_field(data.get("taken_on"), "Date") if data.get("taken_on") else today
    if taken_on > today:
        raise ApiError("The date cannot be in the future")
    if taken_on < date(2000, 1, 1):
        raise ApiError("Please check the date")
    amount = money(decimal_field(data, "amount", "Amount", minimum=ZERO, allow_zero=False))
    if amount > MAX_AMOUNT:
        raise ApiError("That amount is too large - please check it")
    month = taken_on.strftime("%Y-%m")
    entry = SalaryAdvance(station_id=me.station_id, user_id=user.id, month=month, taken_on=taken_on, amount=amount,
                          note=optional_text(data, "note"), recorded_by=me.id)
    db.session.add(entry)
    db.session.flush()
    audit.log(me.station_id, me.id, "MONEY TAKEN FROM SALARY", "salary_advances", entry.id, None,
              {"staff": user.full_name, "month": month, "taken_on": taken_on, "amount": amount, "note": entry.note})
    db.session.commit()
    return jsonify({"row": salary_row(user, me.station_id, month)}), 201


@bp.post("/salary-advances/<int:advance_id>/void")
@manager_required
def void_advance(advance_id):
    me = current_user()
    entry = get_or_404(SalaryAdvance, advance_id, me.station_id, "Entry")
    if entry.status == "VOID":
        raise ApiError("This entry is already cancelled", 409)
    reason = require_text(body(), "reason", "Reason")
    entry.status, entry.void_reason, entry.voided_by, entry.voided_at = "VOID", reason, me.id, utcnow()
    user = db.session.get(User, entry.user_id)
    audit.log(me.station_id, me.id, "MONEY TAKEN CANCELLED", "salary_advances", entry.id,
              {"status": "ACTIVE", "amount": money(entry.amount)}, {"status": "VOID"}, reason=reason)
    db.session.commit()
    return jsonify({"row": salary_row(user, me.station_id, entry.month)})


@bp.get("/staff/my-salary")
@login_required
def my_salary():
    """A staff member's own salary, money taken and what remains (read-only)."""
    me = current_user()
    month = _month(request.args.get("month"), me.station_id)
    return jsonify({"row": salary_row(me, me.station_id, month)})
