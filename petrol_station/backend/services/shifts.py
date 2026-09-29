"""Shift scheduling: configurable shift types, windows, current/previous shift, generation.

The manager defines the shift types (ShiftDefinition), e.g. Morning 06:00 +
Night 18:00, or Morning 06:00 + Afternoon 14:00 + Night 22:00. Active types
are ordered by start time; each shift ends when the next one starts and the
last one of the day ends at the first one's start time the next day.
"""
import re
from datetime import datetime, timedelta

from sqlalchemy import and_, or_

from ..extensions import db
from ..models import DEFAULT_SHIFT_DEFINITIONS, Shift, ShiftDefinition
from .clock import now_local

PRESETS = {
    "TWO": (("MORNING", "Morning", "06:00"), ("NIGHT", "Night", "18:00")),
    "THREE": (("MORNING", "Morning", "06:00"), ("AFTERNOON", "Afternoon", "14:00"), ("NIGHT", "Night", "22:00")),
}


# ---------------------------------------------------------------- definitions

def all_definitions(station_id):
    return (ShiftDefinition.query.filter_by(station_id=station_id)
            .order_by(ShiftDefinition.is_active.desc(), ShiftDefinition.start_time).all())


def active_definitions(station_id):
    return (ShiftDefinition.query.filter_by(station_id=station_id, is_active=True)
            .order_by(ShiftDefinition.start_time).all())


def definition_by_code(station_id, code):
    return ShiftDefinition.query.filter_by(station_id=station_id, code=code).first()


def code_from_name(name):
    return re.sub(r"[^A-Z0-9]+", "_", name.strip().upper()).strip("_")[:30] or "SHIFT"


def ensure_default_definitions(station_id, morning="06:00", night="18:00"):
    """Create the default Morning/Night types for a station that has none."""
    if ShiftDefinition.query.filter_by(station_id=station_id).first():
        return False
    starts = {"MORNING": morning, "NIGHT": night}
    for code, name, default_start in DEFAULT_SHIFT_DEFINITIONS:
        start = datetime.strptime(starts.get(code, default_start), "%H:%M").time()
        db.session.add(ShiftDefinition(station_id=station_id, code=code, name=name, start_time=start))
    db.session.flush()
    resequence(station_id)
    return True


def resequence(station_id):
    for index, definition in enumerate(active_definitions(station_id), start=1):
        definition.sequence = index


def definition_windows(station_id, shift_date):
    """[(definition, start, end)] for every active shift type on `shift_date`, in order."""
    defs = active_definitions(station_id)
    result = []
    for i, d in enumerate(defs):
        start = datetime.combine(shift_date, d.start_time)
        if i + 1 < len(defs):
            end = datetime.combine(shift_date, defs[i + 1].start_time)
        else:
            end = datetime.combine(shift_date + timedelta(days=1), defs[0].start_time)
        result.append((d, start, end))
    return result


def shift_window(station_id, shift_date, shift_type):
    for d, start, end in definition_windows(station_id, shift_date):
        if d.code == shift_type:
            return start, end
    raise ValueError(f"'{shift_type}' is not an active shift type")


def locate(station_id, local_dt):
    """Which (shift_date, shift_type) covers a local datetime."""
    day = local_dt.date()
    for candidate_day in (day - timedelta(days=1), day):
        for d, start, end in definition_windows(station_id, candidate_day):
            if start <= local_dt < end:
                return candidate_day, d.code
    defs = active_definitions(station_id)
    return day, defs[0].code if defs else "MORNING"


# --------------------------------------------------------------------- shifts

def get_shift(station_id, shift_date, shift_type):
    return Shift.query.filter_by(station_id=station_id, shift_date=shift_date, shift_type=shift_type).first()


def get_or_create_shift(station_id, shift_date, shift_type):
    """Never creates duplicates: station_id + shift_date + shift_type is unique."""
    shift = get_shift(station_id, shift_date, shift_type)
    if shift and shift.status != "CANCELLED":
        return shift, False
    start, end = shift_window(station_id, shift_date, shift_type)
    sequence = definition_by_code(station_id, shift_type).sequence
    created = shift is None
    if created:
        shift = Shift(station_id=station_id, shift_date=shift_date, shift_type=shift_type)
        db.session.add(shift)
    # A cancelled shift whose type was switched back on is revived instead of duplicated.
    shift.status, shift.scheduled_start, shift.scheduled_end, shift.sequence = "SCHEDULED", start, end, sequence
    db.session.flush()
    from .roster import apply_roster
    apply_roster(shift)
    return shift, created


def generate_shifts(station_id, start_date, end_date):
    """Create every active shift type for every day in the range; returns (all_shifts, created_count)."""
    if end_date < start_date:
        raise ValueError("End date must be on or after start date")
    if (end_date - start_date).days > 366:
        raise ValueError("Please generate at most one year of shifts at a time")
    if not active_definitions(station_id):
        raise ValueError("There are no active shift types. Add one first.")
    shifts, created = [], 0
    day = start_date
    while day <= end_date:
        for d, _, _ in definition_windows(station_id, day):
            shift, was_created = get_or_create_shift(station_id, day, d.code)
            shifts.append(shift)
            created += int(was_created)
        day += timedelta(days=1)
    return shifts, created


def reschedule_future_shifts(station_id):
    """After shift types change: re-time shifts that have not started and cancel removed types.

    Returns the number of shifts cancelled. Shifts that already started, and
    past shifts, are never touched.
    """
    now = now_local(station_id)
    active = {d.code: d for d in active_definitions(station_id)}
    cancelled = 0
    for shift in Shift.query.filter(Shift.station_id == station_id, Shift.status == "SCHEDULED",
                                    Shift.scheduled_start > now).all():
        if shift.shift_type in active:
            shift.scheduled_start, shift.scheduled_end = shift_window(station_id, shift.shift_date, shift.shift_type)
            shift.sequence = active[shift.shift_type].sequence
        else:
            shift.status = "CANCELLED"
            cancelled += 1
    return cancelled


def fill_planned_days(station_id):
    """Add newly active shift types to every upcoming day that already has shifts planned."""
    today = now_local(station_id).date()
    days = [d for (d,) in db.session.query(Shift.shift_date).filter(
        Shift.station_id == station_id, Shift.shift_date >= today).distinct()]
    created = 0
    for day in days:
        for definition, _, _ in definition_windows(station_id, day):
            created += int(get_or_create_shift(station_id, day, definition.code)[1])
    return created


def current_shift(station_id, create=True):
    key = locate(station_id, now_local(station_id))
    if create:
        return get_or_create_shift(station_id, *key)[0]
    return get_shift(station_id, *key)


def earlier_than(shift):
    """SQL condition: Shift rows strictly earlier than `shift`."""
    return or_(Shift.shift_date < shift.shift_date,
               and_(Shift.shift_date == shift.shift_date, Shift.sequence < shift.sequence))


NEWEST_FIRST = (Shift.shift_date.desc(), Shift.sequence.desc())
OLDEST_FIRST = (Shift.shift_date, Shift.sequence)


def shifts_before(shift):
    """Query of (non-cancelled) shifts strictly earlier than `shift`, newest first."""
    return (Shift.query.filter(Shift.station_id == shift.station_id, Shift.status != "CANCELLED",
                               earlier_than(shift))
            .order_by(*NEWEST_FIRST))


def previous_shift(shift):
    return shifts_before(shift).first()


def parse_time(value):
    try:
        return datetime.strptime(str(value).strip()[:5], "%H:%M").time()
    except ValueError:
        raise ValueError("Start time must be in HH:MM format")
