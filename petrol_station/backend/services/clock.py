"""The authoritative business clock.

All business logic (current shift, reporting period, first Wednesday, alerts)
uses the SERVER clock converted to the station timezone configured in system
settings. The browser clock is only used for display.

Training portal only (DEMO_MODE): the clock is paused at the time stored in the
`training_clock` setting, so a practice session never rolls into the next
shift, raises missing-shift warnings, or hits a month-end by itself.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import current_app, g, has_app_context, has_request_context

from .settings import get_setting


def station_tz(station_id):
    return ZoneInfo(get_setting(station_id, "timezone"))


def paused_at(station_id):
    """The frozen training time, or None when the clock runs normally (always None on the real portal).

    Each trainee practises their own shift, so a logged-in trainee's clock is paused
    inside their regular shift on the practice day (e.g. Morning 09:00, Night 21:00).
    """
    if not (has_app_context() and current_app.config.get("DEMO_MODE")):
        return None
    value = get_setting(station_id, "training_clock")
    if not value:
        return None
    base = datetime.fromisoformat(value)
    user = g.get("user") if has_request_context() else None
    if user is not None and user.default_shift_definition_id:
        return practice_time_in_shift(station_id, base.date(), user.default_shift_definition_id) or base
    return base


def practice_time_in_shift(station_id, day, definition_id):
    """3 hours into the shift (or halfway, for short shifts) on `day`."""
    from ..extensions import db
    from ..models import ShiftDefinition
    from .shifts import shift_window
    definition = db.session.get(ShiftDefinition, definition_id)
    if definition is None or not definition.is_active:
        return None
    start, end = shift_window(station_id, day, definition.code)
    return start + min(timedelta(hours=3), (end - start) / 2)


def now_local(station_id):
    """Current station wall-clock time as a naive datetime."""
    frozen = paused_at(station_id)
    if frozen is not None:
        return frozen
    return datetime.now(timezone.utc).astimezone(station_tz(station_id)).replace(tzinfo=None)


def today_local(station_id):
    return now_local(station_id).date()


def utc_to_local(station_id, value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).astimezone(station_tz(station_id)).replace(tzinfo=None)
