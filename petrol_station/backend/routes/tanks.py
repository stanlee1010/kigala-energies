"""Storage tanks. Levels are manager-only; staff only get tank names (to choose where a refill went)."""
from flask import Blueprint, jsonify

from ..auth import current_user, login_required, manager_required
from ..extensions import db
from ..models import FuelType, Shift, Tank
from ..services import audit, tanks
from .common import ApiError, body, decimal_field, get_or_404, require_text

bp = Blueprint("tanks", __name__, url_prefix="/api/tanks")


@bp.get("")
@login_required
def list_tanks():
    me = current_user()
    if me.is_manager:
        all_tanks = Tank.query.filter_by(station_id=me.station_id).order_by(Tank.fuel_type_id, Tank.tank_number).all()
        return jsonify({"tanks": tanks.tank_overview(me.station_id),
                        "all": [dict(t.to_dict(), fuel=t.fuel_type.name) for t in all_tanks]})
    # Staff: names only — no quantities.
    return jsonify({"tanks": [{"id": t.id, "name": t.name, "fuel_type_id": t.fuel_type_id, "fuel": t.fuel_type.name}
                              for t in tanks.active_tanks(me.station_id)]})


@bp.put("/count/<int:fuel_type_id>")
@manager_required
def set_count(fuel_type_id):
    me = current_user()
    fuel = get_or_404(FuelType, fuel_type_id, label="Fuel type")
    try:
        target = int(body().get("count"))
    except (TypeError, ValueError):
        raise ApiError("Number of tanks must be a whole number")
    before = len(tanks.active_tanks(me.station_id, fuel.id))
    try:
        added, removed = tanks.set_tank_count(me.station_id, fuel.id, target)
    except ValueError as exc:
        raise ApiError(str(exc), 409 if "empty" in str(exc) else 400)
    if added or removed:
        audit.log(me.station_id, me.id, "MANAGER CHANGED NUMBER OF TANKS", "tanks", None,
                  {"fuel": fuel.name, "tanks": before},
                  {"fuel": fuel.name, "tanks": target, "added": added, "removed": removed})
    db.session.commit()
    return jsonify({"fuel": fuel.name, "tanks": target, "added": added, "removed": removed})


@bp.put("/<int:tank_id>")
@manager_required
def update_tank(tank_id):
    me = current_user()
    tank = get_or_404(Tank, tank_id, me.station_id, "Tank")
    data = body()
    before = audit.snapshot(tank, ["name", "capacity_litres"])
    if "name" in data:
        name = require_text(data, "name", "Tank name", 60)
        if Tank.query.filter(Tank.station_id == me.station_id, Tank.id != tank.id, Tank.name == name).first():
            raise ApiError("Another tank already has that name")
        tank.name = name
    if "capacity_litres" in data:
        capacity = decimal_field(data, "capacity_litres", "Capacity", minimum=1)
        level = tanks.tank_level(tank)
        if capacity < level:
            raise ApiError(f"Capacity cannot be less than the fuel now in the tank ({level:,.0f} L)")
        tank.capacity_litres = capacity
    old, new = audit.changed_fields(before, audit.snapshot(tank, ["name", "capacity_litres"]))
    if new:
        audit.log(me.station_id, me.id, "MANAGER EDITED TANK", "tanks", tank.id, old, new)
    db.session.commit()
    return jsonify({"tank": tank.to_dict()})


@bp.get("/shift/<int:shift_id>")
@manager_required
def shift_tanks(shift_id):
    """Per tank for a shift: opening, refilled, sold by each pump, closing."""
    me = current_user()
    shift = get_or_404(Shift, shift_id, me.station_id, "Shift")
    return jsonify({"shift": {"id": shift.id, "label": shift.label, "status": shift.status},
                    "tanks": tanks.shift_breakdown(shift)})
