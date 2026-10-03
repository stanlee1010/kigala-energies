"""DIRECTOR portal: sales and profit for one location or all locations (day, week, month, year),
adding / removing managers and choosing their stations, and adding locations.

The director only views figures and manages managers; station operations stay with managers.
"""
import re
from datetime import date, timedelta

from flask import Blueprint, jsonify, request

from ..auth import current_user, director_required
from ..calculations import ZERO, D, money
from ..extensions import db
from ..models import Pump, Station, User, UserStation
from ..services import audit
from ..services.clock import today_local
from ..services.finance import chart_series, financial_summary
from ..services.settings import get_setting
from ..services.stations import create_station, set_manager_stations
from .common import ApiError, body, optional_text, require_text, validate_password

bp = Blueprint("director", __name__, url_prefix="/api/director")

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,60}$")
SUM_KEYS = ("fuel_sold_litres", "revenue", "cost_of_fuel_sold", "gross_profit", "operating_expenses", "net_profit")


def _selected_stations(raw):
    active = Station.query.filter_by(is_active=True).order_by(Station.id).all()
    if raw in (None, "", "all"):
        return active
    station = db.session.get(Station, int(raw))
    if station is None:
        raise ApiError("Location not found", 404)
    return [station]


def _periods(today):
    return {
        "today": (today, today),
        "week": (today - timedelta(days=today.weekday()), today),
        "month": (today.replace(day=1), today),
        "year": (date(today.year, 1, 1), today),
    }


def _add(total, s):
    for k in SUM_KEYS:
        total[k] = total.get(k, ZERO) + D(s[k], ZERO)
    fuels = total.setdefault("by_fuel", {})
    for f in s["by_fuel"]:
        row = fuels.setdefault(f["fuel"], {"fuel": f["fuel"], "color": f["color"], "litres": ZERO, "sales": ZERO})
        row["litres"] += D(f["sold_qty"], ZERO)
        row["sales"] += D(f["sales_revenue"], ZERO)


def _finish(total):
    out = {k: money(total.get(k, ZERO)) if k != "fuel_sold_litres" else total.get(k, ZERO) for k in SUM_KEYS}
    out["by_fuel"] = [f for f in total.get("by_fuel", {}).values() if f["litres"] or f["sales"]]
    out["is_loss"] = out["net_profit"] < 0
    return out


@bp.get("/overview")
@director_required
def overview():
    """Sales and profit today / this week / this month / this year, overall and per location."""
    stations = _selected_stations(request.args.get("station"))
    totals = {name: {} for name in ("today", "week", "month", "year")}
    per_station = []
    for st in stations:
        row = {"id": st.id, "name": st.name, "currency": get_setting(st.id, "currency")}
        for name, (start, end) in _periods(today_local(st.id)).items():
            s = financial_summary(st.id, start, end)
            _add(totals[name], s)
            row[name] = {"revenue": s["revenue"], "litres": s["fuel_sold_litres"], "net_profit": s["net_profit"],
                         "gross_profit": s["gross_profit"], "expenses": s["operating_expenses"]}
        per_station.append(row)
    return jsonify({
        "locations": [{"id": s.id, "name": s.name} for s in Station.query.filter_by(is_active=True).order_by(Station.id)],
        "selected": request.args.get("station") or "all",
        "currency": get_setting(stations[0].id, "currency") if stations else "TZS",
        "periods": {name: _finish(t) for name, t in totals.items()},
        "by_location": per_station,
    })


@bp.get("/charts")
@director_required
def charts():
    """Sales, fuel sold, expenses and profit per day / week / month / year for the chosen location(s)."""
    stations = _selected_stations(request.args.get("station"))
    granularity = request.args.get("granularity", "daily")
    merged = None
    for st in stations:
        try:
            s = chart_series(st.id, granularity, today_local(st.id))
        except ValueError as exc:
            raise ApiError(str(exc))
        if merged is None:
            merged = s
        else:
            for key in ("revenue", "fuel_sold", "expenses", "cost_of_fuel_sold", "profit"):
                merged[key] = [a + b for a, b in zip(merged[key], s[key])]
    return jsonify(merged or {"labels": [], "revenue": [], "fuel_sold": [], "expenses": [], "cost_of_fuel_sold": [],
                              "profit": []})


# ---------------------------------------------------------------- managers

def _manager_dict(u, names):
    ids = [r.station_id for r in UserStation.query.filter_by(user_id=u.id)]
    data = u.to_dict()
    data.update(station_ids=ids, stations=[names.get(i, "?") for i in ids], current_station=names.get(u.station_id))
    return data


def _manager(user_id):
    user = db.session.get(User, user_id)
    if user is None or user.role != "MANAGER":
        raise ApiError("Manager not found", 404)
    return user


def _station_ids(data):
    ids = sorted({int(s) for s in (data.get("station_ids") or [])})
    if not ids:
        raise ApiError("Choose at least one station for the manager")
    found = {s.id for s in Station.query.filter(Station.id.in_(ids))}
    if set(ids) != found:
        raise ApiError("One of the stations does not exist")
    return ids


@bp.get("/managers")
@director_required
def list_managers():
    names = {s.id: s.name for s in Station.query}
    rows = User.query.filter_by(role="MANAGER").order_by(User.is_active.desc(), User.full_name).all()
    return jsonify({"managers": [_manager_dict(u, names) for u in rows],
                    "stations": [{"id": s.id, "name": s.name, "is_active": s.is_active}
                                 for s in Station.query.order_by(Station.id)]})


@bp.post("/managers")
@director_required
def create_manager():
    me = current_user()
    data = body()
    username = require_text(data, "username").lower()
    if not USERNAME_RE.match(username):
        raise ApiError("Username may contain only letters, numbers, dot, dash and underscore (min 3)")
    if User.query.filter(db.func.lower(User.username) == username).first():
        raise ApiError("That username is already taken")
    ids = _station_ids(data)
    user = User(station_id=ids[0], username=username, full_name=require_text(data, "full_name", "Full name"),
                phone=optional_text(data, "phone"), email=optional_text(data, "email"), role="MANAGER",
                must_change_password=True, created_by=me.id)
    user.set_password(validate_password(data.get("password")))
    db.session.add(user)
    db.session.flush()
    set_manager_stations(user, ids)
    audit.log(ids[0], me.id, "DIRECTOR ADDED MANAGER", "users", user.id,
              new={"username": username, "full_name": user.full_name, "station_ids": ids})
    db.session.commit()
    return jsonify({"manager": _manager_dict(user, {s.id: s.name for s in Station.query})}), 201


@bp.put("/managers/<int:user_id>")
@director_required
def update_manager(user_id):
    me = current_user()
    user = _manager(user_id)
    data = body()
    before = {"full_name": user.full_name, "phone": user.phone, "email": user.email,
              "station_ids": [r.station_id for r in UserStation.query.filter_by(user_id=user.id)]}
    if "full_name" in data:
        user.full_name = require_text(data, "full_name", "Full name")
    if "phone" in data:
        user.phone = optional_text(data, "phone")
    if "email" in data:
        user.email = optional_text(data, "email")
    if "station_ids" in data:
        set_manager_stations(user, _station_ids(data))
    after = {"full_name": user.full_name, "phone": user.phone, "email": user.email,
             "station_ids": [r.station_id for r in UserStation.query.filter_by(user_id=user.id)]}
    old, new = audit.changed_fields(before, after)
    if new:
        audit.log(user.station_id, me.id, "DIRECTOR CHANGED MANAGER", "users", user.id, old, new)
    db.session.commit()
    return jsonify({"manager": _manager_dict(user, {s.id: s.name for s in Station.query})})


@bp.post("/managers/<int:user_id>/<any(disable, enable):action>")
@director_required
def toggle_manager(user_id, action):
    """Remove (disable) a manager's access, or give it back. History is kept."""
    me = current_user()
    user = _manager(user_id)
    user.is_active = action == "enable"
    audit.log(user.station_id, me.id, f"DIRECTOR {'ENABLED' if user.is_active else 'REMOVED'} MANAGER", "users",
              user.id, {"is_active": not user.is_active}, {"is_active": user.is_active},
              reason=optional_text(body(), "reason"))
    db.session.commit()
    return jsonify({"manager": _manager_dict(user, {s.id: s.name for s in Station.query})})


@bp.post("/managers/<int:user_id>/reset-password")
@director_required
def reset_manager_password(user_id):
    me = current_user()
    user = _manager(user_id)
    user.set_password(validate_password(body().get("password")))
    user.must_change_password, user.failed_login_count, user.locked_until = True, 0, None
    audit.log(user.station_id, me.id, "DIRECTOR RESET MANAGER PASSWORD", "users", user.id)
    db.session.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- stations

def _station_dict(s):
    return dict(s.to_dict(),
                managers=UserStation.query.filter_by(station_id=s.id).count(),
                staff=User.query.filter_by(station_id=s.id, role="STAFF", is_active=True).count(),
                pumps=Pump.query.filter_by(station_id=s.id, is_active=True).count(),
                currency=get_setting(s.id, "currency"), timezone=get_setting(s.id, "timezone"))


@bp.get("/stations")
@director_required
def list_stations():
    return jsonify({"stations": [_station_dict(s) for s in Station.query.order_by(Station.id)]})


@bp.post("/stations")
@director_required
def add_station():
    """A new location with the standard starting set-up; give a manager access to run it."""
    me = current_user()
    data = body()
    name = require_text(data, "name", "Station name", 150)
    if Station.query.filter(db.func.lower(Station.name) == name.lower()).first():
        raise ApiError("A station with that name already exists")
    first = Station.query.order_by(Station.id).first()
    station = create_station(name, address=optional_text(data, "address"), phone=optional_text(data, "phone", 40),
                             timezone=get_setting(first.id, "timezone") if first else "Africa/Dar_es_Salaam",
                             currency=get_setting(first.id, "currency") if first else "TZS")
    for manager_id in data.get("manager_ids") or []:
        manager = _manager(int(manager_id))
        set_manager_stations(manager, [r.station_id for r in UserStation.query.filter_by(user_id=manager.id)] + [station.id])
    audit.log(station.id, me.id, "DIRECTOR ADDED STATION", "stations", station.id, new={"name": name})
    db.session.commit()
    return jsonify({"station": _station_dict(station)}), 201


@bp.post("/stations/<int:station_id>/<any(remove, restore):action>")
@director_required
def remove_or_restore_station(station_id, action):
    """Remove a location from use (archived, never deleted: its sales, money and audit history are kept)
    or restore it exactly as it was. Its staff cannot log in while it is removed."""
    me = current_user()
    station = db.session.get(Station, station_id)
    if station is None:
        raise ApiError("Station not found", 404)
    reason = optional_text(body(), "reason", 500)
    if action == "remove":
        if not station.is_active:
            raise ApiError("That station is already removed")
        if Station.query.filter_by(is_active=True).count() <= 1:
            raise ApiError("This is the last station — add another one before removing it")
        station.is_active = False
        # Managers working on it move to another of their stations.
        from ..services.stations import accessible_stations
        for manager in User.query.filter_by(role="MANAGER", station_id=station.id).all():
            others = accessible_stations(manager)
            if others:
                manager.station_id = others[0].id
    else:
        if station.is_active:
            raise ApiError("That station is already in use")
        station.is_active = True
    audit.log(station.id, me.id, f"DIRECTOR {'REMOVED' if action == 'remove' else 'RESTORED'} STATION", "stations",
              station.id, {"is_active": action != "remove"}, {"is_active": action == "restore", "name": station.name},
              reason=reason)
    db.session.commit()
    return jsonify({"station": _station_dict(station)})


@bp.put("/stations/<int:station_id>")
@director_required
def update_station(station_id):
    me = current_user()
    station = db.session.get(Station, station_id)
    if station is None:
        raise ApiError("Station not found", 404)
    data = body()
    before = {"name": station.name, "address": station.address, "phone": station.phone, "is_active": station.is_active}
    if "name" in data:
        station.name = require_text(data, "name", "Station name", 150)
    if "address" in data:
        station.address = optional_text(data, "address")
    if "phone" in data:
        station.phone = optional_text(data, "phone", 40)
    if "is_active" in data:
        if not data["is_active"] and Station.query.filter_by(is_active=True).count() <= 1:
            raise ApiError("At least one station must stay open")
        station.is_active = bool(data["is_active"])
    after = {"name": station.name, "address": station.address, "phone": station.phone, "is_active": station.is_active}
    old, new = audit.changed_fields(before, after)
    if new:
        audit.log(station.id, me.id, "DIRECTOR CHANGED STATION", "stations", station.id, old, new)
    db.session.commit()
    return jsonify({"station": _station_dict(station)})
