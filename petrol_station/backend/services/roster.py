"""Who works which shift: the weekly choice, then the regular roster.

For every week (Monday to Sunday) a staff member works:
  1. the shift they CHOSE for that week (WeeklyShiftChoice) - shift changes start on Mondays; else
  2. their regular shift (users.default_shift_definition_id).
They are assigned automatically to every upcoming shift of that type. The manager can still add or
remove people on a single day from the shift itself.

Automatic assignments are marked source="ROSTER" (regular shift) or "WEEKLY" (weekly choice) so they
can be moved when a choice changes; MANUAL assignments are never touched automatically.
Only SCHEDULED (not yet started) shifts are changed.
"""
from datetime import timedelta

from ..extensions import db
from ..models import Shift, ShiftDefinition, ShiftStaff, User, WeeklyShiftChoice
from .clock import today_local

AUTO_SOURCES = ("ROSTER", "WEEKLY")


def week_start(day):
    """The Monday of the week containing `day`."""
    return day - timedelta(days=day.weekday())


def planned_type(user, day):
    """(shift type code, source) this user should work in the week of `day`, or (None, None)."""
    if not user.is_active or user.role != "STAFF":
        return None, None
    choice = WeeklyShiftChoice.query.filter_by(user_id=user.id, week_start=week_start(day)).first()
    if choice is not None:
        definition = db.session.get(ShiftDefinition, choice.shift_definition_id)
        if definition is not None and definition.is_active:
            return definition.code, "WEEKLY"
    if user.default_shift_definition_id:
        definition = db.session.get(ShiftDefinition, user.default_shift_definition_id)
        if definition is not None and definition.is_active:
            return definition.code, "ROSTER"
    return None, None


def apply_roster(shift):
    """Put everybody who should work this shift (weekly choice or regular shift) on it."""
    if shift.status != "SCHEDULED":
        return 0
    assigned = {a.user_id for a in shift.staff}
    added = 0
    for user in User.query.filter_by(station_id=shift.station_id, role="STAFF", is_active=True).all():
        code, source = planned_type(user, shift.shift_date)
        if code == shift.shift_type and user.id not in assigned:
            db.session.add(ShiftStaff(shift_id=shift.id, user_id=user.id, source=source))
            added += 1
    if added:
        db.session.flush()
        db.session.refresh(shift, ["staff"])
    return added


def sync_user(user, from_day=None):
    """Move a user's automatic assignments on upcoming scheduled shifts to match their plan."""
    start = from_day or today_local(user.station_id)
    upcoming = Shift.query.filter(Shift.station_id == user.station_id, Shift.status == "SCHEDULED",
                                  Shift.shift_date >= start).all()
    removed = added = 0
    for shift in upcoming:
        target, source = planned_type(user, shift.shift_date)
        mine = next((a for a in shift.staff if a.user_id == user.id), None)
        if mine and mine.source in AUTO_SOURCES and shift.shift_type != target:
            db.session.delete(mine)
            removed += 1
        elif mine and mine.source in AUTO_SOURCES and shift.shift_type == target and mine.source != source:
            mine.source = source
        elif mine is None and target and shift.shift_type == target:
            db.session.add(ShiftStaff(shift_id=shift.id, user_id=user.id, source=source))
            added += 1
    db.session.flush()
    return added, removed


def sync_station(station_id):
    for user in User.query.filter_by(station_id=station_id).all():
        sync_user(user)
