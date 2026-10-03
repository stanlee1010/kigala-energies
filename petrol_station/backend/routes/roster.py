"""Shift types (Morning / Afternoon / Night ...), the regular staff roster and weekly shift choices."""
from datetime import timedelta

from flask import Blueprint, jsonify, request

from ..auth import current_user, login_required, manager_required
from ..extensions import db
from ..models import Shift, ShiftDefinition, ShiftStaff, User, WeeklyShiftChoice
from ..services import audit, roster
from ..services.monitor import ensure_upcoming_shifts
from ..services.shifts import (PRESETS, all_definitions, code_from_name, definition_by_code, definition_windows,
                               fill_planned_days, generate_shifts, parse_time, reschedule_future_shifts, resequence)
from ..services.clock import today_local
from .common import ApiError, body, date_field, get_or_404, require_text

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


# ------------------------------------------------------ weekly shift choice

def _week_bounds(station_id, week_param=None):
    """(this Monday, the Monday of the week asked for - next week by default)."""
    this_monday = roster.week_start(today_local(station_id))
    if week_param:
        return this_monday, roster.week_start(date_field(week_param, "Week"))
    return this_monday, this_monday + timedelta(days=7)


def _choose_week(me, user, monday, definition_id):
    """Save the weekly choice and move the person onto that shift for the whole week."""
    this_monday = roster.week_start(today_local(user.station_id))
    if monday <= this_monday:
        raise ApiError("Shift changes start on Monday: you can choose for next week or later")
    definition = get_or_404(ShiftDefinition, int(definition_id or 0), user.station_id, "Shift type")
    if not definition.is_active:
        raise ApiError("That shift type is switched off")
    choice = WeeklyShiftChoice.query.filter_by(user_id=user.id, week_start=monday).first()
    old = db.session.get(ShiftDefinition, choice.shift_definition_id).name if choice else None
    if choice is None:
        choice = WeeklyShiftChoice(station_id=user.station_id, user_id=user.id, week_start=monday)
        db.session.add(choice)
    choice.shift_definition_id = definition.id
    db.session.flush()
    generate_shifts(user.station_id, monday, monday + timedelta(days=6))   # the week's shifts exist
    roster.sync_user(user, from_day=monday)
    added = (db.session.query(ShiftStaff).join(Shift, Shift.id == ShiftStaff.shift_id)
             .filter(ShiftStaff.user_id == user.id, Shift.shift_type == definition.code,
                     Shift.shift_date >= monday, Shift.shift_date <= monday + timedelta(days=6)).count())
    who = "STAFF CHOSE" if me.id == user.id else "MANAGER SET"
    audit.log(user.station_id, me.id, f"{who} WEEKLY SHIFT", "weekly_shift_choices", choice.id,
              {"week": monday, "shift": old}, {"week": monday, "shift": definition.name, "user": user.username})
    return choice, definition, added


def _plan_name(user, monday):
    code, source = roster.planned_type(user, monday)
    if not code:
        return None, None
    d = ShiftDefinition.query.filter_by(station_id=user.station_id, code=code).first()
    return (d.name if d else code), source


@bp.get("/staff/week-choice")
@login_required
def my_week_choice():
    """Staff: which shift am I on this week, and which shift do I want next week?"""
    me = current_user()
    this_monday, next_monday = _week_bounds(me.station_id)
    this_name, this_source = _plan_name(me, this_monday)
    choice = WeeklyShiftChoice.query.filter_by(user_id=me.id, week_start=next_monday).first()
    next_name, next_source = _plan_name(me, next_monday)
    return jsonify({
        "this_week": {"start": this_monday, "end": this_monday + timedelta(days=6), "shift": this_name,
                      "source": this_source},
        "next_week": {"start": next_monday, "end": next_monday + timedelta(days=6),
                      "chosen_shift_type_id": choice.shift_definition_id if choice else None,
                      "shift": next_name, "source": next_source, "chosen": choice is not None},
        "shift_types": [t for t in _types_payload(me.station_id)["shift_types"] if t["is_active"]],
    })


@bp.put("/staff/week-choice")
@login_required
def set_my_week_choice():
    me = current_user()
    if me.role != "STAFF":
        raise ApiError("Only staff choose a weekly shift; managers assign shifts on the Shifts page", 403)
    _, next_monday = _week_bounds(me.station_id)
    choice, definition, added = _choose_week(me, me, next_monday, body().get("shift_type_id"))
    db.session.commit()
    return jsonify({"week_start": next_monday, "shift": definition.name, "shifts_assigned": added})


@bp.get("/roster/week-choices")
@manager_required
def week_choices():
    """Manager: every staff member's shift for a week (their choice, or their regular shift)."""
    me = current_user()
    _, monday = _week_bounds(me.station_id, request.args.get("week"))
    staff = User.query.filter_by(station_id=me.station_id, role="STAFF", is_active=True).order_by(User.full_name).all()
    rows, counts = [], {}
    for u in staff:
        name, source = _plan_name(u, monday)
        rows.append({"id": u.id, "full_name": u.full_name, "username": u.username, "shift": name,
                     "source": source, "chosen": source == "WEEKLY"})
        counts[name or "Not set"] = counts.get(name or "Not set", 0) + 1
    return jsonify({"week_start": monday, "week_end": monday + timedelta(days=6), "staff": rows, "counts": counts,
                    "shift_types": [t for t in _types_payload(me.station_id)["shift_types"] if t["is_active"]]})


@bp.put("/roster/week-choices/<int:user_id>")
@manager_required
def set_week_choice(user_id):
    """Manager sets (or changes) a staff member's shift for a week."""
    me = current_user()
    user = get_or_404(User, user_id, me.station_id, "Staff member")
    if user.role != "STAFF":
        raise ApiError("Weekly shifts are for staff")
    data = body()
    _, monday = _week_bounds(me.station_id, data.get("week"))
    _, definition, added = _choose_week(me, user, monday, data.get("shift_type_id"))
    db.session.commit()
    return jsonify({"week_start": monday, "shift": definition.name, "shifts_assigned": added})


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
