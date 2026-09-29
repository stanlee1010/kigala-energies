"""First-run setup: create tables and the first location's default configuration.

Creates: the fuel types (Petrol, Diesel), the first station with its starter
pumps, tanks, shifts, low-stock levels and expense categories, the first
manager account and the director account (both must change their password at
first login). No sales, prices or stock are invented - those come from real entries.
"""
from flask import current_app

from ..extensions import db
from ..models import FuelType, Station, User, UserStation

DEFAULT_FUELS = (("PETROL", "Petrol", "#16a34a"), ("DIESEL", "Diesel", "#ea580c"))


def bootstrap():
    db.create_all()
    from .migrations import run_all
    run_all()
    if Station.query.first() is not None:
        return False
    cfg = current_app.config
    for code, name, color in DEFAULT_FUELS:
        if FuelType.query.filter_by(code=code).first() is None:
            db.session.add(FuelType(code=code, name=name, color=color))
    db.session.flush()

    from .stations import create_station, ensure_director
    station = create_station(cfg["DEFAULT_STATION_NAME"], timezone=cfg["DEFAULT_TIMEZONE"],
                             currency=cfg["DEFAULT_CURRENCY"], code="MAIN")
    manager = User(station_id=station.id, username=cfg["INITIAL_MANAGER_USERNAME"].lower(),
                   full_name="Station Manager", role="MANAGER", must_change_password=True)
    manager.set_password(cfg["INITIAL_MANAGER_PASSWORD"])
    db.session.add(manager)
    db.session.flush()
    db.session.add(UserStation(user_id=manager.id, station_id=station.id))
    ensure_director(cfg["INITIAL_DIRECTOR_USERNAME"], cfg["INITIAL_DIRECTOR_PASSWORD"], station.id)
    db.session.commit()
    return True
