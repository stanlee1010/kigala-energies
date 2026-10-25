"""Training portal endpoints (staff-only practice).

/api/demo/info works on both portals: the training portal lists the practice
staff accounts; the real portal reports whether the training portal is running
so the login page can link to it. Anything that changes data is refused unless
the server runs in DEMO_MODE with its own training database.
"""
import hmac
import urllib.request

from flask import Blueprint, current_app, jsonify, request

from ..auth import login_user
from ..extensions import db
from ..models import Station, utcnow
from ..services import audit, demo, ratelimit
from ..services.audit import client_ip
from .common import ApiError, body, require_text

bp = Blueprint("demo", __name__, url_prefix="/api/demo")
TRAINING_PORT = 8001


def _require_demo():
    if not current_app.config.get("DEMO_MODE"):
        raise ApiError("This only works in the training portal", 403)


def _training_running():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{TRAINING_PORT}/healthz", timeout=0.4) as r:
            return r.status == 200
    except Exception:
        return False


@bp.get("/info")
def info():
    if current_app.config.get("DEMO_MODE"):
        station = Station.query.first()
        rebuilt = False
        # The login page is where a training session starts: if the manager changed the real
        # shifts, pumps or tanks since the practice data was built, rebuild it to match.
        if station and demo.needs_rebuild(station.id):
            from ..services.bootstrap import training_lock
            with training_lock():
                if demo.needs_rebuild(Station.query.first().id):   # not already done by another copy
                    demo.reset()
                    rebuilt = True
            station = Station.query.first()
        return jsonify({"demo": True, "station": station.name if station else "Training",
                        "accounts": demo.accounts(), "rebuilt": rebuilt})
    if current_app.config.get("TRAINING_URL"):
        # The training portal runs online as its own site (own practice database).
        return jsonify({"demo": False, "training_running": True, "training_url": current_app.config["TRAINING_URL"]})
    if current_app.config.get("VERCEL"):
        # No online training portal configured: the one on the station computer cannot be reached from here.
        return jsonify({"demo": False, "training_available": False})
    running = _training_running()
    host = request.host.split(":")[0]
    return jsonify({"demo": False, "training_running": running,
                    "training_url": f"{request.scheme}://{host}:{TRAINING_PORT}/" if running else None})


@bp.post("/login")
def login():
    """Sign a trainee in as the practice attendant for the shift they chose.

    The practice passwords are random and known to nobody, so the browser never holds or
    sends a password. This only works on the training portal and only for practice staff.
    """
    _require_demo()
    wait = ratelimit.hit(f"login:{client_ip()}", current_app.config["LOGIN_RATE_LIMIT"],
                         current_app.config["LOGIN_RATE_WINDOW_SECONDS"])
    if wait:
        raise ApiError(f"Too many login attempts from this device. Try again in {max(1, wait // 60)} minute(s).", 429)
    user = demo.practice_account(require_text(body(), "username", max_len=60).lower())
    if user is None:
        raise ApiError("Choose one of the practice shifts shown on the page", 404)
    from ..auth.routes import user_payload
    user.last_login_at = utcnow()
    audit.log(user.station_id, user.id, "USER LOGGED IN", "users", user.id, reason="training portal practice account")
    db.session.commit()
    login_user(user)
    return jsonify({"user": user_payload(user)})


@bp.get("/setup")
def setup():
    """Real site only: the station's set-up for the online training portal to copy.

    Shifts, fuels, tanks, pumps and prices of the first station - no people, sales or money.
    Answered only to a request carrying the shared TRAINING_SYNC_TOKEN.
    """
    token = current_app.config.get("TRAINING_SYNC_TOKEN") or ""
    sent = request.headers.get("X-Training-Token") or ""
    if current_app.config.get("DEMO_MODE") or not token or not hmac.compare_digest(sent.encode(), token.encode()):
        raise ApiError("Not found", 404)
    with db.engine.connect() as c:
        return jsonify({"setup": demo.read_setup(c)})


@bp.post("/reset")
def reset():
    """Start a training session with fresh practice data (anyone on the training portal)."""
    _require_demo()
    from ..services.bootstrap import training_lock
    with training_lock():
        demo.reset()
    return jsonify({"ok": True, "message": "Training data has been reset. Please log in again."})
