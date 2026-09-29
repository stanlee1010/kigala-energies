"""Station settings stored in the system_settings table."""
from datetime import datetime

from flask import g, has_app_context

from ..extensions import db
from ..models import SystemSetting

DEFAULTS = {
    "timezone": "Africa/Dar_es_Salaam",
    "currency": "TZS",
    # Legacy: only used once to create the Morning/Night shift types when upgrading
    # an older database. Shift times are now managed as shift types (shift_definitions).
    "morning_start": "06:00",
    "night_start": "18:00",
    # Differences at or below these tolerances do not raise warnings.
    "stock_mismatch_tolerance_litres": "0",
    "meter_mismatch_tolerance": "0",
    "deposit_tolerance": "0",
    # A closing dip within this % of "calculated minus the refill" means the
    # refill was probably never reflected in the tank.
    "refill_dip_tolerance_pct": "5",
    # Shifts before this date are never reported as missing.
    "go_live_date": "",
    # Training portal only: paused business clock (ISO local datetime). Ignored on the real portal.
    "training_clock": "",
    # Training portal only: fingerprint of the real station set-up it was copied from.
    "training_fingerprint": "",
}

EDITABLE_KEYS = tuple(k for k in DEFAULTS
                      if k not in ("morning_start", "night_start", "training_clock", "training_fingerprint"))


def _cache():
    if not has_app_context():
        return {}
    if "_settings_cache" not in g:
        g._settings_cache = {}
    return g._settings_cache


def get_settings(station_id):
    cache = _cache()
    if station_id in cache:
        return cache[station_id]
    values = dict(DEFAULTS)
    for row in SystemSetting.query.filter_by(station_id=station_id).all():
        values[row.key] = row.value
    cache[station_id] = values
    return values


def get_setting(station_id, key):
    return get_settings(station_id).get(key, DEFAULTS.get(key))


def set_setting(station_id, key, value, user_id=None):
    if key not in DEFAULTS:
        raise ValueError(f"Unknown setting '{key}'")
    value = "" if value is None else str(value).strip()
    _validate(key, value)
    row = SystemSetting.query.filter_by(station_id=station_id, key=key).first()
    old = row.value if row else DEFAULTS[key]
    if row is None:
        row = SystemSetting(station_id=station_id, key=key, value=value, updated_by=user_id)
        db.session.add(row)
    else:
        row.value = value
        row.updated_by = user_id
    _cache().pop(station_id, None)
    return old, value


def _validate(key, value):
    if key == "timezone":
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(value)
        except Exception:
            raise ValueError(f"Unknown timezone '{value}'")
    elif key in ("morning_start", "night_start"):
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError:
            raise ValueError(f"{key} must be in HH:MM format")
    elif key == "go_live_date" and value:
        datetime.strptime(value, "%Y-%m-%d")
    elif key == "training_clock" and value:
        datetime.fromisoformat(value)
    elif key.endswith(("_litres", "_tolerance", "_pct")):
        if float(value) < 0:
            raise ValueError(f"{key} cannot be negative")
