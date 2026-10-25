from datetime import timedelta

from flask import Blueprint, current_app, jsonify
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from itsdangerous import BadSignature, URLSafeTimedSerializer

from ..models import LoginFailure, Station, TwoStepLogin, User, utcnow
from ..services import twostep
from ..routes.common import ApiError, body, require_text, validate_password
from ..services import audit, guard, ratelimit
from ..services.audit import client_ip
from . import current_user, end_session, login_required, login_user, station_in_use

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 10
# Wait after the 3rd and 4th wrong password in a row (seconds); the 5th locks for LOCK_MINUTES.
# These count per username AND device, so someone guessing cannot lock the real person out.
SHORT_WAITS = {3: 30, 4: 60}
# Across every device: this many wrong passwords for one username within an hour locks it for a while
# (stops guessing spread over many devices; one device is blocked by the guard well before this).
ACCOUNT_FAILURES_TO_LOCK = 20
ACCOUNT_LOCK_MINUTES = 15
ALL_DEVICES = "*"
_decoy = {}


def _check_decoy(password):
    """Unknown username: spend the same time as a real password check, so timing reveals nothing."""
    if "hash" not in _decoy:
        _decoy["hash"] = generate_password_hash("not-a-real-account-password")
    check_password_hash(_decoy["hash"], password)
    return False


def _failure_row(username, ip):
    row = db.session.get(LoginFailure, (username, ip))
    if row is None:
        row = LoginFailure(username=username, ip=ip, count=0, updated_at=utcnow())
        db.session.add(row)
    return row


def _locked_for(username, ip, now):
    """Seconds this username is still locked on this device (or on every device), else 0."""
    left = 0
    for key in (ip, ALL_DEVICES):
        row = db.session.get(LoginFailure, (username, key))
        if row is not None and row.locked_until and row.locked_until > now:
            left = max(left, (row.locked_until - now).total_seconds())
    return left


def _count_failure(username, ip, now):
    """One wrong password for username from this device. Returns seconds to wait (or None)."""
    device, every = _failure_row(username, ip), _failure_row(username, ALL_DEVICES)
    device.count += 1
    device.updated_at = now
    if device.count >= MAX_FAILED_LOGINS:
        wait, device.count = LOCK_MINUTES * 60, 0
    else:
        wait = SHORT_WAITS.get(device.count)
    if wait:
        device.locked_until = now + timedelta(seconds=wait)
    if every.updated_at < now - timedelta(hours=1):
        every.count = 0
    every.count += 1
    every.updated_at = now
    if every.count >= ACCOUNT_FAILURES_TO_LOCK:
        every.count, every.locked_until = 0, now + timedelta(minutes=ACCOUNT_LOCK_MINUTES)
        wait = max(wait or 0, ACCOUNT_LOCK_MINUTES * 60)
    try:
        db.session.commit()
    except IntegrityError:          # another copy added the same row at the same moment: count it again
        db.session.rollback()
        return _count_failure(username, ip, now)
    return wait


def forget_failures(username):
    """A new password was set for this person: earlier wrong guesses no longer count."""
    LoginFailure.query.filter_by(username=username.lower()).delete(synchronize_session=False)


def _wait_text(seconds):
    seconds = max(1, int(seconds))
    return f"{seconds // 60} min {seconds % 60:02d} s" if seconds >= 60 else f"{seconds} seconds"


def user_payload(user):
    from ..services.stations import accessible_stations
    data = user.to_dict()
    data["station"] = {"id": user.station_id}
    # Locations this person can work on (managers switch between them; directors see all).
    data["stations"] = [{"id": s.id, "name": s.name} for s in accessible_stations(user)]
    data["two_step"] = two_step_on(user)
    return data


# ---------------------------------------------------------------- 2-step login (manager, director)

TWO_STEP_ROLES = ("MANAGER", "DIRECTOR")
TICKET_MINUTES = 5


def two_step_on(user):
    row = db.session.get(TwoStepLogin, user.id) if user.role in TWO_STEP_ROLES else None
    return bool(row and row.enabled_at)


def _tickets():
    return URLSafeTimedSerializer(current_app.secret_key, salt="two-step-login")


def _finish_login(user, username, now, how="USER LOGGED IN"):
    forget_failures(username)
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    audit.log(user.station_id, user.id, how, "users", user.id)
    db.session.commit()
    login_user(user)
    return jsonify({"user": user_payload(user)})


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
    ip = client_ip()[:64]
    now = utcnow()
    # Waits and locks are kept by username for real and made-up names alike: same answers either way.
    left = _locked_for(username, ip, now)
    if left:
        raise ApiError(f"Too many wrong passwords. Wait {_wait_text(left)} before trying again.", 429,
                       retry_after=left)
    user = User.query.filter(db.func.lower(User.username) == username).first()
    if current_app.config.get("DEMO_MODE") and user is not None and user.role != "STAFF":
        raise ApiError("The training portal is for staff practice only. Managers use the real system.", 403)
    if not (user.check_password(password) if user else _check_decoy(password)):
        wait = _count_failure(username, ip, now)
        guard.failed_login(ip, user.station_id if user else None, username)
        if wait:
            raise ApiError(f"Incorrect username or password. Wait {_wait_text(wait)} before trying again.", 401,
                           retry_after=wait)
        raise ApiError("Incorrect username or password", 401)
    if not user.is_active:
        raise ApiError("This account has been disabled. Please contact the manager.", 403)
    if not station_in_use(user):
        raise ApiError("Your station has been removed by the director. Please contact the manager.", 403)

    if two_step_on(user):
        # Right password: now the code from the authenticator app (the ticket proves step one, for 5 minutes).
        ticket = _tickets().dumps({"u": user.id, "s": user.session_stamp})
        return jsonify({"two_step_required": True, "ticket": ticket})
    return _finish_login(user, username, now)


@bp.post("/login/code")
def login_code():
    """Second step: the 6-digit code. Wrong codes count like wrong passwords (same waits and locks)."""
    data = body()
    ip = client_ip()[:64]
    wait = ratelimit.hit(f"login:{ip}", current_app.config["LOGIN_RATE_LIMIT"],
                         current_app.config["LOGIN_RATE_WINDOW_SECONDS"])
    if wait:
        guard.failed_login(ip)
        raise ApiError(f"Too many login attempts from this device. Try again in {_wait_text(wait)}.", 429,
                       retry_after=wait)
    try:
        ticket = _tickets().loads(str(data.get("ticket") or ""), max_age=TICKET_MINUTES * 60)
    except BadSignature:
        raise ApiError("The login took too long. Please enter your username and password again.", 401) from None
    user = db.session.get(User, ticket.get("u"))
    if user is None or not user.is_active or ticket.get("s") != user.session_stamp:
        raise ApiError("Please enter your username and password again.", 401)
    username, now = user.username.lower(), utcnow()
    left = _locked_for(username, ip, now)
    if left:
        raise ApiError(f"Too many wrong codes. Wait {_wait_text(left)} before trying again.", 429, retry_after=left)
    row = db.session.get(TwoStepLogin, user.id)
    step = twostep.verify(row.secret, data.get("code"), row.last_step) if row and row.enabled_at else None
    if step is None:
        wait = _count_failure(username, ip, now)
        guard.failed_login(ip, user.station_id, username)
        if wait:
            raise ApiError(f"Wrong code. Wait {_wait_text(wait)} before trying again.", 401, retry_after=wait)
        raise ApiError("Wrong code. Type the 6-digit code shown in your authenticator app now.", 401)
    row.last_step = step
    return _finish_login(user, username, now)


def _two_step_user():
    user = current_user()
    if user.role not in TWO_STEP_ROLES or current_app.config.get("DEMO_MODE"):
        raise ApiError("2-step login is for managers and the director", 403)
    return user


@bp.get("/two-step")
@login_required
def two_step_status():
    return jsonify({"enabled": two_step_on(_two_step_user())})


@bp.post("/two-step/setup")
@login_required
def two_step_setup():
    """Start turning 2-step login on: a new key, shown as a QR code (asks for the password again)."""
    user = _two_step_user()
    if not user.check_password(str(body().get("password") or "")):
        raise ApiError("Password is incorrect", 400)
    if two_step_on(user):
        raise ApiError("2-step login is already on", 409)
    row = db.session.get(TwoStepLogin, user.id) or TwoStepLogin(user_id=user.id, secret="")
    row.secret, row.enabled_at, row.last_step = twostep.new_secret(), None, None
    db.session.add(row)
    db.session.commit()
    link = twostep.setup_link(row.secret, user.username)
    return jsonify({"secret": row.secret, "link": link, "qr_svg": twostep.qr_svg(link)})


@bp.post("/two-step/confirm")
@login_required
def two_step_confirm():
    """Finish turning it on: the first code from the app proves it was added correctly."""
    user = _two_step_user()
    row = db.session.get(TwoStepLogin, user.id)
    if row is None or row.enabled_at:
        raise ApiError("Start again: press Turn on 2-step login", 409)
    step = twostep.verify(row.secret, body().get("code"))
    if step is None:
        raise ApiError("That code is not right. Type the 6-digit code shown in the app now.", 400)
    row.enabled_at, row.last_step = utcnow(), step
    audit.log(user.station_id, user.id, "2-STEP LOGIN TURNED ON", "users", user.id)
    db.session.commit()
    return jsonify({"enabled": True})


@bp.post("/two-step/off")
@login_required
def two_step_off():
    """Turn it off: needs the password AND a current code."""
    user = _two_step_user()
    data = body()
    row = db.session.get(TwoStepLogin, user.id)
    if not user.check_password(str(data.get("password") or "")):
        raise ApiError("Password is incorrect", 400)
    if row is None or not row.enabled_at or twostep.verify(row.secret, data.get("code"), row.last_step) is None:
        raise ApiError("That code is not right", 400)
    db.session.delete(row)
    audit.log(user.station_id, user.id, "2-STEP LOGIN TURNED OFF", "users", user.id)
    db.session.commit()
    return jsonify({"enabled": False})


@bp.post("/logout")
def logout():
    end_session()
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
    new = validate_password(data.get("new_password"), username=user.username, strong=user.role != "STAFF")
    if user.check_password(new):
        raise ApiError("The new password must be different from the current one", 400)
    user.set_password(new)
    user.must_change_password = False
    audit.log(user.station_id, user.id, "USER CHANGED PASSWORD", "users", user.id)
    db.session.commit()
    login_user(user)
    return jsonify({"ok": True})
