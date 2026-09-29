"""Training portal: staff-only practice, one trainee per shift, paused clock, isolation from the real system."""
import time

import pytest

from backend.app import create_app
from backend.config import DemoTestConfig
from backend.extensions import db

from .conftest import login


@pytest.fixture()
def demo_app():
    app = create_app(DemoTestConfig)
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def test_one_practice_attendant_per_shift_and_no_manager(demo_app):
    info = demo_app.test_client().get("/api/demo/info").json
    assert info["demo"] is True
    assert [(a["username"], a["shift"], a["role"]) for a in info["accounts"]] == [
        ("demo.morning", "Morning", "STAFF"), ("demo.night", "Night", "STAFF")]     # 2 shifts -> 2 trainees
    client = demo_app.test_client()
    r = client.post("/api/auth/login", json={"username": "training.system", "password": "x"})
    assert r.status_code == 403 and "staff practice only" in r.json["error"]


def test_each_trainee_practises_their_own_shift_with_a_paused_clock(demo_app):
    morning = login(demo_app, "demo.morning", "Demo1234")
    night = login(demo_app, "demo.night", "Demo1234")
    t1 = morning.get("/api/time").json
    assert t1["paused"] and t1["now"].endswith("T09:00:00") and t1["current_shift_type"] == "MORNING"
    t2 = night.get("/api/time").json
    assert t2["now"].endswith("T21:00:00") and t2["current_shift_type"] == "NIGHT"
    time.sleep(1.1)
    assert morning.get("/api/time").json["now"] == t1["now"]                  # time does not move

    shift = morning.get("/api/staff/my-shift").json["shift"]
    assert shift["shift_type"] == "MORNING" and shift["status"] == "SCHEDULED"
    started = morning.post(f"/api/shifts/{shift['id']}/start").json["shift"]
    pumps = {r["pump_name"] for r in started["readings"]}
    station_pumps = {p["pump_name"] for p in morning.get("/api/pumps").json["pumps"] if p["is_active"]}
    assert pumps == station_pumps and len(pumps) == 3                        # exactly the station's pumps
    assert started["stock"] == [] and started["tanks"] == []                  # staff never see tanks
    assert night.get("/api/staff/my-shift").json["shift"]["shift_type"] == "NIGHT"

    r = morning.post("/api/auth/change-password", json={"current_password": "Demo1234", "new_password": "Other1234"})
    assert r.status_code == 403


def test_reset_rebuilds_practice_data(demo_app):
    morning = login(demo_app, "demo.morning", "Demo1234")
    shift = morning.get("/api/staff/my-shift").json["shift"]
    morning.post(f"/api/shifts/{shift['id']}/start")
    assert demo_app.test_client().post("/api/demo/reset", json={}).status_code == 200
    morning = login(demo_app, "demo.morning", "Demo1234")
    assert morning.get("/api/staff/my-shift").json["shift"]["status"] == "SCHEDULED"


def test_real_system_refuses_training_actions(app, manager):
    assert manager.post("/api/demo/reset").status_code == 403
    info = app.test_client().get("/api/demo/info").json
    assert info["demo"] is False and "accounts" not in info
    assert manager.get("/api/time").json["paused"] is False          # the real clock always runs
