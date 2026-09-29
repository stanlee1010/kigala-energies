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

    # Secrets come only from the environment / .env (never from the code or git).
    # In production a missing SECRET_KEY stops the app (see create_app) instead of silently
    # inventing one that would log everybody out on every restart.
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    SECRET_KEY_FROM_ENV = bool(os.environ.get("SECRET_KEY"))

    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # HTTPS: when served on the internet set FORCE_HTTPS=true (http -> https redirect, HSTS,
    # Secure cookies). TRUST_PROXY=1 when behind a proxy/load balancer (Render, Nginx) so the
    # real client IP and https scheme are read from its X-Forwarded-* headers - never otherwise.
    FORCE_HTTPS = _bool("FORCE_HTTPS", False)
    TRUST_PROXY = int(os.environ.get("TRUST_PROXY", "0"))

    SESSION_COOKIE_HTTPONLY = True          # JavaScript can never read the session cookie
    SESSION_COOKIE_SAMESITE = "Lax"         # not sent on cross-site form posts
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False) or FORCE_HTTPS
    PERMANENT_SESSION_LIFETIME = timedelta(hours=int(os.environ.get("SESSION_HOURS", "12")))

    # Login protection (per client IP, on top of the 5-attempt account lock-out).
    LOGIN_RATE_LIMIT = int(os.environ.get("LOGIN_RATE_LIMIT", "20"))          # attempts ...
    LOGIN_RATE_WINDOW_SECONDS = int(os.environ.get("LOGIN_RATE_WINDOW", "600"))  # ... per 10 minutes

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
