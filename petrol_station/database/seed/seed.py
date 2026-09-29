"""Create the database tables and the station's starting configuration.

    cd petrol_station
    python database/seed/seed.py

Safe to run repeatedly: it does nothing if the station already exists.
It creates: station, Petrol + Diesel, 3 starter pumps, 2,000 L low-stock
thresholds, expense categories and the first manager account
(MANAGER_USERNAME / MANAGER_PASSWORD from .env; default manager / ChangeMe123!).
No sales, prices or stock are invented - the system only reports real entries.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("SCHEDULER_ENABLED", "false")

from backend.app import create_app  # noqa: E402
from backend.models import Pump, Station, User  # noqa: E402

app = create_app()
with app.app_context():
    station = Station.query.first()
    print(f"Station: {station.name}")
    print(f"Pumps:   {', '.join(p.pump_name for p in Pump.query.all())}")
    print(f"Users:   {', '.join(f'{u.username} ({u.role})' for u in User.query.all())}")
    print("Database ready.")
