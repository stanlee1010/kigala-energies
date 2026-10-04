"""Staff press "Start shift" and are put on the shift that is running now (by the server clock)."""
from .conftest import login


def _staff(app, manager, username):
    manager.post("/api/users", json={"full_name": username.title(), "username": username, "password": "Temp1234"})
    return login(app, username, "Temp1234")


def test_staff_start_the_shift_running_now_and_others_join_it(app, manager):
    asha, juma = _staff(app, manager, "asha01"), _staff(app, manager, "juma01")

    view = asha.get("/api/staff/my-shift").json
    cur = view["current_shift"]
    assert view["shift"] is None and cur["can_start"] and cur["status"] == "SCHEDULED"

    shift = asha.post("/api/staff/start-shift").json["shift"]
    assert shift["id"] == cur["id"] and shift["status"] == "OPEN" and shift["readings"]
    mine = asha.get("/api/staff/my-shift").json
    assert mine["shift"]["id"] == cur["id"] and mine["is_current"]

    # A second attendant joins the open shift instead of opening a new one.
    assert juma.get("/api/staff/my-shift").json["current_shift"]["can_start"]
    assert juma.post("/api/staff/start-shift").json["shift"]["id"] == cur["id"]
    staff = manager.get(f"/api/shifts/{cur['id']}").json["shift"]["staff"]
    assert {(s["full_name"], s["source"]) for s in staff} == {("Asha01", "SELF"), ("Juma01", "SELF")}
    assert asha.post("/api/staff/start-shift").status_code == 200          # pressing twice is harmless
    assert len(manager.get(f"/api/shifts/{cur['id']}").json["shift"]["staff"]) == 2


def test_start_shift_rules(app, manager):
    asha = _staff(app, manager, "asha01")
    assert manager.post("/api/staff/start-shift").status_code == 403        # managers assign shifts instead
    cur = asha.post("/api/staff/start-shift").json["shift"]
    with app.app_context():
        from backend.extensions import db
        from backend.models import Shift
        db.session.get(Shift, cur["id"]).status = "SUBMITTED"
        db.session.commit()
    late = _staff(app, manager, "late01")
    view = late.get("/api/staff/my-shift").json
    assert view["shift"] is None and not view["current_shift"]["can_start"]
    assert late.post("/api/staff/start-shift").status_code == 409


def test_staff_must_submit_an_older_open_shift_first(app, manager):
    asha = _staff(app, manager, "asha01")
    cur_id = asha.get("/api/staff/my-shift").json["current_shift"]["id"]
    with app.app_context():
        from backend.extensions import db
        from backend.models import Shift, ShiftStaff, User
        from datetime import timedelta
        from backend.services.shifts import get_or_create_shift
        cur = db.session.get(Shift, cur_id)
        prev = get_or_create_shift(cur.station_id, cur.shift_date - timedelta(days=1), cur.shift_type)[0]
        prev.status = "OPEN"
        prev.staff.append(ShiftStaff(user_id=User.query.filter_by(username="asha01").one().id))
        prev_id = prev.id
        db.session.commit()
    view = asha.get("/api/staff/my-shift").json
    assert view["shift"]["id"] == prev_id and not view["current_shift"]["can_start"]
    r = asha.post("/api/staff/start-shift")
    assert r.status_code == 409 and "submit" in r.json["error"]
