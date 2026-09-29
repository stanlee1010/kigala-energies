"""Training portal (demo) server — practice data only, separate database.

    python serve_demo.py            -> http://0.0.0.0:8001

Uses database/training.db and uploads/training/, so nothing done here can
touch the real station records served by serve.py on port 8000.
"""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
from dotenv import load_dotenv  # noqa: E402
load_dotenv(HERE / ".env")
# The real station database is only READ, to copy its shifts, pumps, tanks and prices.
os.environ["REAL_DATABASE_URL"] = os.environ.get("DATABASE_URL") or \
    f"sqlite:///{(HERE / 'database' / 'petrol_station.db').as_posix()}"
os.environ["DEMO_MODE"] = "true"
os.environ["DATABASE_URL"] = os.environ.get("TRAINING_DATABASE_URL") or \
    f"sqlite:///{(HERE / 'database' / 'training.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(HERE / "uploads" / "training")
# The training clock is paused, so the background monitor is not needed (it would only
# re-check a moment in time that never changes).
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ.setdefault("PORT", "8001")

from waitress import serve  # noqa: E402

from backend.app import create_app  # noqa: E402

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ["PORT"])
    print(f"TRAINING PORTAL (practice data only) running on http://{host}:{port}")
    serve(create_app(), host=host, port=port, threads=8)
