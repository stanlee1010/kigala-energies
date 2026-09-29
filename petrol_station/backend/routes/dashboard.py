"""Manager dashboard, charts, server clock and station settings."""
import time
from datetime import date

from flask import Blueprint, current_app, jsonify, request

from ..auth import current_user, login_required, manager_required
from ..extensions import db
from ..models import Alert, Expense, FuelRefill, Shift, Station, StockThreshold
from ..services import audit
from ..services.clock import now_local, paused_at, today_local
from ..services.finance import chart_series, financial_summary
from ..services.monitor import run_checks
from ..services.periods import period_status
from ..services.settings import EDITABLE_KEYS, get_settings, set_setting
from ..services.shifts import current_shift
from ..services.stock import current_stock
from ..services.cash import day_money
from ..services.tanks import tank_overview
from .common import ApiError, body, optional_text

bp = Blueprint("dashboard", __name__, url_prefix="/api")

_last_check = {}
CHECK_EVERY_SECONDS = 60


def run_checks_throttled(station_id):
    """Make the dashboard real-time even if the background scheduler is disabled."""
    now = time.monotonic()
    if now - _last_check.get(station_id, 0) >= CHECK_EVERY_SECONDS:
        _last_check[station_id] = now
        run_checks(station_id)


@bp.get("/time")
@login_required
def server_time():
    me = current_user()
    now = now_local(me.station_id)
    shift = current_shift(me.station_id)
    db.session.commit()
    settings = get_settings(me.station_id)
    return jsonify({"now": now.isoformat(timespec="seconds"), "timezone": settings["timezone"],
                    "currency": settings["currency"], "current_shift": shift.label,
                    "current_shift_type": shift.shift_type,
                    "paused": paused_at(me.station_id) is not None})


@bp.get("/dashboard/manager")
@manager_required
def manager_dashboard():
    me = current_user()
    run_checks_throttled(me.station_id)
    today = today_local(me.station_id)
    status = period_status(me.station_id)
    period = status["current_period"]
    thresholds = {t.fuel_type_id: t.minimum_litres for t in StockThreshold.query.filter_by(station_id=me.station_id)}
    stock_items = current_stock(me.station_id)
    for item in stock_items:
        item["minimum_litres"] = thresholds.get(item["fuel_type_id"])
        item["is_low"] = item["minimum_litres"] is not None and item["litres"] <= item["minimum_litres"]
    open_alerts = Alert.query.filter_by(station_id=me.station_id, is_resolved=False).all()
    order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    open_alerts.sort(key=lambda a: (order.get(a.severity, 9), -a.id))
    shift = current_shift(me.station_id)
    db.session.commit()
    return jsonify({
        "today": today,
        "today_summary": financial_summary(me.station_id, today, today),
        "period_to_date": financial_summary(me.station_id, date.fromisoformat(period["start_date"]), today),
        "period": status,
        "stock": stock_items,
        "tanks": tank_overview(me.station_id),
        # The day of the shift running now (at 02:00 that is still yesterday's Night shift's day).
        "money_today": day_money(me.station_id, shift.shift_date),
        "alerts": [a.to_dict() for a in open_alerts[:50]],
        "alert_count": len(open_alerts),
        "current_shift": {"id": shift.id, "label": shift.label, "status": shift.status,
                          "staff": [a.user.full_name for a in shift.staff]},
        "pending": {
            "shifts_to_review": Shift.query.filter_by(station_id=me.station_id, status="SUBMITTED").count(),
            "refills_to_approve": FuelRefill.query.filter_by(station_id=me.station_id, status="CONFIRMED").count(),
            "expenses_to_approve": Expense.query.filter_by(station_id=me.station_id, status="PENDING").count(),
        },
    })


@bp.get("/dashboard/charts")
@manager_required
def charts():
    me = current_user()
    granularity = request.args.get("granularity", "daily")
    try:
        return jsonify(chart_series(me.station_id, granularity, today_local(me.station_id)))
    except ValueError as exc:
        raise ApiError(str(exc))


@bp.get("/settings")
@login_required
def get_station_settings():
    me = current_user()
    station = db.session.get(Station, me.station_id)
    return jsonify({"station": station.to_dict(), "settings": get_settings(me.station_id),
                    "demo": bool(current_app.config.get("DEMO_MODE"))})


@bp.put("/settings")
@manager_required
def update_settings():
    me = current_user()
    data = body()
    station = db.session.get(Station, me.station_id)
    changes_old, changes_new = {}, {}
    for field in ("name", "address", "phone"):
        if field in data:
            value = optional_text(data, field, 255)
            if field == "name" and not value:
                raise ApiError("Station name is required")
            if getattr(station, field) != value:
                changes_old[field], changes_new[field] = getattr(station, field), value
                setattr(station, field, value)
    for key, value in (data.get("settings") or {}).items():
        if key not in EDITABLE_KEYS:
            continue
        try:
            old, new = set_setting(me.station_id, key, value, me.id)
        except ValueError as exc:
            raise ApiError(str(exc))
        if old != new:
            changes_old[key], changes_new[key] = old, new
    settings = get_settings(me.station_id)
    if changes_new:
        audit.log(me.station_id, me.id, "MANAGER CHANGED SETTINGS", "system_settings", None, changes_old, changes_new)
    db.session.commit()
    return jsonify({"station": station.to_dict(), "settings": settings})
