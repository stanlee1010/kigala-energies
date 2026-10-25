"""Session authentication and role-based access control."""
import secrets
from datetime import timedelta
from functools import wraps

from flask import current_app, g, jsonify, request, session

from ..extensions import db
from ..models import User, UserSession, utcnow

# While a temporary password is in use (first login, or after a reset) only these work:
# exactly what the Change password page needs.
FIRST_LOGIN_ENDPOINTS = {"auth.me", "auth.change_password", "auth.logout",
                         "dashboard.server_time", "dashboard.get_station_settings"}


def current_user():
    if "user" in g:
        return g.user
    user = None
    user_id = session.get("user_id")
    if user_id:
        user = db.session.get(User, user_id)
        # Logged-out sessions, disabled accounts, changed passwords and removed stations end here.
        if (user is None or not _session_open(session.get("sid"), user.id) or not user.is_active
                or session.get("stamp") != user.session_stamp or not station_in_use(user)):
            session.clear()
            user = None
    g.user = user
    return user


def station_in_use(user):
    """False when the person's station was removed by the director (a manager is moved to another
    of their stations if they have one; the director is never blocked)."""
    from ..models import Station
    from ..services.stations import accessible_stations
    if user.is_director:
        return True
    station = db.session.get(Station, user.station_id)
    if station is not None and station.is_active:
        return True
    if user.is_manager:
        others = accessible_stations(user)
        if others:
            user.station_id = others[0].id
            db.session.commit()
            return True
    return False


def _session_open(sid, user_id):
    if not sid:
        return False
    row = db.session.get(UserSession, sid)
    return (row is not None and row.user_id == user_id and row.ended_at is None
            and row.created_at > utcnow() - current_app.permanent_session_lifetime)


def login_user(user):
    """Start a session: a server-side record (ended at logout) named by the signed cookie."""
    now = utcnow()
    UserSession.query.filter(UserSession.user_id == user.id,       # tidy this person's old sessions
                             UserSession.created_at < now - current_app.permanent_session_lifetime - timedelta(days=1)
                             ).delete(synchronize_session=False)
    row = UserSession(id=secrets.token_urlsafe(32), user_id=user.id, created_at=now)
    db.session.add(row)
    db.session.commit()
    session.clear()
    session.permanent = True
    session["user_id"] = user.id
    session["sid"] = row.id
    session["stamp"] = user.session_stamp
    g.user = user


def end_session():
    """Log out: the session ends on the server too, so a copied cookie no longer works."""
    sid = session.get("sid")
    row = db.session.get(UserSession, sid) if sid else None
    if row is not None and row.ended_at is None:
        row.ended_at = utcnow()
        db.session.commit()
    session.clear()
    g.pop("user", None)


def _password_change_pending(user):
    return user.must_change_password and request.endpoint not in FIRST_LOGIN_ENDPOINTS


_CHANGE_FIRST = "Set your own password first (Change password)."


def _deny(message, status):
    return jsonify({"error": message}), status


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return _deny("Please log in", 401)
        if _password_change_pending(user):
            return jsonify({"error": _CHANGE_FIRST, "must_change_password": True}), 403
        return view(*args, **kwargs)
    return wrapper


def director_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return _deny("Please log in", 401)
        if _password_change_pending(user):
            return jsonify({"error": _CHANGE_FIRST, "must_change_password": True}), 403
        if not user.is_director:
            return _deny("Only the director can do this", 403)
        return view(*args, **kwargs)
    return wrapper


def manager_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return _deny("Please log in", 401)
        if _password_change_pending(user):
            return jsonify({"error": _CHANGE_FIRST, "must_change_password": True}), 403
        if not user.is_manager:
            return _deny("Only the manager can do this", 403)
        return view(*args, **kwargs)
    return wrapper
