"""STAFF MANAGEMENT (manager only).

A manager creates and manages STAFF at the stations they run and chooses each
worker's location. Managers themselves are added or removed by the director
(see routes/director.py).
"""
import re

from flask import Blueprint, jsonify, request

from ..auth import current_user, manager_required
from ..auth.routes import forget_failures
from ..extensions import db
from ..models import AuditLog, Shift, ShiftDefinition, ShiftStaff, Station, User
from ..services import audit, roster
from ..services.clock import today_local
from ..services.stations import accessible_station_ids
from .common import ApiError, body, get_or_404, optional_email, optional_phone, optional_text, require_text, validate_password

bp = Blueprint("users", __name__, url_prefix="/api/users")

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,60}$")


def _user_row(user, today, types, stations):
    upcoming = (db.session.query(Shift)
                .join(ShiftStaff, ShiftStaff.shift_id == Shift.id)
                .filter(ShiftStaff.user_id == user.id, Shift.shift_date >= today, Shift.status != "CANCELLED")
                .order_by(Shift.shift_date, Shift.sequence).first())
    data = user.to_dict()
    data["assigned_shift"] = upcoming.label if upcoming else None
    data["assigned_shift_type"] = upcoming.shift_type if upcoming else None
    data["regular_shift"] = types.get(user.default_shift_definition_id)
    data["station_name"] = stations.get(user.station_id)
    return data


def _station_for(me, raw):
    """The location chosen for a worker: one of the manager's stations (default: the current one)."""
    station_id = int(raw) if raw not in (None, "", 0, "0") else me.station_id
    if station_id not in accessible_station_ids(me):
        raise ApiError("You can only add workers to the stations you manage", 403)
    station = db.session.get(Station, station_id)
    if station is None or not station.is_active:
        raise ApiError("That station is not active")
    return station


def _staff_member(me, user_id):
    """A staff member at one of this manager's stations (managers/directors are handled by the director)."""
    user = db.session.get(User, user_id)
    if user is None or user.station_id not in accessible_station_ids(me):
        raise ApiError("User not found", 404)
    if user.role != "STAFF" and user.id != me.id:
        raise ApiError("Managers are added and changed by the director", 403)
    return user


def _set_regular_shift(user, data):
    """Apply an optional shift_type_id (from the worker's own station); returns True if it changed."""
    if "shift_type_id" not in data:
        return False
    raw = data.get("shift_type_id")
    new_id = None
    if raw not in (None, "", 0, "0"):
        definition = get_or_404(ShiftDefinition, int(raw), user.station_id, "Shift type")
        if not definition.is_active:
            raise ApiError("That shift type is switched off")
        new_id = definition.id
    changed = new_id != user.default_shift_definition_id
    user.default_shift_definition_id = new_id
    return changed


@bp.get("")
@manager_required
def list_users():
    """Staff at every station this manager runs (plus the manager), with each person's location."""
    me = current_user()
    ids = accessible_station_ids(me)
    today = today_local(me.station_id)
    stations = {s.id: s.name for s in Station.query.filter(Station.id.in_(ids))}
    users = (User.query.filter(User.station_id.in_(ids), db.or_(User.role == "STAFF", User.id == me.id))
             .order_by(User.role.desc(), User.full_name).all())
    types = {d.id: d.name for d in ShiftDefinition.query.filter(ShiftDefinition.station_id.in_(ids))}
    return jsonify({"users": [_user_row(u, today, types, stations) for u in users],
                    "stations": [{"id": sid, "name": name} for sid, name in stations.items()],
                    "current_station_id": me.station_id})


@bp.post("")
@manager_required
def create_user():
    me = current_user()
    data = body()
    username = require_text(data, "username").lower()
    if not USERNAME_RE.match(username):
        raise ApiError("Username may contain only letters, numbers, dot, dash and underscore (min 3)")
    if User.query.filter(db.func.lower(User.username) == username).first():
        raise ApiError("That username is already taken")
    if str(data.get("role") or "STAFF").upper() != "STAFF":
        raise ApiError("Managers are added by the director", 403)
    station = _station_for(me, data.get("station_id"))
    user = User(
        station_id=station.id, username=username, full_name=require_text(data, "full_name", "Full name"),
        phone=optional_phone(data), email=optional_email(data), role="STAFF",
        is_active=(data.get("status", "ACTIVE") != "DISABLED"), must_change_password=True, created_by=me.id,
    )
    user.set_password(validate_password(data.get("password"), username=username))
    db.session.add(user)
    db.session.flush()
    if _set_regular_shift(user, data):
        roster.sync_user(user)
    audit.log(station.id, me.id, "MANAGER CREATED STAFF", "users", user.id,
              new=dict(audit.snapshot(user), station=station.name))
    db.session.commit()
    return jsonify({"user": user.to_dict()}), 201


@bp.put("/<int:user_id>")
@manager_required
def update_user(user_id):
    me = current_user()
    user = _staff_member(me, user_id)
    data = body()
    before = audit.snapshot(user)
    if "full_name" in data:
        user.full_name = require_text(data, "full_name", "Full name")
    if "phone" in data:
        user.phone = optional_phone(data)
    if "email" in data:
        user.email = optional_email(data)
    if data.get("station_id") not in (None, "") and user.role == "STAFF":
        station = _station_for(me, data.get("station_id"))
        if station.id != user.station_id:
            # Moving to another location: off the old station's upcoming roster shifts first.
            user.default_shift_definition_id = None
            roster.sync_user(user)
            user.station_id = station.id
            # A regular shift chosen from the OLD station's shift types no longer applies.
            chosen = data.get("shift_type_id")
            if chosen not in (None, "", 0, "0") and not ShiftDefinition.query.filter_by(
                    id=int(chosen), station_id=station.id).first():
                data.pop("shift_type_id")
    if _set_regular_shift(user, data):
        roster.sync_user(user)
    old, new = audit.changed_fields(before, audit.snapshot(user))
    if new:
        audit.log(user.station_id, me.id, "MANAGER EDITED STAFF", "users", user.id, old, new)
    db.session.commit()
    return jsonify({"user": user.to_dict()})


@bp.post("/<int:user_id>/disable")
@manager_required
def disable_user(user_id):
    me = current_user()
    user = _staff_member(me, user_id)
    if user.id == me.id:
        raise ApiError("You cannot disable your own account")
    user.is_active = False
    roster.sync_user(user)  # taken off upcoming roster shifts
    audit.log(user.station_id, me.id, "MANAGER DISABLED USER", "users", user.id,
              {"is_active": True}, {"is_active": False}, reason=optional_text(body(), "reason"))
    db.session.commit()
    return jsonify({"user": user.to_dict()})


@bp.post("/<int:user_id>/enable")
@manager_required
def enable_user(user_id):
    me = current_user()
    user = _staff_member(me, user_id)
    user.is_active = True
    roster.sync_user(user)
    audit.log(user.station_id, me.id, "MANAGER ENABLED USER", "users", user.id, {"is_active": False}, {"is_active": True})
    db.session.commit()
    return jsonify({"user": user.to_dict()})


@bp.post("/<int:user_id>/reset-password")
@manager_required
def reset_password(user_id):
    me = current_user()
    user = _staff_member(me, user_id)
    if user.id == me.id:
        # Your own password changes on Change password, which asks for the current one first.
        raise ApiError("Use Change password for your own account (it asks for your current password)", 403)
    user.set_password(validate_password(body().get("password"), username=user.username))
    user.must_change_password = True
    user.failed_login_count = 0
    user.locked_until = None
    forget_failures(user.username)
    audit.log(user.station_id, me.id, "MANAGER RESET PASSWORD", "users", user.id)
    db.session.commit()
    return jsonify({"ok": True})


@bp.get("/<int:user_id>/activity")
@manager_required
def user_activity(user_id):
    me = current_user()
    user = _staff_member(me, user_id)
    limit = min(int(request.args.get("limit", 100)), 500)
    logs = (AuditLog.query.filter(AuditLog.user_id == user.id, AuditLog.station_id.in_(accessible_station_ids(me)))
            .order_by(AuditLog.created_at.desc()).limit(limit).all())
    shifts = (db.session.query(Shift).join(ShiftStaff, ShiftStaff.shift_id == Shift.id)
              .filter(ShiftStaff.user_id == user.id).order_by(Shift.shift_date.desc()).limit(30).all())
    return jsonify({
        "user": user.to_dict(),
        "activity": [log.to_dict() for log in logs],
        "shifts": [dict(s.to_dict(), label=s.label) for s in shifts],
    })
