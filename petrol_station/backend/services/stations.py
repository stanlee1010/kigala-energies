"""Locations (stations) and who may access them.

STAFF    -> only users.station_id
MANAGER  -> the stations listed in user_stations (chosen by the director);
            users.station_id is the station the manager is working on right now
DIRECTOR -> every station
"""
from ..extensions import db
from ..models import (DEFAULT_EXPENSE_CATEGORIES, ExpenseCategory, FuelType, Pump, Station, StockThreshold,
                      SystemSetting, User, UserStation)

STARTER_PUMPS = (("DIESEL", 1), ("DIESEL", 2), ("PETROL", 1))


def accessible_station_ids(user):
    if user.is_director:
        return [s.id for s in Station.query.order_by(Station.id)]
    if user.is_manager:
        ids = [r.station_id for r in UserStation.query.filter_by(user_id=user.id)]
        return ids or [user.station_id]
    return [user.station_id]


def accessible_stations(user, active_only=True):
    ids = accessible_station_ids(user)
    q = Station.query.filter(Station.id.in_(ids or [0]))
    if active_only:
        q = q.filter(Station.is_active.is_(True))
    return q.order_by(Station.id).all()


def can_access(user, station_id):
    return station_id in accessible_station_ids(user)


def set_manager_stations(user, station_ids):
    """Replace a manager's station list; keep their current station if it is still allowed."""
    ids = sorted({int(s) for s in station_ids})
    UserStation.query.filter_by(user_id=user.id).delete()
    for sid in ids:
        db.session.add(UserStation(user_id=user.id, station_id=sid))
    if ids and user.station_id not in ids:
        user.station_id = ids[0]
    db.session.flush()
    return ids


def next_code():
    n = Station.query.count() + 1
    while Station.query.filter_by(code=f"ST{n:02d}").first():
        n += 1
    return f"ST{n:02d}"


def create_station(name, address=None, phone=None, timezone="Africa/Dar_es_Salaam", currency="TZS", code=None):
    """A new location with the standard starting set-up: settings, starter pumps, one tank per fuel,
    Morning/Night shifts, low-stock levels and expense categories. The manager adjusts it afterwards."""
    station = Station(name=name, code=code or next_code(), address=address, phone=phone)
    db.session.add(station)
    db.session.flush()
    db.session.add_all([
        SystemSetting(station_id=station.id, key="timezone", value=timezone),
        SystemSetting(station_id=station.id, key="currency", value=currency),
    ])
    fuels = {f.code: f for f in FuelType.query.filter_by(is_active=True)}
    for fuel in fuels.values():
        db.session.add(StockThreshold(station_id=station.id, fuel_type_id=fuel.id, minimum_litres=2000))
    for code, number in STARTER_PUMPS:
        if code in fuels:
            db.session.add(Pump(station_id=station.id, fuel_type_id=fuels[code].id, pump_number=number,
                                pump_name=f"{fuels[code].name} Pump {number}"))
    for category in DEFAULT_EXPENSE_CATEGORIES:
        db.session.add(ExpenseCategory(station_id=station.id, name=category))
    db.session.flush()
    from .shifts import ensure_default_definitions
    from .tanks import ensure_default_tanks
    ensure_default_definitions(station.id)
    db.session.flush()
    ensure_default_tanks(station.id)
    return station


def ensure_director(username, password, station_id):
    """There is always at least one director account (created with a password that must be changed)."""
    if User.query.filter_by(role="DIRECTOR").first():
        return None
    if User.query.filter(db.func.lower(User.username) == username.lower()).first():
        username = f"{username}.owner"
    director = User(station_id=station_id, username=username.lower(), full_name="Director", role="DIRECTOR",
                    must_change_password=True)
    director.set_password(password)
    db.session.add(director)
    db.session.flush()
    return director


def ensure_manager_access():
    """Every manager can at least access the station they are on."""
    for user in User.query.filter_by(role="MANAGER").all():
        if not UserStation.query.filter_by(user_id=user.id).first():
            db.session.add(UserStation(user_id=user.id, station_id=user.station_id))
    db.session.flush()
