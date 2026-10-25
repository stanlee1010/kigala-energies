"""Flask application factory.

Run for development (from the petrol_station folder):
    python -m backend.app
Run for production:
    python serve.py            (Waitress, works on Windows and Linux)
    gunicorn -w 1 --threads 8 "backend.app:create_app()"
"""
import hashlib
import logging
import os
import re
import time
from decimal import Decimal
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, request, send_from_directory
from flask.sessions import SecureCookieSessionInterface
from sqlalchemy import event
from flask.json.provider import DefaultJSONProvider
from werkzeug.exceptions import HTTPException
from werkzeug.security import safe_join

from .config import Config
from .extensions import db
from .models.base import serialize
from .routes.common import ApiError


class JSONProvider(DefaultJSONProvider):
    sort_keys = False

    @staticmethod
    def default(o):
        if isinstance(o, Decimal):
            return float(o)
        value = serialize(o)
        if value is o:
            return DefaultJSONProvider.default(o)
        return value


IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9-]{16,64}")


def _tune_sqlite(engine):
    """Local SQLite: let many people work at once. WAL lets readers carry on while one person
    saves; busy_timeout makes a second writer wait its turn instead of failing."""
    if engine.url.get_backend_name() != "sqlite":
        return
    in_memory = engine.url.database in (None, "", ":memory:")
    read_only = "mode=ro" in str(engine.url)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA busy_timeout=15000")
        if not in_memory and not read_only:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


class ApiOnlySessionInterface(SecureCookieSessionInterface):
    """Only API replies send or refresh the login cookie.

    Page files (HTML, CSS, JS, images) then carry nothing personal, so a CDN may keep one copy for
    everyone. Every page calls the API, which keeps the login fresh.
    """

    def save_session(self, app, session, response):
        if request.path.startswith("/api/"):
            super().save_session(app, session, response)


def create_app(config_class=Config):
    frontend_dir = Path(config_class.FRONTEND_DIR)
    app = Flask(__name__, static_folder=None)
    app.config.from_object(config_class)
    app.session_interface = ApiOnlySessionInterface()
    if app.config["VERCEL"]:
        if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:"):
            raise RuntimeError("Vercel deployments require DATABASE_URL to point to a persistent PostgreSQL database")
        if app.config["INITIAL_MANAGER_PASSWORD"] == "ChangeMe123!":
            raise RuntimeError("Set a unique MANAGER_PASSWORD in the Vercel project environment")
        if app.config["INITIAL_DIRECTOR_PASSWORD"] == "Director123!":
            raise RuntimeError("Set a unique DIRECTOR_PASSWORD in the Vercel project environment")
    if app.config["ENV_NAME"] == "production" and not app.config.get("SECRET_KEY_FROM_ENV") and not app.testing:
        raise RuntimeError("SECRET_KEY must be set in the environment (.env) when APP_ENV=production")
    if app.config.get("TRUST_PROXY"):
        # Behind a trusted load balancer, use its client IP and HTTPS headers.
        from werkzeug.middleware.proxy_fix import ProxyFix
        hops = app.config["TRUST_PROXY"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)
    app.json_provider_class = JSONProvider
    app.json = JSONProvider(app)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    db.init_app(app)
    with app.app_context():
        _tune_sqlite(db.engine)

    from .auth.routes import bp as auth_bp
    from .routes import (alerts, capital, dashboard, demo, director, expenses, fuel, pumps, refills, reports, roster,
                         shifts, tanks, users)
    app.register_blueprint(auth_bp)
    for module in (users, pumps, tanks, fuel, capital, shifts, roster, refills, expenses, alerts, dashboard, reports,
                   director, demo):
        app.register_blueprint(module.bp)

    STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}

    @app.before_request
    def enforce_https_and_same_origin():
        # Force HTTPS on the internet (the health check stays reachable for the host's probe).
        if app.config.get("FORCE_HTTPS") and not request.is_secure and request.path != "/healthz":
            return redirect(request.url.replace("http://", "https://", 1), code=308)
        # Rate limit every API route per device; block a device that keeps attacking.
        if request.path.startswith("/api/"):
            from .services import guard
            from .services.audit import client_ip
            refused = guard.check_api_request(client_ip())
            if refused:
                status, message, retry_after = refused
                response = jsonify({"error": message, "retry_after": retry_after})
                response.headers["Retry-After"] = str(retry_after)
                return response, status
        # Block cross-site request forgery: a state-changing API call must be JSON or carry the
        # X-Requested-With header that only this site's own JavaScript sends (browsers do not let
        # other websites add it without permission).
        if request.path.startswith("/api/") and request.method in STATE_CHANGING:
            if not request.is_json and request.headers.get("X-Requested-With") != "fetch":
                return jsonify({"error": "Request blocked: it did not come from this website."}), 403
        return None

    @app.errorhandler(ApiError)
    def handle_api_error(exc):
        db.session.rollback()
        return jsonify(exc.to_dict()), exc.status

    @app.errorhandler(ValueError)
    @app.errorhandler(TypeError)
    def handle_bad_value(exc):
        # e.g. letters sent where a number is expected: a clear 400, not a server error.
        db.session.rollback()
        if request.path.startswith("/api/"):
            app.logger.warning("Rejected invalid input on %s: %s", request.path, exc, exc_info=True)
            return jsonify({"error": "Some of the information sent is not valid. Please check and try again."}), 400
        raise exc

    @app.errorhandler(HTTPException)
    def handle_http(exc):
        if request.path.startswith("/api/"):
            return jsonify({"error": exc.description}), exc.code
        if exc.code == 404:
            return send_page("404.html"), 404
        return exc

    @app.errorhandler(Exception)
    def handle_unexpected(exc):
        db.session.rollback()
        app.logger.exception("Unhandled error")
        return jsonify({"error": "Something went wrong on the server. The error has been logged."}), 500

    versions = {"checked": 0.0, "stamp": None, "v": ""}

    def app_version():
        """A short fingerprint of every frontend file. File times are looked at every 10 seconds
        (files can change without a server restart); the contents are hashed only when they moved."""
        now = time.monotonic()
        if versions["v"] and now - versions["checked"] < 10:
            return versions["v"]
        versions["checked"] = now
        files = sorted(f for f in frontend_dir.rglob("*") if f.is_file())
        stamp = [(f.as_posix(), f.stat().st_mtime, f.stat().st_size) for f in files]
        if stamp != versions["stamp"]:
            digest = hashlib.sha256()
            for f in files:
                digest.update(f.relative_to(frontend_dir).as_posix().encode() + b"\0" + f.read_bytes())
            versions["stamp"], versions["v"] = stamp, digest.hexdigest()[:12]
        return versions["v"]

    @app.after_request
    def security_headers(response):
        # Changes whenever a page file changes: phones then refresh their saved copies (see sw.js).
        response.headers["X-App-Version"] = app_version()
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        # A private tool: search engines must not list it (see also /robots.txt and the page meta tags).
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy",
                                    "geolocation=(), microphone=(), payment=(), usb=(), camera=(self)")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; font-src 'self'; "
            "img-src 'self' data: blob:; connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'")
        if app.config.get("FORCE_HTTPS"):
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        else:
            # Pages, scripts and styles: always check for a newer version (cheap 304 if unchanged),
            # so every phone gets updates immediately instead of an old cached copy.
            response.headers["Cache-Control"] = "no-cache"
            # Chrome/Edge: build the page behind a link the moment it is touched or hovered, so it
            # opens at once when tapped (rules in frontend/speculation-rules.json).
            if response.mimetype == "text/html":
                response.headers["Speculation-Rules"] = '"/speculation-rules.json"'
            # Vercel: keep the files in the nearby CDN so pages open fast (each deploy starts a fresh
            # cache, so updates still show at once). Never for anything tied to a login.
            if (app.config.get("VERCEL") and response.status_code == 200 and "Set-Cookie" not in response.headers
                    and "Cookie" not in response.headers.get("Vary", "")):
                response.headers["Vercel-CDN-Cache-Control"] = "max-age=31536000"
        return response

    # ------------------------------------------------------------ frontend
    etags = {}

    def send_page(path):
        """A frontend file whose ETag comes from its contents (file times are not kept on every host)."""
        safe = safe_join(str(frontend_dir), path)
        target = Path(safe) if safe else None
        try:
            stat = target.stat()
            if not target.is_file():
                raise OSError
        except (OSError, AttributeError):
            return send_from_directory(frontend_dir, path)   # 404 page
        key = (path, stat.st_mtime, stat.st_size)
        if etags.get(path, (None,))[0] != key:
            etags[path] = (key, hashlib.sha256(target.read_bytes()).hexdigest()[:20])
        mimetype = "application/speculationrules+json" if path == "speculation-rules.json" else None
        return send_from_directory(frontend_dir, path, etag=etags[path][1], mimetype=mimetype)

    @app.get("/")
    def index():
        return send_page("index.html")

    @app.get("/<path:path>")
    def frontend(path):
        if path.startswith("api/"):
            abort(404)
        target = frontend_dir / path
        if target.is_dir():
            path = f"{path.rstrip('/')}/index.html"
        return send_page(path)

    @app.get("/healthz")
    def health():
        db.session.execute(db.text("SELECT 1"))
        return jsonify({"status": "ok"})

    if app.config.get("RUN_CHECKS_ON_REQUESTS"):
        last_checks = {"at": None}

        @app.before_request
        def run_station_checks():
            # Idempotent checks (upcoming shifts, periods, missing shifts, stock, refills): safe to
            # run from several instances. A failure is logged and never breaks the person's request.
            if not request.path.startswith("/api/") or request.path.startswith("/api/demo"):
                return None
            now = time.monotonic()
            if last_checks["at"] is not None and now - last_checks["at"] < app.config["SCHEDULER_INTERVAL_SECONDS"]:
                return None
            last_checks["at"] = now
            from .services.monitor import run_all_stations
            try:
                run_all_stations()
            except Exception:
                db.session.rollback()
                app.logger.exception("Station checks failed")
            return None

    # Registered last, so nothing else commits before the request's own work: the key is saved in
    # the same transaction as the change (and rolled back with it if the save fails).
    @app.before_request
    def apply_each_save_once():
        key = request.headers.get("Idempotency-Key")
        if not key or request.method not in STATE_CHANGING or not request.path.startswith("/api/"):
            return None
        if not IDEMPOTENCY_KEY.fullmatch(key):
            return jsonify({"error": "Invalid Idempotency-Key"}), 400
        from .models import ProcessedRequest
        if db.session.get(ProcessedRequest, key) is not None:
            return jsonify({"ok": True, "already_saved": True})      # sent again: already applied
        now = int(time.time())
        db.session.add(ProcessedRequest(key=key, created_at=now))
        if now % 50 == 0:   # now and then: forget keys older than 60 days
            db.session.query(ProcessedRequest).filter(ProcessedRequest.created_at < now - 60 * 86400).delete()
        return None

    register_cli(app)

    with app.app_context():
        from .services.bootstrap import bootstrap
        created = bootstrap()
        if app.config["DEMO_MODE"]:
            from .models import Station
            from .services.clock import paused_at
            from .services.demo import needs_rebuild, reset, scramble_passwords, seed
            if created:
                seed()
            else:
                station_id = Station.query.first().id
                if paused_at(station_id) is None or needs_rebuild(station_id):
                    reset()   # older practice data, or the real station's shifts/pumps/tanks changed
                scramble_passwords()
                db.session.commit()
            app.logger.warning("Training portal: practice data ready (trainees sign in by choosing their shift)")
        elif created:
            app.logger.warning("Created station and initial manager account '%s' - change the password at first login.",
                               app.config["INITIAL_MANAGER_USERNAME"])

    from .services import scheduler
    scheduler.start(app)
    return app


def register_cli(app):
    @app.cli.command("check")
    def run_checks_command():
        """Run the real-time business checks once (shifts, periods, alerts)."""
        from .services.monitor import run_all_stations
        run_all_stations()
        print("Checks completed.")

    @app.cli.command("reset-manager-password")
    def reset_manager_password():
        """Reset the first manager account's password (prompts for a new one)."""
        import getpass
        from .models import User
        user = User.query.filter_by(role="MANAGER").order_by(User.id).first()
        password = getpass.getpass(f"New password for {user.username}: ")
        user.set_password(password)
        user.must_change_password = True
        user.is_active = True
        user.locked_until = None
        db.session.commit()
        print("Password updated.")


if __name__ == "__main__":
    application = create_app()
    application.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")),
                    debug=os.environ.get("FLASK_DEBUG", "1") == "1")
