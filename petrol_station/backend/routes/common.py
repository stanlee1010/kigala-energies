"""Request parsing helpers shared by all API routes."""
import re
from datetime import date, datetime, timedelta, timezone

from flask import current_app, request

from ..calculations import D
from ..extensions import db
from ..models.base import utcnow as utcnow_naive


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


# Passwords people pick first - refused even when long enough (checked without letter case).
COMMON_PASSWORDS = frozenset("""
password password1 password12 password123 password1234 passw0rd p@ssw0rd p@ssword1 qwerty123 qwerty1234
qwertyuiop1 abc12345 abcd1234 abc123456 1q2w3e4r 1qaz2wsx zaq12wsx letmein1 welcome1 welcome123
admin123 admin1234 administrator1 manager1 manager123 director1 director123 changeme1 changeme123
iloveyou1 sunshine1 football1 monkey123 dragon123 master123 trustno1 baseball1 superman1 princess1
kigala123 kigala1234 kigalaenergies1 petrol123 diesel123 station123 tanzania1 dar12345 daressalaam1
""".split())


def validate_password(value, username=None, strong=False):
    """strong: managers and the director (they control money and accounts) need 10+ characters."""
    value = value or ""
    minimum = 10 if strong else 8
    if len(value) < minimum:
        raise ApiError(f"Password must be at least {minimum} characters long")
    if value.isdigit() or value.isalpha():
        raise ApiError("Password must contain both letters and numbers")
    lowered = value.lower()
    if lowered in COMMON_PASSWORDS or (username and username.lower() in lowered):
        raise ApiError("That password is too easy to guess. Choose another one")
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


# ---------------------------------------------------------------- saves made without internet
OFFLINE_MAX_AGE = timedelta(days=3)


def saved_at(not_before=None):
    """When the person pressed Save (UTC). Normally now; a save kept on the phone while offline
    sends X-Saved-At, measured with the SERVER's clock (the phone keeps its offset to the server,
    never its own time). Times in the future become now; anything older than 3 days is refused;
    nothing earlier than `not_before` (e.g. the shift start) is accepted."""
    now = utcnow_naive()
    raw = request.headers.get("X-Saved-At")
    if not raw or current_app.config.get("DEMO_MODE"):
        return now
    try:
        t = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ApiError("Invalid X-Saved-At time")
    if t.tzinfo is not None:
        t = t.astimezone(timezone.utc).replace(tzinfo=None)
    if t < now - OFFLINE_MAX_AGE:
        raise ApiError("This was saved without internet more than 3 days ago and can no longer be sent. "
                       "Give the figures to the manager.", 409)
    if not_before is not None and t < not_before:
        t = not_before
    return min(t, now)


def offline_note(station_id, when):
    """Audit-log note for a save that was typed offline and arrived later (None otherwise)."""
    now = utcnow_naive()
    if now - when < timedelta(minutes=2):
        return None
    from ..services.clock import station_tz
    tz = station_tz(station_id)
    fmt = lambda t: t.replace(tzinfo=timezone.utc).astimezone(tz).strftime("%d/%m/%Y %H:%M")
    return f"Saved on the phone at {fmt(when)} without internet; received {fmt(now)}."


def join_notes(*notes):
    return " ".join(n for n in notes if n) or None


def check_unchanged(current, based_on, what):
    """A save says which values it was based on. If someone changed them since, refuse instead
    of overwriting their newer values (the person sees both and can check with the manager)."""
    changed = {k: v for k, v in current.items() if _norm(v) != _norm(based_on.get(k))}
    if changed:
        now_text = ", ".join(f"{k.replace('_', ' ')} {v if v not in (None, '') else '(empty)'}" for k, v in current.items())
        raise ApiError(f"Someone changed this {what} after you saved yours, so yours was not used. "
                       f"It now shows: {now_text}. Check with the manager before entering it again.", 409)


def _norm(value):
    if value in (None, ""):
        return None
    try:
        return D(value)
    except (ValueError, ArithmeticError):
        return " ".join(str(value).split())
