"""First-run setup: create tables and the first location's default configuration.

Creates: the fuel types (Petrol, Diesel), the first station with its starter
pumps, tanks, shifts, low-stock levels and expense categories, the first
manager account and the director account (both must change their password at
first login). No sales, prices or stock are invented - those come from real entries.
"""
from contextlib import contextmanager

from flask import current_app

from ..extensions import db
from ..models import FuelType, Station, User, UserStation

DEFAULT_FUELS = (("PETROL", "Petrol", "#16a34a"), ("DIESEL", "Diesel", "#ea580c"))


# Any fixed numbers: the PostgreSQL locks that make starting copies take turns (see bootstrap).
_BOOTSTRAP_LOCK = 724_001
_TRAINING_LOCK = 724_002


@contextmanager
def training_lock():
    """Online training portal: only one copy at a time builds or resets the practice data."""
    if db.engine.dialect.name != "postgresql":
        yield
        return
    with db.engine.begin() as lock_conn:          # held until this transaction ends
        lock_conn.exec_driver_sql(f"SELECT pg_advisory_xact_lock({_TRAINING_LOCK})")
        yield


def bootstrap():
    """Create/upgrade the tables and seed a new database.

    On Vercel several copies of the app can start at the same moment. On PostgreSQL they take
    turns here, so two copies never create the same table or the first station twice: a
    transaction-level advisory lock is held on its own connection while the work runs, and is
    released when that transaction ends (also safe behind a connection pooler such as Neon's).
    Other databases run one process: no lock needed.
    """
    if db.engine.dialect.name != "postgresql":
        return _bootstrap()
    with db.engine.begin() as lock_conn:          # the lock lasts until this transaction ends
        lock_conn.exec_driver_sql(f"SELECT pg_advisory_xact_lock({_BOOTSTRAP_LOCK})")
        try:
            return _bootstrap()
        finally:
            db.session.remove()


def _bootstrap():
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
