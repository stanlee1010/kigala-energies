"""Fuel prices (incl. first-Wednesday review), thresholds, stock, reporting periods."""
from datetime import datetime

from flask import Blueprint, jsonify, request

from ..auth import current_user, login_required, manager_required
from ..calculations import litres
from ..extensions import db
from ..models import (ADJUSTMENT_TYPES, FuelPrice, FuelStock, FuelType, ReportingPeriod, Shift,
                      StockAdjustment, StockThreshold, User, utcnow)
from ..services import alerts, audit, stock
from ..services.clock import now_local
from ..services.finance import financial_summary
from ..services.periods import current_price, ensure_periods, period_status
from ..services.shifts import current_shift
from ..services.tanks import OverCapacity, check_capacity, resolve_tank
from .common import ApiError, body, date_field, decimal_field, get_or_404, optional_text, require_text

bp = Blueprint("fuel", __name__, url_prefix="/api")


# ------------------------------------------------------------------ prices

@bp.get("/prices")
@login_required
def prices():
    me = current_user()
    status = period_status(me.station_id)
    db.session.commit()
    history = []
    if me.is_manager:
        names = {u.id: u.full_name for u in User.query}
        fuels = {f.id: f.name for f in FuelType.query.all()}
        for row in (FuelPrice.query.filter_by(station_id=me.station_id)
                    .order_by(FuelPrice.created_at.desc()).limit(200)):
            data = row.to_dict()
            data["fuel"] = fuels.get(row.fuel_type_id)
            data["set_by_name"] = names.get(row.set_by)
            history.append(data)
    return jsonify({"current": status["current_prices"], "history": history,
                    "price_review_required": status["price_review_required"],
                    "is_first_wednesday": status["is_first_wednesday"]})


def _record_price(me, fuel, new_price, status, period, note):
    previous = current_price(me.station_id, fuel.id)
    row = FuelPrice(station_id=me.station_id, fuel_type_id=fuel.id, price_per_litre=new_price,
                    previous_price=previous.price_per_litre if previous else None,
                    confirmation_status=status, reporting_period_id=period.id, set_by=me.id, note=note)
    db.session.add(row)
    db.session.flush()
    action = "MANAGER CHANGED PRICE" if status == "UPDATED" else "MANAGER KEPT PRICE"
    audit.log(me.station_id, me.id, action, "fuel_prices", row.id,
              {"fuel": fuel.name, "price_per_litre": previous.price_per_litre if previous else None},
              {"fuel": fuel.name, "price_per_litre": new_price, "confirmation_status": status}, reason=note)
    return row


@bp.post("/prices")
@manager_required
def set_price():
    """Only the manager changes selling prices."""
    me = current_user()
    data = body()
    fuel = get_or_404(FuelType, int(data.get("fuel_type_id") or 0), label="Fuel type")
    price = decimal_field(data, "price_per_litre", "Price per litre", minimum=0, allow_zero=False)
    period = ensure_periods(me.station_id)
    row = _record_price(me, fuel, price, "UPDATED", period, optional_text(data, "note"))
    db.session.commit()
    return jsonify({"price": row.to_dict()}), 201


@bp.post("/prices/review")
@manager_required
def review_prices():
    """First-Wednesday review: manager must KEEP or UPDATE every active fuel price."""
    me = current_user()
    data = body()
    decisions = {int(d.get("fuel_type_id")): d for d in data.get("decisions") or []}
    fuels = FuelType.query.filter_by(is_active=True).order_by(FuelType.id).all()
    missing = [f.name for f in fuels if f.id not in decisions]
    if missing:
        raise ApiError(f"Please choose Keep or Update for: {', '.join(missing)}")
    period = ensure_periods(me.station_id)
    for fuel in fuels:
        decision = decisions[fuel.id]
        action = str(decision.get("action", "")).upper()
        previous = current_price(me.station_id, fuel.id)
        if action == "KEEP":
            if previous is None:
                raise ApiError(f"{fuel.name} has no current price; please enter one")
            _record_price(me, fuel, previous.price_per_litre, "KEPT", period, "Existing price confirmed unchanged")
        elif action == "UPDATE":
            price = decimal_field(decision, "price_per_litre", f"{fuel.name} price", minimum=0, allow_zero=False)
            _record_price(me, fuel, price, "UPDATED", period, optional_text(decision, "note"))
        else:
            raise ApiError(f"Invalid decision for {fuel.name}")
    period.price_reviewed_at = utcnow()
    period.price_reviewed_by = me.id
    alerts.resolve_by_key(me.station_id, f"PRICE_REVIEW:{period.id}", "Manager reviewed fuel prices", me.id)
    audit.log(me.station_id, me.id, "MANAGER COMPLETED PRICE REVIEW", "reporting_periods", period.id)
    db.session.commit()
    return jsonify({"ok": True})


# -------------------------------------------------------------- thresholds

@bp.put("/thresholds/<int:fuel_type_id>")
@manager_required
def set_threshold(fuel_type_id):
    me = current_user()
    fuel = get_or_404(FuelType, fuel_type_id, label="Fuel type")
    minimum = decimal_field(body(), "minimum_litres", "Minimum litres", minimum=0)
    row = StockThreshold.query.filter_by(station_id=me.station_id, fuel_type_id=fuel.id).first()
    old = row.minimum_litres if row else None
    if row is None:
        row = StockThreshold(station_id=me.station_id, fuel_type_id=fuel.id)
        db.session.add(row)
    row.minimum_litres, row.updated_by = minimum, me.id
    db.session.flush()
    audit.log(me.station_id, me.id, "MANAGER CHANGED STOCK THRESHOLD", "stock_thresholds", row.id,
              {"fuel": fuel.name, "minimum_litres": old}, {"fuel": fuel.name, "minimum_litres": minimum})
    db.session.commit()
    return jsonify({"threshold": row.to_dict()})


# ------------------------------------------------------------------- stock

@bp.get("/stock/current")
@manager_required
def stock_current():
    me = current_user()
    thresholds = {t.fuel_type_id: t.minimum_litres for t in StockThreshold.query.filter_by(station_id=me.station_id)}
    items = stock.current_stock(me.station_id)
    for item in items:
        item["minimum_litres"] = thresholds.get(item["fuel_type_id"])
        item["is_low"] = item["minimum_litres"] is not None and item["litres"] <= item["minimum_litres"]
    return jsonify({"stock": items})


@bp.get("/stock/history")
@manager_required
def stock_history():
    me = current_user()
    start = date_field(request.args.get("start"), "Start", required=False)
    end = date_field(request.args.get("end"), "End", required=False)
    q = (db.session.query(FuelStock, Shift, FuelType)
         .join(Shift, Shift.id == FuelStock.shift_id).join(FuelType, FuelType.id == FuelStock.fuel_type_id)
         .filter(Shift.station_id == me.station_id))
    if start:
        q = q.filter(Shift.shift_date >= start)
    if end:
        q = q.filter(Shift.shift_date <= end)
    rows = q.order_by(Shift.shift_date.desc(), Shift.sequence.desc(), FuelType.id).limit(300).all()
    return jsonify({"rows": [dict(r.to_dict(), shift_label=s.label, shift_status=s.status, fuel=f.name)
                             for r, s, f in rows]})


@bp.post("/stock/adjustments")
@manager_required
def create_adjustment():
    me = current_user()
    data = body()
    fuel = get_or_404(FuelType, int(data.get("fuel_type_id") or 0), label="Fuel type")
    kind = str(data.get("adjustment_type") or "").upper()
    if kind not in ADJUSTMENT_TYPES:
        raise ApiError(f"Adjustment type must be one of {', '.join(ADJUSTMENT_TYPES)}")
    qty = decimal_field(data, "quantity_litres", "Quantity")
    cost = decimal_field(data, "cost_per_litre", "Cost per litre", required=kind == "OPENING_BALANCE", minimum=0)
    if kind == "OPENING_BALANCE" and qty < 0:
        raise ApiError("Opening balance cannot be negative")
    shift = None if kind == "OPENING_BALANCE" else current_shift(me.station_id)
    try:
        tank = resolve_tank(me.station_id, fuel.id, data.get("tank_id"))
        if qty > 0:
            check_capacity(tank, qty)
    except OverCapacity as exc:
        raise ApiError(str(exc), 400, title="TANK CAPACITY EXCEEDED")
    except ValueError as exc:
        raise ApiError(str(exc))
    adj = StockAdjustment(station_id=me.station_id, fuel_type_id=fuel.id, tank_id=tank.id,
                          shift_id=shift.id if shift else None,
                          adjustment_type=kind, quantity_litres=litres(qty), cost_per_litre=cost,
                          reason=require_text(data, "reason", "Reason", 500),
                          adjusted_at=now_local(me.station_id), created_by=me.id)
    db.session.add(adj)
    db.session.flush()
    if shift:
        stock.recompute(stock.ensure_stock_row(shift, fuel.id, me.id))
    audit.log(me.station_id, me.id, f"MANAGER STOCK {kind.replace('_', ' ')}", "stock_adjustments", adj.id,
              new=audit.snapshot(adj), reason=adj.reason)
    db.session.commit()
    return jsonify({"adjustment": adj.to_dict()}), 201


@bp.get("/stock/adjustments")
@manager_required
def list_adjustments():
    me = current_user()
    fuels = {f.id: f.name for f in FuelType.query.all()}
    rows = (StockAdjustment.query.filter_by(station_id=me.station_id)
            .order_by(StockAdjustment.adjusted_at.desc()).limit(200).all())
    return jsonify({"adjustments": [dict(a.to_dict(), fuel=fuels.get(a.fuel_type_id)) for a in rows]})


@bp.post("/stock/<int:row_id>/reconcile")
@manager_required
def reconcile_stock_row(row_id):
    """Pull confirmed refills/sales into a (possibly closed) shift's stock row."""
    me = current_user()
    row = get_or_404(FuelStock, row_id, me.station_id, "Stock record")
    before = audit.snapshot(row)
    stock.recompute(row)
    old, new = audit.changed_fields(before, audit.snapshot(row))
    audit.log(me.station_id, me.id, "MANAGER RECONCILED STOCK", "fuel_stock", row.id, old, new,
              reason=optional_text(body(), "reason"))
    db.session.commit()
    from ..services.monitor import check_refill_reconciliation
    check_refill_reconciliation(me.station_id)
    db.session.commit()
    return jsonify({"row": row.to_dict()})


# ---------------------------------------------------------------- periods

@bp.get("/periods/status")
@login_required
def periods_status():
    me = current_user()
    status = period_status(me.station_id)
    db.session.commit()
    if not me.is_manager:
        status = {k: status[k] for k in ("today", "is_first_wednesday", "current_prices")}
    return jsonify(status)


@bp.get("/periods")
@manager_required
def list_periods():
    me = current_user()
    ensure_periods(me.station_id)
    db.session.commit()
    rows = ReportingPeriod.query.filter_by(station_id=me.station_id).order_by(ReportingPeriod.start_date.desc()).all()
    return jsonify({"periods": [p.to_dict() for p in rows]})


@bp.get("/periods/<int:period_id>")
@manager_required
def period_detail(period_id):
    me = current_user()
    period = get_or_404(ReportingPeriod, period_id, me.station_id, "Reporting period")
    return jsonify({"period": period.to_dict(),
                    "summary": financial_summary(me.station_id, period.start_date, period.end_date)})


@bp.post("/periods/<int:period_id>/review")
@manager_required
def mark_reviewed(period_id):
    me = current_user()
    period = get_or_404(ReportingPeriod, period_id, me.station_id, "Reporting period")
    if period.status != "CLOSED":
        raise ApiError("Only a completed reporting period can be marked as reviewed")
    period.reviewed_at, period.reviewed_by = utcnow(), me.id
    alerts.resolve_by_key(me.station_id, f"MONTHLY_REVIEW:{period.id}", "Manager reviewed the period", me.id)
    audit.log(me.station_id, me.id, "MANAGER REVIEWED MONTHLY RESULTS", "reporting_periods", period.id)
    db.session.commit()
    return jsonify({"period": period.to_dict()})
