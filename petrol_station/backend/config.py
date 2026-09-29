"""Application configuration.

Values are read from environment variables (or a `.env` file in the
project root). Local development falls back to SQLite; production should
set DATABASE_URL to a PostgreSQL or MySQL connection string, e.g.

    postgresql://user:pass@host:5432/petrol_station
    mysql+pymysql://user:pass@host:3306/petrol_station
"""
import os
import secrets
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")


def _bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _database_url():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return f"sqlite:///{(PROJECT_DIR / 'database' / 'petrol_station.db').as_posix()}"
    # Some hosts (Heroku/Render) still hand out the old "postgres://" scheme.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


class Config:
    ENV_NAME = os.environ.get("APP_ENV", "development")

    # Training portal: practice data in its own database, never the real one.
    DEMO_MODE = _bool("DEMO_MODE", False)
    # Real and training portals run on the same host (ports 8000/8001) and browsers
    # share cookies across ports, so each needs its own session cookie name.
    SESSION_COOKIE_NAME = "training_session" if DEMO_MODE else "session"
    # Training portal reads (never writes) the real station's set-up — shifts, pumps, tanks, prices —
    # so practice matches the real station.
    REAL_DATABASE_URL = os.environ.get("REAL_DATABASE_URL", "")

    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    PERMANENT_SESSION_LIFETIME = timedelta(hours=int(os.environ.get("SESSION_HOURS", "12")))

    FRONTEND_DIR = PROJECT_DIR / "frontend"
    UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", PROJECT_DIR / "uploads"))
    REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", PROJECT_DIR / "reports"))
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB receipts

    # Background monitor (shift generation, first-Wednesday detection, alerts).
    SCHEDULER_ENABLED = _bool("SCHEDULER_ENABLED", True)
    SCHEDULER_INTERVAL_SECONDS = int(os.environ.get("SCHEDULER_INTERVAL_SECONDS", "300"))

    # Used only when the database is first created.
    DEFAULT_TIMEZONE = os.environ.get("STATION_TIMEZONE", "Africa/Dar_es_Salaam")
    DEFAULT_CURRENCY = os.environ.get("STATION_CURRENCY", "TZS")
    DEFAULT_STATION_NAME = os.environ.get("STATION_NAME", "Kigala Energies")
    INITIAL_MANAGER_USERNAME = os.environ.get("MANAGER_USERNAME", "manager")
    INITIAL_MANAGER_PASSWORD = os.environ.get("MANAGER_PASSWORD", "ChangeMe123!")
    INITIAL_DIRECTOR_USERNAME = os.environ.get("DIRECTOR_USERNAME", "director")
    INITIAL_DIRECTOR_PASSWORD = os.environ.get("DIRECTOR_PASSWORD", "Director123!")

    TESTING = False


class TestConfig(Config):
    TESTING = True
    DEMO_MODE = False
    SESSION_COOKIE_NAME = "session"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    SCHEDULER_ENABLED = False
    SECRET_KEY = "test"


class DemoTestConfig(TestConfig):
    DEMO_MODE = True
    SESSION_COOKIE_NAME = "training_session"
