"""Session authentication and role-based access control."""
from functools import wraps

from flask import g, jsonify, session

from ..extensions import db
from ..models import User


def current_user():
    if "user" in g:
        return g.user
    user = None
    user_id = session.get("user_id")
    if user_id:
        user = db.session.get(User, user_id)
        # Disabled accounts, changed passwords and removed stations end existing sessions.
        if (user is None or not user.is_active or session.get("stamp") != user.session_stamp
                or not station_in_use(user)):
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


def login_user(user):
    session.clear()
    session.permanent = True
    session["user_id"] = user.id
    session["stamp"] = user.session_stamp
    g.user = user


def _deny(message, status):
    return jsonify({"error": message}), status


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return _deny("Please log in", 401)
        return view(*args, **kwargs)
    return wrapper


def director_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return _deny("Please log in", 401)
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
        if not user.is_manager:
            return _deny("Only the manager can do this", 403)
        return view(*args, **kwargs)
    return wrapper
