"""Create / resolve manager warnings.

Alerts with a dedupe_key are never duplicated while an unresolved alert with
the same key exists; condition-based alerts (low stock, unreconciled refill)
are resolved automatically when the condition clears.
"""
import json

from ..extensions import db
from ..models import Alert, utcnow
from ..models.base import serialize


def raise_alert(station_id, alert_type, title, message, severity="WARNING", dedupe_key=None,
                shift_id=None, fuel_type_id=None, related_record_id=None, details=None):
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
