"""Regular staff roster.

Each staff member can have a regular shift type (e.g. Night). They are then
assigned automatically to every upcoming shift of that type. The manager can
still add or remove people on a single day from the shift itself.

Roster assignments are marked source="ROSTER" so they can be moved when the
regular shift changes; manual assignments are never touched automatically.
Only SCHEDULED (not yet started) shifts are changed.
"""
from ..extensions import db
from ..models import Shift, ShiftDefinition, ShiftStaff, User
from .clock import today_local


def _rostered_users(station_id, shift_type):
    return (db.session.query(User)
            .join(ShiftDefinition, ShiftDefinition.id == User.default_shift_definition_id)
            .filter(User.station_id == station_id, User.is_active.is_(True), ShiftDefinition.code == shift_type)
            .all())


def apply_roster(shift):
    """Add the regular staff of this shift type to a scheduled shift."""
    if shift.status != "SCHEDULED":
        return 0
    assigned = {a.user_id for a in shift.staff}
    added = 0
    for user in _rostered_users(shift.station_id, shift.shift_type):
        if user.id not in assigned:
            db.session.add(ShiftStaff(shift_id=shift.id, user_id=user.id, source="ROSTER"))
            added += 1
    if added:
        db.session.flush()
        db.session.refresh(shift, ["staff"])
    return added


def sync_user(user):
    """Move a user's roster assignments on upcoming scheduled shifts to match their regular shift."""
    today = today_local(user.station_id)
    target = None
    if user.is_active and user.default_shift_definition_id:
        definition = db.session.get(ShiftDefinition, user.default_shift_definition_id)
        target = definition.code if definition and definition.is_active else None

    upcoming = Shift.query.filter(Shift.station_id == user.station_id, Shift.status == "SCHEDULED",
                                  Shift.shift_date >= today).all()
    removed = added = 0
    for shift in upcoming:
        mine = next((a for a in shift.staff if a.user_id == user.id), None)
        if mine and mine.source == "ROSTER" and shift.shift_type != target:
            db.session.delete(mine)
            removed += 1
        elif mine is None and shift.shift_type == target:
            db.session.add(ShiftStaff(shift_id=shift.id, user_id=user.id, source="ROSTER"))
            added += 1
    db.session.flush()
    return added, removed


def sync_station(station_id):
    for user in User.query.filter_by(station_id=station_id).all():
        sync_user(user)
