"""Training portal endpoints (staff-only practice).

/api/demo/info works on both portals: the training portal lists the practice
staff accounts; the real portal reports whether the training portal is running
so the login page can link to it. Anything that changes data is refused unless
the server runs in DEMO_MODE with its own training database.
"""
import urllib.request

from flask import Blueprint, current_app, jsonify, request

from ..models import Station
from ..services import demo
from .common import ApiError

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
            demo.reset()
            station, rebuilt = Station.query.first(), True
        return jsonify({"demo": True, "station": station.name if station else "Training",
                        "accounts": demo.accounts(), "rebuilt": rebuilt})
    running = _training_running()
    host = request.host.split(":")[0]
    return jsonify({"demo": False, "training_running": running,
                    "training_url": f"{request.scheme}://{host}:{TRAINING_PORT}/" if running else None})


@bp.post("/reset")
def reset():
    """Start a training session with fresh practice data (anyone on the training portal)."""
    _require_demo()
    demo.reset()
    return jsonify({"ok": True, "message": "Training data has been reset. Please log in again."})
