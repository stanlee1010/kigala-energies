"""Configurable shift types (e.g. adding an Afternoon shift) and the regular staff roster."""
from datetime import date, datetime, timedelta

from backend.services.clock import today_local
from backend.services.shifts import locate


def shift_on(manager, day, shift_type, status=None):
    shifts = manager.get(f"/api/shifts?start={day}&end={day}" + (f"&status={status}" if status else "")).json["shifts"]
    return next((s for s in shifts if s["shift_type"] == shift_type), None)


def types_by_code(manager):
    return {t["code"]: t for t in manager.get("/api/shift-types").json["shift_types"]}


def test_roster_and_three_shift_pattern(app, manager):
    with app.app_context():
        day = today_local(1) + timedelta(days=3)   # a day whose shifts have certainly not started
    types = types_by_code(manager)
    assert [t["code"] for t in types.values() if t["is_active"]] == ["MORNING", "NIGHT"]

    # Choose John's regular shift once when creating him.
    r = manager.post("/api/users", json={"full_name": "John", "username": "john01", "password": "Temp1234",
                                         "shift_type_id": types["NIGHT"]["id"]})
    john = r.json["user"]["id"]
    manager.post("/api/shifts/generate", json={"start_date": str(day), "end_date": str(day)})
    night = manager.get(f"/api/shifts/{shift_on(manager, day, 'NIGHT')['id']}").json["shift"]
    assert [(s["user_id"], s["source"]) for s in night["staff"]] == [(john, "ROSTER")]
    assert shift_on(manager, day, "MORNING")["staff"] == []

    # Switch to 3 x 8 h shifts: Afternoon appears, Night now starts at 22:00, John stays on Night.
    res = manager.post("/api/shift-types/preset", json={"preset": "THREE"}).json
    active = [t for t in res["shift_types"] if t["is_active"]]
    assert [(t["code"], t["start_time"], t["end_time"], t["hours"]) for t in active] == [
        ("MORNING", "06:00", "14:00", 8), ("AFTERNOON", "14:00", "22:00", 8), ("NIGHT", "22:00", "06:00", 8)]
    listed = manager.get(f"/api/shifts?start={day}&end={day}").json["shifts"]
    assert [s["shift_type"] for s in listed] == ["NIGHT", "AFTERNOON", "MORNING"]   # newest first, by sequence
    night = shift_on(manager, day, "NIGHT")
    assert night["scheduled_start"].endswith("22:00:00") and night["staff"] == ["John"]

    with app.app_context():
        assert locate(1, datetime.combine(day, datetime.min.time()).replace(hour=15)) == (day, "AFTERNOON")
        assert locate(1, datetime.combine(day, datetime.min.time()).replace(hour=3)) == (day - timedelta(days=1), "NIGHT")

    # Move John to Afternoon: taken off upcoming Night, put on upcoming Afternoon.
    afternoon_id = types_by_code(manager)["AFTERNOON"]["id"]
    r = manager.put(f"/api/roster/{john}", json={"shift_type_id": afternoon_id}).json
    assert r["added_to"] >= 1 and r["removed_from"] >= 1
    assert shift_on(manager, day, "AFTERNOON")["staff"] == ["John"]
    assert shift_on(manager, day, "NIGHT")["staff"] == []

    # A manual choice for one day is kept when the roster changes.
    morning = shift_on(manager, day, "MORNING")
    manager.post(f"/api/shifts/{morning['id']}/assign", json={"user_id": john})
    manager.put(f"/api/roster/{john}", json={"shift_type_id": None})
    assert shift_on(manager, day, "MORNING")["staff"] == ["John"]
    assert shift_on(manager, day, "AFTERNOON")["staff"] == []

    # Switching Afternoon off cancels (never deletes) its upcoming shifts.
    res = manager.put(f"/api/shift-types/{afternoon_id}", json={"is_active": False}).json
    assert res["cancelled_shifts"] >= 1
    assert shift_on(manager, day, "AFTERNOON") is None
    assert shift_on(manager, day, "AFTERNOON", status="CANCELLED") is not None

    # Switching it back on revives the same shift rather than creating a duplicate.
    manager.put(f"/api/shift-types/{afternoon_id}", json={"is_active": True})
    manager.post("/api/shifts/generate", json={"start_date": str(day), "end_date": str(day)})
    revived = shift_on(manager, day, "AFTERNOON")
    assert revived and revived["status"] == "SCHEDULED"


def test_shift_type_validation(app, manager):
    types = types_by_code(manager)
    r = manager.post("/api/shift-types", json={"name": "Early", "start_time": "06:00"})
    assert r.status_code == 400 and "same time" in r.json["error"]
    manager.put(f"/api/shift-types/{types['NIGHT']['id']}", json={"is_active": False})
    r = manager.put(f"/api/shift-types/{types['MORNING']['id']}", json={"is_active": False})
    assert r.status_code == 400 and "At least one" in r.json["error"]
    # Only one shift a day: it runs 24 hours.
    t = types_by_code(manager)["MORNING"]
    assert t["hours"] == 24


def test_staff_cannot_change_shift_types(app, manager):
    manager.post("/api/users", json={"full_name": "Peter", "username": "peter02", "password": "Temp1234"})
    from .conftest import login
    peter = login(app, "peter02", "Temp1234")
    assert peter.post("/api/shift-types", json={"name": "Afternoon", "start_time": "14:00"}).status_code == 403
    assert peter.put("/api/roster/1", json={"shift_type_id": None}).status_code == 403
