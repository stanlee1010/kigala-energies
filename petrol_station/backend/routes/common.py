"""Request parsing helpers shared by all API routes."""
import re
from datetime import date, datetime

from flask import request

from ..calculations import D
from ..extensions import db


class ApiError(Exception):
    def __init__(self, message, status=400, title=None, details=None, retry_after=None):
        super().__init__(message)
        self.message, self.status, self.title, self.details = message, status, title, details
        self.retry_after = retry_after

    def to_dict(self):
        data = {"error": self.message}
        if self.retry_after:
            data["retry_after"] = int(self.retry_after)
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


# Invisible characters that have no place in names or notes: control characters (except tab and
# new line) and the "bidi" marks that can make text display differently from what it is.
_INVISIBLE = {c: None for c in list(range(0, 9)) + [11, 12] + list(range(14, 32)) + [127]
              + list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A)) + [0x200B, 0x200E, 0x200F, 0xFEFF]}


def clean_text(value):
    """Text from a person: invisible/control characters removed, outer spaces trimmed."""
    return str(value if value is not None else "").translate(_INVISIBLE).strip()


def require_text(data, key, label=None, max_len=255):
    value = clean_text(data.get(key))
    if not value:
        raise ApiError(f"{label or key.replace('_', ' ').capitalize()} is required")
    if len(value) > max_len:
        raise ApiError(f"{label or key} is too long (max {max_len} characters)")
    return value


def optional_text(data, key, max_len=255):
    value = clean_text(data.get(key))
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


def optional_email(data, key="email"):
    value = optional_text(data, key, 120)
    if value and not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[A-Za-z]{2,}", value):
        raise ApiError("Email address is not valid")
    return value


def optional_phone(data, key="phone", max_len=40):
    value = optional_text(data, key, max_len)
    if value and not re.fullmatch(r"[0-9+()\-\s]{6,40}", value):
        raise ApiError("Phone number may only contain digits, spaces, +, - and brackets")
    return value


def color_field(data, key="color"):
    value = optional_text(data, key, 20)
    if value and not re.fullmatch(r"#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3})?", value):
        raise ApiError("Colour must look like #1a2b3c")
    return value
