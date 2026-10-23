"""PUMP MANAGEMENT, fuel types, suppliers."""
from flask import Blueprint, jsonify

from ..auth import current_user, login_required, manager_required
from ..extensions import db
from ..models import FuelType, Pump, PumpReading, Shift, StockThreshold, Supplier
from ..services import audit
from ..services.tanks import default_tank, resolve_tank
from .common import ApiError, body, color_field, get_or_404, optional_email, optional_phone, optional_text, require_text

bp = Blueprint("pumps", __name__, url_prefix="/api")

MAX_PUMPS_PER_FUEL = 50
UNFINISHED_SHIFT_STATUSES = ("OPEN", "FLAGGED", "SUBMITTED")


def _pump_dict(pump):
    data = pump.to_dict()
    data["fuel_type"] = pump.fuel_type.name
    data["fuel_code"] = pump.fuel_type.code
    data["fuel_color"] = pump.fuel_type.color
    data["tank"] = pump.tank.name if pump.tank else None
    return data


@bp.get("/pumps")
@login_required
def list_pumps():
    me = current_user()
    pumps = Pump.query.filter_by(station_id=me.station_id).order_by(Pump.fuel_type_id, Pump.pump_number).all()
    return jsonify({"pumps": [_pump_dict(p) for p in pumps]})


def _apply_pump(pump, data, station_id):
    fuel = get_or_404(FuelType, int(data.get("fuel_type_id") or 0), label="Fuel type")
    try:
        number = int(data.get("pump_number") or 0)
    except ValueError:
        number = 0
    if number <= 0:
        raise ApiError("Pump number must be a positive whole number")
    name = optional_text(data, "pump_name", 60) or f"{fuel.name} Pump {number}"
    clash = Pump.query.filter(Pump.station_id == station_id, Pump.id != (pump.id or 0),
                              db.or_(Pump.pump_name == name,
                                     db.and_(Pump.fuel_type_id == fuel.id, Pump.pump_number == number))).first()
    if clash:
        raise ApiError(f"A pump with that name or number already exists ({clash.pump_name})")
    # The tank the pump draws from (must hold the pump's fuel; defaults to that fuel's first tank).
    keep_tank = pump.tank_id if pump.fuel_type_id == fuel.id else None
    try:
        tank = resolve_tank(station_id, fuel.id, data.get("tank_id") or keep_tank)
    except ValueError as exc:
        raise ApiError(str(exc))
    pump.fuel_type_id, pump.pump_number, pump.pump_name, pump.tank_id = fuel.id, number, name, tank.id


@bp.post("/pumps")
@manager_required
def create_pump():
    me = current_user()
    pump = Pump(station_id=me.station_id, is_active=True)
    _apply_pump(pump, body(), me.station_id)
    db.session.add(pump)
    db.session.flush()
    audit.log(me.station_id, me.id, "MANAGER ADDED PUMP", "pumps", pump.id, new=audit.snapshot(pump))
    db.session.commit()
    return jsonify({"pump": _pump_dict(pump)}), 201


@bp.put("/pumps/<int:pump_id>")
@manager_required
def update_pump(pump_id):
    me = current_user()
    pump = get_or_404(Pump, pump_id, me.station_id, "Pump")
    before = audit.snapshot(pump)
    data = body()
    _apply_pump(pump, {"fuel_type_id": data.get("fuel_type_id", pump.fuel_type_id),
                       "pump_number": data.get("pump_number", pump.pump_number),
                       "pump_name": data.get("pump_name", pump.pump_name),
                       "tank_id": data.get("tank_id")}, me.station_id)
    old, new = audit.changed_fields(before, audit.snapshot(pump))
    audit.log(me.station_id, me.id, "MANAGER EDITED PUMP", "pumps", pump.id, old, new)
    db.session.commit()
    return jsonify({"pump": _pump_dict(pump)})


@bp.post("/pumps/<int:pump_id>/<any(activate, deactivate):action>")
@manager_required
def toggle_pump(pump_id, action):
    me = current_user()
    pump = get_or_404(Pump, pump_id, me.station_id, "Pump")
    pump.is_active = action == "activate"
    audit.log(me.station_id, me.id, f"MANAGER {'ACTIVATED' if pump.is_active else 'DEACTIVATED'} PUMP",
              "pumps", pump.id, {"is_active": not pump.is_active}, {"is_active": pump.is_active})
    db.session.commit()
    return jsonify({"pump": _pump_dict(pump)})


@bp.put("/pumps/count/<int:fuel_type_id>")
@manager_required
def set_pump_count(fuel_type_id):
    """Set how many active pumps a fuel has.

    Increasing re-activates removed pumps first (lowest number first), then creates new ones.
    Decreasing deactivates the highest-numbered pumps; pumps are never deleted so their
    history stays. A pump with an unfinished reading in an open shift cannot be removed.
    """
    from .shifts import _ensure_reading  # shared helper; imported here to keep module load order simple

    me = current_user()
    fuel = get_or_404(FuelType, fuel_type_id, label="Fuel type")
    try:
        target = int(body().get("count"))
    except (TypeError, ValueError):
        raise ApiError("Number of pumps must be a whole number")
    if target < 0 or target > MAX_PUMPS_PER_FUEL:
        raise ApiError(f"Number of pumps must be between 0 and {MAX_PUMPS_PER_FUEL}")

    pumps = Pump.query.filter_by(station_id=me.station_id, fuel_type_id=fuel.id).order_by(Pump.pump_number).all()
    active = [p for p in pumps if p.is_active]
    before = len(active)
    added, removed = [], []
    open_shifts = Shift.query.filter(Shift.station_id == me.station_id,
                                     Shift.status.in_(UNFINISHED_SHIFT_STATUSES)).all()

    if target > before:
        for pump in (p for p in pumps if not p.is_active):
            if len(active) + len(added) >= target:
                break
            pump.is_active = True
            added.append(pump)
        next_number = max((p.pump_number for p in pumps), default=0) + 1
        taken = {p.pump_name for p in Pump.query.filter_by(station_id=me.station_id)}
        while len(active) + len(added) < target:
            name = f"{fuel.name} Pump {next_number}"
            while name in taken:
                next_number += 1
                name = f"{fuel.name} Pump {next_number}"
            pump = Pump(station_id=me.station_id, fuel_type_id=fuel.id, pump_number=next_number,
                        pump_name=name, is_active=True, tank_id=default_tank(me.station_id, fuel.id).id)
            db.session.add(pump)
            db.session.flush()
            taken.add(name)
            added.append(pump)
            next_number += 1
        db.session.flush()
        for shift in open_shifts:            # new pumps can be read in the shift that is running now
            if shift.status in ("OPEN", "FLAGGED"):
                for pump in added:
                    _ensure_reading(shift, pump, me.id)

    elif target < before:
        to_remove = sorted(active, key=lambda p: p.pump_number, reverse=True)[:before - target]
        blocked = []
        for pump in to_remove:
            for shift in open_shifts:
                reading = PumpReading.query.filter_by(shift_id=shift.id, pump_id=pump.id).first()
                if reading is None:
                    continue
                if reading.opening_recorded_at is None and reading.closing_meter is None:
                    db.session.delete(reading)   # empty placeholder, no sales recorded yet
                elif reading.closing_meter is None:
                    blocked.append(f"{pump.pump_name} ({shift.label})")
        if blocked:
            raise ApiError("These pumps have readings in a shift that is still open. Enter their closing reading "
                           "or close the shift first: " + ", ".join(blocked), 409)
        for pump in to_remove:
            pump.is_active = False
            removed.append(pump)

    if added or removed:
        audit.log(me.station_id, me.id, "MANAGER CHANGED NUMBER OF PUMPS", "pumps", None,
                  {"fuel": fuel.name, "active_pumps": before},
                  {"fuel": fuel.name, "active_pumps": target, "added": [p.pump_name for p in added],
                   "deactivated": [p.pump_name for p in removed]})
    db.session.commit()
    return jsonify({"fuel": fuel.name, "active_pumps": target,
                    "added": [p.pump_name for p in added], "deactivated": [p.pump_name for p in removed]})


# ------------------------------------------------------------- fuel types

@bp.get("/fuel-types")
@login_required
def list_fuel_types():
    me = current_user()
    thresholds = {t.fuel_type_id: t for t in StockThreshold.query.filter_by(station_id=me.station_id)}
    result = []
    for fuel in FuelType.query.order_by(FuelType.id).all():
        data = fuel.to_dict()
        t = thresholds.get(fuel.id)
        data["minimum_litres"] = float(t.minimum_litres) if t else None
        result.append(data)
    return jsonify({"fuel_types": result})


@bp.post("/fuel-types")
@manager_required
def create_fuel_type():
    me = current_user()
    data = body()
    code = require_text(data, "code", "Code", 20).upper().replace(" ", "_")
    if FuelType.query.filter_by(code=code).first():
        raise ApiError("A fuel type with that code already exists")
    fuel = FuelType(code=code, name=require_text(data, "name", "Name", 60),
                    color=color_field(data) or "#64748b")
    db.session.add(fuel)
    db.session.flush()
    audit.log(me.station_id, me.id, "MANAGER ADDED FUEL TYPE", "fuel_types", fuel.id, new=audit.snapshot(fuel))
    db.session.commit()
    return jsonify({"fuel_type": fuel.to_dict()}), 201


@bp.put("/fuel-types/<int:fuel_id>")
@manager_required
def update_fuel_type(fuel_id):
    me = current_user()
    fuel = get_or_404(FuelType, fuel_id, label="Fuel type")
    before = audit.snapshot(fuel)
    data = body()
    if "name" in data:
        fuel.name = require_text(data, "name", "Name", 60)
    if "color" in data:
        fuel.color = color_field(data) or fuel.color
    if "is_active" in data:
        fuel.is_active = bool(data["is_active"])
    old, new = audit.changed_fields(before, audit.snapshot(fuel))
    audit.log(me.station_id, me.id, "MANAGER EDITED FUEL TYPE", "fuel_types", fuel.id, old, new)
    db.session.commit()
    return jsonify({"fuel_type": fuel.to_dict()})


# -------------------------------------------------------------- suppliers

@bp.get("/suppliers")
@login_required
def list_suppliers():
    me = current_user()
    rows = Supplier.query.filter_by(station_id=me.station_id).order_by(Supplier.name).all()
    return jsonify({"suppliers": [s.to_dict() for s in rows]})


@bp.post("/suppliers")
@manager_required
def create_supplier():
    me = current_user()
    data = body()
    name = require_text(data, "name", "Supplier name", 150)
    if Supplier.query.filter_by(station_id=me.station_id, name=name).first():
        raise ApiError("That supplier already exists")
    supplier = Supplier(station_id=me.station_id, name=name, phone=optional_phone(data),
                        email=optional_email(data))
    db.session.add(supplier)
    db.session.flush()
    audit.log(me.station_id, me.id, "MANAGER ADDED SUPPLIER", "suppliers", supplier.id, new=audit.snapshot(supplier))
    db.session.commit()
    return jsonify({"supplier": supplier.to_dict()}), 201
