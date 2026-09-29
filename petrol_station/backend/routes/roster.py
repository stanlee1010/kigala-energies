"""Shift types (Morning / Afternoon / Night ...) and the regular staff roster."""
from flask import Blueprint, jsonify

from ..auth import current_user, login_required, manager_required
from ..extensions import db
from ..models import ShiftDefinition, User
from ..services import audit, roster
from ..services.monitor import ensure_upcoming_shifts
from ..services.shifts import (PRESETS, all_definitions, code_from_name, definition_by_code, definition_windows,
                               fill_planned_days, parse_time, reschedule_future_shifts, resequence)
from ..services.clock import today_local
from .common import ApiError, body, get_or_404, require_text

bp = Blueprint("roster", __name__, url_prefix="/api")


def _types_payload(station_id):
    windows = {d.id: (s, e) for d, s, e in definition_windows(station_id, today_local(station_id))}
    staff_counts = dict(db.session.query(User.default_shift_definition_id, db.func.count(User.id))
                        .filter(User.station_id == station_id, User.is_active.is_(True),
                                User.default_shift_definition_id.isnot(None))
                        .group_by(User.default_shift_definition_id).all())
    result = []
    for d in all_definitions(station_id):
        data = d.to_dict()
        data["start_time"] = d.start_time.strftime("%H:%M")
        if d.id in windows:
            start, end = windows[d.id]
            data["end_time"] = end.strftime("%H:%M")
            data["hours"] = round((end - start).total_seconds() / 3600, 2)
            data["ends_next_day"] = end.date() > start.date()
        data["staff_count"] = staff_counts.get(d.id, 0)
        result.append(data)
    active_staff = User.query.filter_by(station_id=station_id, is_active=True, role="STAFF").count()
    return {"shift_types": result, "active_staff": active_staff, "presets": list(PRESETS)}


def _after_change(me, action, old=None, new=None):
    """Validate, re-order, re-time/cancel upcoming shifts and re-apply the roster."""
    active = [d for d in all_definitions(me.station_id) if d.is_active]
    if not active:
        raise ApiError("At least one shift type must stay active")
    starts = [d.start_time for d in active]
    if len(set(starts)) != len(starts):
        raise ApiError("Two active shifts cannot start at the same time")
    resequence(me.station_id)
    cancelled = reschedule_future_shifts(me.station_id)
    ensure_upcoming_shifts(me.station_id)
    fill_planned_days(me.station_id)
    roster.sync_station(me.station_id)
    audit.log(me.station_id, me.id, action, "shift_definitions", None, old, new)
    db.session.commit()
    payload = _types_payload(me.station_id)
    payload["cancelled_shifts"] = cancelled
    return payload


@bp.get("/shift-types")
@login_required
def list_types():
    me = current_user()
    return jsonify(_types_payload(me.station_id))


@bp.post("/shift-types")
@manager_required
def create_type():
    me = current_user()
    data = body()
    name = require_text(data, "name", "Shift name", 60)
    code = code_from_name(name)
    existing = definition_by_code(me.station_id, code)
    try:
        start = parse_time(data.get("start_time"))
    except ValueError as exc:
        raise ApiError(str(exc))
    if existing and existing.is_active:
        raise ApiError(f"A shift called {existing.name} already exists")
    if existing:  # re-activate a type that was switched off earlier
        existing.name, existing.start_time, existing.is_active = name, start, True
    else:
        db.session.add(ShiftDefinition(station_id=me.station_id, code=code, name=name, start_time=start))
    db.session.flush()
    return jsonify(_after_change(me, "MANAGER ADDED SHIFT TYPE", new={"name": name, "start_time": start})), 201


@bp.put("/shift-types/<int:type_id>")
@manager_required
def update_type(type_id):
    me = current_user()
    d = get_or_404(ShiftDefinition, type_id, me.station_id, "Shift type")
    data = body()
    before = {"name": d.name, "start_time": d.start_time.strftime("%H:%M"), "is_active": d.is_active}
    if "name" in data:
        d.name = require_text(data, "name", "Shift name", 60)
    if "start_time" in data:
        try:
            d.start_time = parse_time(data["start_time"])
        except ValueError as exc:
            raise ApiError(str(exc))
    if "is_active" in data:
        d.is_active = bool(data["is_active"])
    after = {"name": d.name, "start_time": d.start_time.strftime("%H:%M"), "is_active": d.is_active}
    db.session.flush()
    return jsonify(_after_change(me, "MANAGER CHANGED SHIFT TYPE", before, after))


@bp.post("/shift-types/preset")
@manager_required
def apply_preset():
    me = current_user()
    key = str(body().get("preset", "")).upper()
    if key not in PRESETS:
        raise ApiError("Unknown preset")
    before = [f"{d.name} {d.start_time:%H:%M}" for d in all_definitions(me.station_id) if d.is_active]
    wanted = set()
    for code, name, start in PRESETS[key]:
        wanted.add(code)
        d = definition_by_code(me.station_id, code)
        if d is None:
            d = ShiftDefinition(station_id=me.station_id, code=code, name=name, start_time=parse_time(start))
            db.session.add(d)
        d.name, d.start_time, d.is_active = name, parse_time(start), True
    for d in all_definitions(me.station_id):
        if d.code not in wanted:
            d.is_active = False
    db.session.flush()
    after = [f"{n} {s}" for _, n, s in PRESETS[key]]
    return jsonify(_after_change(me, "MANAGER APPLIED SHIFT PATTERN", {"shifts": before}, {"shifts": after}))


# ------------------------------------------------------------------ roster

@bp.get("/roster")
@manager_required
def get_roster():
    me = current_user()
    users = (User.query.filter_by(station_id=me.station_id, role="STAFF")
             .order_by(User.is_active.desc(), User.full_name).all())
    payload = _types_payload(me.station_id)
    payload["staff"] = [{"id": u.id, "full_name": u.full_name, "username": u.username, "role": u.role,
                         "is_active": u.is_active, "shift_type_id": u.default_shift_definition_id} for u in users]
    return jsonify(payload)


@bp.put("/roster/<int:user_id>")
@manager_required
def set_regular_shift(user_id):
    """Choose who normally works which shift. Upcoming scheduled shifts update immediately."""
    me = current_user()
    user = get_or_404(User, user_id, me.station_id, "Staff member")
    raw = body().get("shift_type_id")
    new_def = None
    if raw not in (None, "", 0, "0"):
        new_def = get_or_404(ShiftDefinition, int(raw), me.station_id, "Shift type")
        if not new_def.is_active:
            raise ApiError("That shift type is switched off")
    old_def = db.session.get(ShiftDefinition, user.default_shift_definition_id) if user.default_shift_definition_id else None
    user.default_shift_definition_id = new_def.id if new_def else None
    added, removed = roster.sync_user(user)
    audit.log(me.station_id, me.id, "MANAGER CHANGED REGULAR SHIFT", "users", user.id,
              {"regular_shift": old_def.name if old_def else None},
              {"regular_shift": new_def.name if new_def else None})
    db.session.commit()
    return jsonify({"user_id": user.id, "shift_type_id": user.default_shift_definition_id,
                    "added_to": added, "removed_from": removed})
