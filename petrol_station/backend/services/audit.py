"""Audit trail helpers. Every important change goes through log()."""
from flask import has_request_context, request

from ..extensions import db
from ..models import AuditLog
from ..models.base import dumps, serialize


def client_ip():
    if not has_request_context():
        return None
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",")[0].strip() or request.remote_addr) if forwarded else request.remote_addr


def snapshot(obj, fields=None):
    if obj is None:
        return None
    keys = fields or [c.key for c in obj.__table__.columns if c.key != "password_hash"]
    return {k: serialize(getattr(obj, k)) for k in keys}


def log(station_id, user_id, action, table_name=None, record_id=None, old=None, new=None, reason=None):
    entry = AuditLog(
        station_id=station_id,
        user_id=user_id,
        action=action,
        table_name=table_name,
        record_id=record_id,
        old_values=dumps(old),
        new_values=dumps(new),
        reason=reason,
        ip_address=client_ip(),
    )
    db.session.add(entry)
    return entry


def changed_fields(before, after):
    """Return (old, new) dicts limited to keys whose values differ."""
    before, after = before or {}, after or {}
    keys = [k for k in after if before.get(k) != after.get(k) and k not in ("updated_at",)]
    return {k: before.get(k) for k in keys}, {k: after.get(k) for k in keys}
