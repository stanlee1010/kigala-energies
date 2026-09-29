"""Lightweight background thread that runs the business monitor periodically.

For multi-process deployments run a single process with the scheduler enabled
(SCHEDULER_ENABLED=true) and disable it elsewhere; the checks are idempotent
so an occasional double run is harmless.
"""
import logging
import os
import threading
import time

log = logging.getLogger(__name__)
_started = False


def start(app):
    global _started
    if _started or not app.config.get("SCHEDULER_ENABLED"):
        return
    # Flask's debug reloader imports the app twice; only run in the child.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    _started = True
    interval = app.config["SCHEDULER_INTERVAL_SECONDS"]

    def loop():
        from .monitor import run_all_stations
        while True:
            with app.app_context():
                try:
                    run_all_stations()
                except Exception:
                    log.exception("Background checks failed")
            time.sleep(interval)

    threading.Thread(target=loop, name="station-monitor", daemon=True).start()
    log.info("Station monitor started (every %ss)", interval)
