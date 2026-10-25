"""Training portal: staff-only practice, one trainee per shift, paused clock, isolation from the real system."""
import re
import time
from pathlib import Path

import pytest

from backend.app import create_app
from backend.config import DemoTestConfig, TestConfig
from backend.extensions import db

from .conftest import browser_client, login


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


def trainee(demo_app, username):
    """Sign in the way the training login page does: by shift, with no password."""
    client = browser_client(demo_app)
    r = client.post("/api/demo/login", json={"username": username})
    assert r.status_code == 200, r.json
    return client


def test_trainees_sign_in_without_any_password_in_the_browser(demo_app):
    info = demo_app.test_client().get("/api/demo/info").json
    assert all("password" not in a for a in info["accounts"])
    client = browser_client(demo_app)
    # practice passwords are random: nobody can type one, and the old shared one no longer works
    assert client.post("/api/auth/login", json={"username": "demo.morning", "password": "Demo1234"}).status_code == 401
    for name in ("training.system", "manager", "director", "nobody"):
        assert client.post("/api/demo/login", json={"username": name}).status_code == 404
    assert trainee(demo_app, "demo.morning").get("/api/auth/me").json["user"]["username"] == "demo.morning"


def test_no_password_is_written_in_the_frontend():
    front = Path(__file__).resolve().parents[1] / "frontend"
    for path in list(front.rglob("*.js")) + list(front.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        assert "Demo1234" not in text and not re.search(r"password\s*[:=]\s*[\"'][^\"']+[\"']", text), path.name


def test_each_trainee_practises_their_own_shift_with_a_paused_clock(demo_app):
    morning = trainee(demo_app, "demo.morning")
    night = trainee(demo_app, "demo.night")
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

    r = morning.post("/api/auth/change-password", json={"current_password": "x", "new_password": "Other1234"})
    assert r.status_code == 403


def test_reset_rebuilds_practice_data(demo_app):
    morning = trainee(demo_app, "demo.morning")
    shift = morning.get("/api/staff/my-shift").json["shift"]
    morning.post(f"/api/shifts/{shift['id']}/start")
    assert demo_app.test_client().post("/api/demo/reset", json={}).status_code == 200
    morning = trainee(demo_app, "demo.morning")
    assert morning.get("/api/staff/my-shift").json["shift"]["status"] == "SCHEDULED"


def test_real_system_refuses_training_actions(app, manager):
    assert manager.post("/api/demo/reset").status_code == 403
    assert browser_client(app).post("/api/demo/login", json={"username": "manager"}).status_code == 403
    info = app.test_client().get("/api/demo/info").json
    assert info["demo"] is False and "accounts" not in info
    assert manager.get("/api/time").json["paused"] is False          # the real clock always runs


def test_training_copies_people_per_shift_and_trainees_choose_pumps(tmp_path):
    """Real station with 2 people per shift -> 2 trainees per shift, each on their own pumps."""
    real_url = "sqlite:///" + (tmp_path / "real.db").as_posix()

    class RealConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = real_url

    class TrainingConfig(DemoTestConfig):
        REAL_DATABASE_URL = real_url

    real = create_app(RealConfig)
    manager = login(real, "manager", "ChangeMe123!")
    manager.post("/api/auth/change-password", json={"current_password": "ChangeMe123!", "new_password": "Manager123x"})
    for t in manager.get("/api/shift-types").json["shift_types"]:
        assert manager.put(f"/api/shift-types/{t['id']}", json={"staff_needed": 2}).status_code == 200
    with real.app_context():
        db.session.remove()
        db.engine.dispose()

    training = create_app(TrainingConfig)
    try:
        accounts = training.test_client().get("/api/demo/info").json["accounts"]
        assert [(a["username"], a["shift"], a["people"]) for a in accounts] == [
            ("demo.morning", "Morning", 2), ("demo.morning2", "Morning", 2),
            ("demo.night", "Night", 2), ("demo.night2", "Night", 2)]
        assert len({a["full_name"] for a in accounts}) == 4

        first, second = trainee(training, "demo.morning"), trainee(training, "demo.morning2")
        mine = first.get("/api/staff/my-shift").json
        assert mine["pump_choice"]["needed"] == 2 and mine["pump_choice"]["must_choose"]
        sid = mine["shift"]["id"]
        assert first.post(f"/api/shifts/{sid}/start").status_code == 400            # must choose pumps
        pumps = [p["id"] for p in mine["pump_choice"]["pumps"]]
        assert first.post(f"/api/shifts/{sid}/start", json={"pump_ids": pumps[:2]}).status_code == 200
        theirs = second.get("/api/staff/my-shift").json
        assert theirs["pump_choice"]["suggested"] == pumps[2:]
        ok = second.post("/api/staff/start-shift", json={"pump_ids": pumps[2:]})
        assert ok.status_code == 200 and [r["pump_id"] for r in ok.json["shift"]["readings"]] == pumps[2:]
    finally:
        with training.app_context():
            db.session.remove()
            db.drop_all()
