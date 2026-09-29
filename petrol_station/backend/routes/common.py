"""Request parsing helpers shared by all API routes."""
from datetime import date, datetime

from flask import request

from ..calculations import D
from ..extensions import db


class ApiError(Exception):
    def __init__(self, message, status=400, title=None, details=None):
        super().__init__(message)
        self.message, self.status, self.title, self.details = message, status, title, details

    def to_dict(self):
        data = {"error": self.message}
        if self.title:
            data["title"] = self.title
        if self.details is not None:
            data["details"] = self.details
        return data


def body():
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict() if request.form else {}
    if not isinstance(data, dict):
        raise ApiError("Expected a JSON object")
    return data


def require_text(data, key, label=None, max_len=255):
    value = str(data.get(key) or "").strip()
    if not value:
        raise ApiError(f"{label or key.replace('_', ' ').capitalize()} is required")
    if len(value) > max_len:
        raise ApiError(f"{label or key} is too long (max {max_len} characters)")
    return value


def optional_text(data, key, max_len=255):
    value = str(data.get(key) or "").strip()
    return value[:max_len] or None


def decimal_field(data, key, label=None, required=True, minimum=None, allow_zero=True):
    label = label or key.replace("_", " ").capitalize()
    try:
        value = D(data.get(key))
    except ValueError:
        raise ApiError(f"{label} must be a number")
    if value is None:
        if required:
            raise ApiError(f"{label} is required")
        return None
    if minimum is not None and value < minimum:
        raise ApiError(f"{label} cannot be less than {minimum}")
    if not allow_zero and value == 0:
        raise ApiError(f"{label} must be greater than zero")
    return value


def date_field(value, label="Date", required=True):
    if value in (None, ""):
        if required:
            raise ApiError(f"{label} is required")
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ApiError(f"{label} must be a date (YYYY-MM-DD)")


def time_field(value, label="Time"):
    if not value:
        raise ApiError(f"{label} is required")
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(str(value), fmt).time()
        except ValueError:
            continue
    raise ApiError(f"{label} must be a time (HH:MM)")


def get_or_404(model, record_id, station_id=None, label=None):
    obj = db.session.get(model, record_id)
    if obj is None or (station_id is not None and getattr(obj, "station_id", station_id) != station_id):
        raise ApiError(f"{label or model.__name__} not found", 404)
    return obj


def arg_date(name, default=None):
    return date_field(request.args.get(name), name, required=False) or default


def validate_password(value):
    value = value or ""
    if len(value) < 8:
        raise ApiError("Password must be at least 8 characters long")
    if value.isdigit() or value.isalpha():
        raise ApiError("Password must contain both letters and numbers")
    return value
