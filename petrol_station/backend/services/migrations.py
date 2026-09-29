"""In-place schema upgrades for existing databases.

db.create_all() creates new tables but never changes existing ones, so each
upgrade here checks the live schema and only applies what is missing. All
steps are idempotent. SQLite databases are copied to database/backups/ before
any table is rebuilt.

002 - configurable shift types (Morning / Afternoon / Night ...) and roster:
      * shifts: drop the MORNING/NIGHT-only check, allow CANCELLED, add `sequence`
      * shift_staff.source, users.default_shift_definition_id
      * shift_definitions seeded from the old morning_start / night_start settings
"""
import logging
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateIndex, CreateTable

from ..extensions import db
from ..models import Shift, Station

log = logging.getLogger(__name__)


def _columns(inspector, table):
    return {c["name"] for c in inspector.get_columns(table)}


def _backup_sqlite():
    url = db.engine.url
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    source = Path(url.database)
    target_dir = source.parent / "backups"
    target_dir.mkdir(exist_ok=True)
    target = target_dir / f"{source.stem}_{datetime.now():%Y%m%d_%H%M%S}_before_upgrade{source.suffix}"
    db.session.commit()
    db.engine.dispose()
    shutil.copy2(source, target)
    log.warning("Database backed up to %s", target)
    return target


def _rebuild_sqlite_shifts(conn):
    """SQLite cannot drop a CHECK constraint: rebuild the table (official 12-step procedure)."""
    ddl = str(CreateTable(Shift.__table__).compile(dialect=conn.dialect)).strip()
    ddl = ddl.replace("CREATE TABLE shifts", "CREATE TABLE shifts__new", 1)
    old_cols = [c["name"] for c in inspect(conn).get_columns("shifts")]
    new_cols = [c.name for c in Shift.__table__.columns]
    common = [c for c in new_cols if c in old_cols]
    select_cols = list(common)
    insert_cols = list(common)
    if "sequence" not in old_cols:
        insert_cols.append("sequence")
        select_cols.append("CASE shift_type WHEN 'MORNING' THEN 1 ELSE 2 END")
    conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
    conn.exec_driver_sql(ddl)
    conn.exec_driver_sql(f"INSERT INTO shifts__new ({', '.join(insert_cols)}) SELECT {', '.join(select_cols)} FROM shifts")
    conn.exec_driver_sql("DROP TABLE shifts")
    conn.exec_driver_sql("ALTER TABLE shifts__new RENAME TO shifts")
    for index in Shift.__table__.indexes:
        conn.exec_driver_sql(str(CreateIndex(index).compile(dialect=conn.dialect)))
    conn.exec_driver_sql("PRAGMA foreign_keys=ON")


def upgrade_002_shift_definitions():
    inspector = inspect(db.engine)
    dialect = db.engine.dialect.name
    shift_cols = _columns(inspector, "shifts")
    checks = {c.get("name") for c in inspector.get_check_constraints("shifts")}
    sqlite_sql = ""
    if dialect == "sqlite":
        with db.engine.connect() as conn:
            sqlite_sql = conn.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='shifts'").scalar() or ""
    needs_shift_rebuild = ("sequence" not in shift_cols or "ck_shift_type" in checks
                           or (dialect == "sqlite" and "ck_shift_type" in sqlite_sql))
    needs_staff_source = "source" not in _columns(inspector, "shift_staff")
    needs_user_default = "default_shift_definition_id" not in _columns(inspector, "users")

    if needs_shift_rebuild or needs_staff_source or needs_user_default:
        _backup_sqlite()
        with db.engine.begin() as conn:
            if needs_shift_rebuild:
                if dialect == "sqlite":
                    _rebuild_sqlite_shifts(conn)
                else:
                    drop = "DROP CHECK" if dialect == "mysql" else "DROP CONSTRAINT IF EXISTS"
                    conn.exec_driver_sql(f"ALTER TABLE shifts {drop} ck_shift_type")
                    conn.exec_driver_sql(f"ALTER TABLE shifts {drop} ck_shift_status")
                    conn.exec_driver_sql(
                        "ALTER TABLE shifts ADD CONSTRAINT ck_shift_status CHECK "
                        "(status IN ('SCHEDULED','OPEN','SUBMITTED','FLAGGED','CLOSED','CANCELLED'))")
                    if dialect == "postgresql":
                        conn.exec_driver_sql("ALTER TABLE shifts ALTER COLUMN shift_type TYPE VARCHAR(30)")
                    else:
                        conn.exec_driver_sql("ALTER TABLE shifts MODIFY shift_type VARCHAR(30) NOT NULL")
                    if "sequence" not in shift_cols:
                        conn.exec_driver_sql("ALTER TABLE shifts ADD COLUMN sequence INTEGER NOT NULL DEFAULT 1")
                        conn.exec_driver_sql("UPDATE shifts SET sequence = 2 WHERE shift_type = 'NIGHT'")
            if needs_staff_source:
                conn.exec_driver_sql("ALTER TABLE shift_staff ADD COLUMN source VARCHAR(10) NOT NULL DEFAULT 'MANUAL'")
            if needs_user_default:
                conn.exec_driver_sql("ALTER TABLE users ADD COLUMN default_shift_definition_id INTEGER "
                                     "REFERENCES shift_definitions(id)")
        log.warning("Database upgraded: configurable shift types and staff roster")

    # Seed shift types for existing stations from their old Morning/Night times.
    from .settings import get_setting
    from .shifts import ensure_default_definitions
    for station in Station.query.all():
        ensure_default_definitions(station.id, get_setting(station.id, "morning_start"),
                                   get_setting(station.id, "night_start"))
    db.session.commit()


def upgrade_003_tanks():
    """Storage tanks: pumps, refills and stock adjustments are linked to a tank."""
    inspector = inspect(db.engine)
    missing = [(table, "tank_id") for table in ("pumps", "fuel_refills", "stock_adjustments", "pump_readings")
               if "tank_id" not in _columns(inspector, table)]
    if missing:
        _backup_sqlite()
        with db.engine.begin() as conn:
            for table, column in missing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} INTEGER REFERENCES tanks(id)")
        log.warning("Database upgraded: storage tanks")
    from .tanks import ensure_default_tanks
    for station in Station.query.all():
        ensure_default_tanks(station.id)
    db.session.commit()


def _rebuild_sqlite_table(conn, table):
    """Recreate a SQLite table from the current model (to change CHECK constraints), keeping every row."""
    ddl = str(CreateTable(table).compile(dialect=conn.dialect)).strip()
    ddl = ddl.replace(f"CREATE TABLE {table.name}", f"CREATE TABLE {table.name}__new", 1)
    old_cols = {c["name"] for c in inspect(conn).get_columns(table.name)}
    cols = [c.name for c in table.columns if c.name in old_cols]
    conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
    conn.exec_driver_sql(ddl)
    conn.exec_driver_sql(f"INSERT INTO {table.name}__new ({', '.join(cols)}) SELECT {', '.join(cols)} FROM {table.name}")
    conn.exec_driver_sql(f"DROP TABLE {table.name}")
    conn.exec_driver_sql(f"ALTER TABLE {table.name}__new RENAME TO {table.name}")
    for index in table.indexes:
        conn.exec_driver_sql(str(CreateIndex(index).compile(dialect=conn.dialect)))
    conn.exec_driver_sql("PRAGMA foreign_keys=ON")


def upgrade_004_directors_and_locations():
    """Several locations: the DIRECTOR role, managers' station access, the first director account."""
    from ..models import User
    dialect = db.engine.dialect.name
    if dialect == "sqlite":
        with db.engine.connect() as conn:
            sql = conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").scalar() or ""
        needs = "DIRECTOR" not in sql
    else:
        checks = {c.get("name"): c.get("sqltext", "") for c in inspect(db.engine).get_check_constraints("users")}
        needs = "DIRECTOR" not in str(checks.get("ck_users_role", "DIRECTOR"))
    if needs:
        _backup_sqlite()
        with db.engine.begin() as conn:
            if dialect == "sqlite":
                _rebuild_sqlite_table(conn, User.__table__)
            else:
                drop = "DROP CHECK" if dialect == "mysql" else "DROP CONSTRAINT IF EXISTS"
                conn.exec_driver_sql(f"ALTER TABLE users {drop} ck_users_role")
                conn.exec_driver_sql("ALTER TABLE users ADD CONSTRAINT ck_users_role "
                                     "CHECK (role IN ('STAFF','MANAGER','DIRECTOR'))")
        log.warning("Database upgraded: several locations and the director role")
    first = Station.query.order_by(Station.id).first()
    if first is not None:
        from flask import current_app
        from .stations import ensure_director, ensure_manager_access
        ensure_manager_access()
        created = ensure_director(current_app.config["INITIAL_DIRECTOR_USERNAME"],
                                  current_app.config["INITIAL_DIRECTOR_PASSWORD"], first.id)
        if created:
            log.warning("Created director account '%s' - change the password at first login.", created.username)
    db.session.commit()


def run_all():
    upgrade_002_shift_definitions()
    upgrade_003_tanks()
    upgrade_004_directors_and_locations()
