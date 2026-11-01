"""Create / resolve manager warnings.

Alerts with a dedupe_key are never duplicated while an unresolved alert with
the same key exists; condition-based alerts (low stock, unreconciled refill)
are resolved automatically when the condition clears.
"""
import json

from ..extensions import db
from ..models import Alert, utcnow
from ..models.base import serialize

# Every kind of warning, in plain words, and whether it is urgent unless the manager chooses otherwise.
# Urgent = severity CRITICAL (listed first, in red; the menu counter turns red); not urgent = WARNING.
WARNING_KINDS = (
    ("LOW_STOCK", "Low fuel stock", "A fuel is at or below its minimum level (Tanks & Stock).", True),
    ("STOCK_MISMATCH", "Stock does not match between shifts", "A shift's opening stock differs from the previous closing stock.", True),
    ("METER_ERROR", "Pump meter reading problem", "A meter reading is invalid or does not follow the previous one.", False),
    ("DEPOSIT_DIFFERENCE", "Shift money difference", "The money handed in differs from the money expected from the shift.", True),
    ("MISSING_SHIFT", "Shift not recorded", "A shift ended without being started or submitted.", False),
    ("REFILL_NOT_RECONCILED", "Refill not in the tank", "A delivered refill does not show in the tank dip.", True),
    ("MISSING_STOCK_UPDATE", "Stock not updated", "Tank stock was not recorded or updated for a shift.", True),
    ("TANK_OVER_CAPACITY", "Tank over capacity", "A tank holds more fuel than it can take - check the readings.", True),
    ("TANK_STOCK_NEGATIVE", "Tank below zero", "A tank's calculated stock went below zero - check the readings.", False),
    ("MONTHLY_REVIEW_REQUIRED", "Monthly review", "A reporting period ended and its profit and loss is ready to review.", True),
    ("FUEL_PRICE_UPDATE_REQUIRED", "Fuel price review", "First Wednesday of the period: keep or update each fuel price.", True),
    ("SECURITY", "Security", "A device was blocked after suspicious login attempts or requests.", True),
)
# These are always urgent: the price review must be done, and attacks must never be missed.
ALWAYS_URGENT = frozenset({"FUEL_PRICE_UPDATE_REQUIRED", "SECURITY"})
_USUAL = {kind: urgent for kind, _, _, urgent in WARNING_KINDS}


def urgent_choices(station_id):
    """{alert_type: urgent?} for this station: the manager's choices over the usual urgency."""
    from .settings import get_setting
    try:
        chosen = json.loads(get_setting(station_id, "urgent_warnings") or "{}")
    except ValueError:
        chosen = {}
    return {kind: kind in ALWAYS_URGENT or bool(chosen.get(kind, usual)) for kind, usual in _USUAL.items()}


def severity_for(station_id, alert_type, usual="WARNING"):
    if alert_type not in _USUAL:
        return usual
    return "CRITICAL" if urgent_choices(station_id)[alert_type] else "WARNING"


def raise_alert(station_id, alert_type, title, message, severity="WARNING", dedupe_key=None,
                shift_id=None, fuel_type_id=None, related_record_id=None, details=None):
    severity = severity_for(station_id, alert_type, severity)       # the manager decides what is urgent
    if dedupe_key:
        existing = Alert.query.filter_by(
            station_id=station_id, dedupe_key=dedupe_key, is_resolved=False
        ).first()
        if existing:
            existing.message = message
            existing.title = title
            existing.severity = severity
            if details is not None:
                existing.details = json.dumps(details, default=serialize)
            return existing
    alert = Alert(
        station_id=station_id,
        alert_type=alert_type,
        severity=severity,
        title=title,
        message=message,
        dedupe_key=dedupe_key,
        shift_id=shift_id,
        fuel_type_id=fuel_type_id,
        related_record_id=related_record_id,
        details=json.dumps(details, default=serialize) if details is not None else None,
    )
    db.session.add(alert)
    return alert


def resolve_by_key(station_id, dedupe_key, note="Condition cleared automatically", user_id=None):
    alerts = Alert.query.filter_by(station_id=station_id, dedupe_key=dedupe_key, is_resolved=False).all()
    for alert in alerts:
        resolve(alert, user_id, note)
    return len(alerts)


def resolve(alert, user_id, note=None):
    alert.is_resolved = True
    alert.resolved_by = user_id
    alert.resolved_at = utcnow()
    alert.resolution_note = note
    return alert
