"""Shared column types and helpers for all models.

Timestamps (created_at/updated_at) are stored in UTC. Business dates such as
shift_date, refill_date and expense_date are stored in the station's local
timezone, because that is what "today" means at the pumps.
"""
import json
from datetime import date, datetime, time, timezone
from decimal import Decimal

from ..extensions import db

MONEY = db.Numeric(18, 2)
RATE = db.Numeric(14, 2)      # price / cost per litre
LITRES = db.Numeric(16, 3)
METER = db.Numeric(18, 3)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)


def serialize(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    if isinstance(value, (date, time)):
        return value.isoformat()
    return value


class SerializerMixin:
    """Adds to_dict() based on the table's columns."""

    hidden_fields = ()
    # DateTime columns holding station-local wall-clock time (not UTC).
    local_datetime_fields = ()

    def to_dict(self, *extra):
        data = {}
        for column in self.__table__.columns:
            if column.key in self.hidden_fields:
                continue
            value = getattr(self, column.key)
            if isinstance(value, datetime) and column.key in self.local_datetime_fields:
                data[column.key] = value.isoformat()
            else:
                data[column.key] = serialize(value)
        for name in extra:
            data[name] = serialize(getattr(self, name))
        return data


def dumps(value):
    if value is None:
        return None
    return json.dumps(value, default=serialize)
