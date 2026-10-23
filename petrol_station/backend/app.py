"""Flask application factory.

Run for development (from the petrol_station folder):
    python -m backend.app
Run for production:
    python serve.py            (Waitress, works on Windows and Linux)
    gunicorn -w 1 --threads 8 "backend.app:create_app()"
"""
import logging
import os
from decimal import Decimal
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, request, send_from_directory
from sqlalchemy import event
from flask.json.provider import DefaultJSONProvider
from werkzeug.exceptions import HTTPException

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


def create_app(config_class=Config):
    frontend_dir = Path(config_class.FRONTEND_DIR)
    app = Flask(__name__, static_folder=None)
    app.config.from_object(config_class)
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
            return send_from_directory(frontend_dir, "404.html"), 404
        return exc

    @app.errorhandler(Exception)
    def handle_unexpected(exc):
        db.session.rollback()
        app.logger.exception("Unhandled error")
        return jsonify({"error": "Something went wrong on the server. The error has been logged."}), 500

    @app.after_request
    def security_headers(response):
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
        return response

    # ------------------------------------------------------------ frontend
    @app.get("/")
    def index():
        return send_from_directory(frontend_dir, "index.html")

    @app.get("/<path:path>")
    def frontend(path):
        if path.startswith("api/"):
            abort(404)
        target = frontend_dir / path
        if target.is_dir():
            path = f"{path.rstrip('/')}/index.html"
        return send_from_directory(frontend_dir, path)

    @app.get("/healthz")
    def health():
        db.session.execute(db.text("SELECT 1"))
        return jsonify({"status": "ok"})

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
