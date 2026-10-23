from datetime import timedelta

from flask import Blueprint, current_app, jsonify, session

from ..extensions import db
from ..models import Station, User, utcnow
from ..routes.common import ApiError, body, require_text, validate_password
from ..services import audit, guard, ratelimit
from ..services.audit import client_ip
from . import current_user, login_required, login_user, station_in_use

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 10
# Wait after the 3rd and 4th wrong password in a row (seconds); the 5th locks for LOCK_MINUTES.
SHORT_WAITS = {3: 30, 4: 60}


def _wait_text(seconds):
    seconds = max(1, int(seconds))
    return f"{seconds // 60} min {seconds % 60:02d} s" if seconds >= 60 else f"{seconds} seconds"


def user_payload(user):
    from ..services.stations import accessible_stations
    data = user.to_dict()
    data["station"] = {"id": user.station_id}
    # Locations this person can work on (managers switch between them; directors see all).
    data["stations"] = [{"id": s.id, "name": s.name} for s in accessible_stations(user)]
    return data


@bp.post("/station")
@login_required
def switch_station():
    """A manager with several locations chooses which one to work on."""
    from ..services.stations import can_access
    user = current_user()
    if not user.is_manager:
        raise ApiError("Only managers switch between stations", 403)
    station_id = int(body().get("station_id") or 0)
    station = db.session.get(Station, station_id)
    if station is None or not station.is_active or not can_access(user, station_id):
        raise ApiError("You do not have access to that station", 403)
    old = user.station_id
    user.station_id = station_id
    audit.log(station_id, user.id, "MANAGER SWITCHED STATION", "users", user.id, {"station_id": old},
              {"station_id": station_id, "station": station.name})
    db.session.commit()
    return jsonify({"user": user_payload(user)})


@bp.post("/login")
def login():
    data = body()
    # Bot protection: "website" is a hidden field people never see or fill; simple bots fill every field.
    if str(data.get("website") or "").strip():
        first = Station.query.order_by(Station.id).first()
        if first:
            audit.log(first.id, None, "BLOCKED BOT LOGIN", reason=f"hidden bot-trap field filled, from {client_ip()}")
            db.session.commit()
        raise ApiError("Incorrect username or password", 401)
    # Rate limit per client IP (on top of the 5-attempt lock-out per account).
    wait = ratelimit.hit(f"login:{client_ip()}", current_app.config["LOGIN_RATE_LIMIT"],
                         current_app.config["LOGIN_RATE_WINDOW_SECONDS"])
    if wait:
        guard.failed_login(client_ip())
        raise ApiError(f"Too many login attempts from this device. Try again in {_wait_text(wait)}.", 429,
                       retry_after=wait)
    username = require_text(data, "username", max_len=60).lower()
    password = str(data.get("password") or "")[:200]
    user = User.query.filter(db.func.lower(User.username) == username).first()
    now = utcnow()

    if user and user.locked_until and user.locked_until > now:
        left = (user.locked_until - now).total_seconds()
        raise ApiError(f"Too many wrong passwords. Wait {_wait_text(left)} before trying again.", 429,
                       retry_after=left)
    if current_app.config.get("DEMO_MODE") and user is not None and user.role != "STAFF":
        raise ApiError("The training portal is for staff practice only. Managers use the real system.", 403)
    if user is None or not user.check_password(password):
        wait = None
        if user:
            user.failed_login_count += 1
            if user.failed_login_count >= MAX_FAILED_LOGINS:
                wait = LOCK_MINUTES * 60
                user.failed_login_count = 0
            else:
                wait = SHORT_WAITS.get(user.failed_login_count)
            if wait:
                user.locked_until = now + timedelta(seconds=wait)
            db.session.commit()
        guard.failed_login(client_ip(), user.station_id if user else None, username)
        if wait:
            raise ApiError(f"Incorrect username or password. Wait {_wait_text(wait)} before trying again.", 401,
                           retry_after=wait)
        raise ApiError("Incorrect username or password", 401)
    if not user.is_active:
        raise ApiError("This account has been disabled. Please contact the manager.", 403)
    if not station_in_use(user):
        raise ApiError("Your station has been removed by the director. Please contact the manager.", 403)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    audit.log(user.station_id, user.id, "USER LOGGED IN", "users", user.id)
    db.session.commit()
    login_user(user)
    return jsonify({"user": user_payload(user)})


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@bp.get("/me")
@login_required
def me():
    return jsonify({"user": user_payload(current_user())})


@bp.post("/change-password")
@login_required
def change_password():
    user = current_user()
    if current_app.config.get("DEMO_MODE"):
        raise ApiError("In the training portal you sign in by choosing your shift, so there is no password to change. "
                       "In the real system you will be asked to set your own password at first login.", 403)
    data = body()
    if not user.check_password(data.get("current_password") or ""):
        raise ApiError("Current password is incorrect", 400)
    new = validate_password(data.get("new_password"))
    if user.check_password(new):
        raise ApiError("The new password must be different from the current one", 400)
    user.set_password(new)
    user.must_change_password = False
    audit.log(user.station_id, user.id, "USER CHANGED PASSWORD", "users", user.id)
    db.session.commit()
    login_user(user)
    return jsonify({"ok": True})
