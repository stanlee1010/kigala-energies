"""Manager sets the number of Diesel / Petrol pumps."""
from .conftest import login


def active_names(manager, code):
    return [p["pump_name"] for p in manager.get("/api/pumps").json["pumps"]
            if p["fuel_code"] == code and p["is_active"]]


def test_increase_and_decrease_pump_count(app, manager):
    fuels = {f["code"]: f["id"] for f in manager.get("/api/fuel-types").json["fuel_types"]}
    assert active_names(manager, "PETROL") == ["Petrol Pump 1"]

    r = manager.put(f"/api/pumps/count/{fuels['PETROL']}", json={"count": 3}).json
    assert r["added"] == ["Petrol Pump 2", "Petrol Pump 3"]
    assert active_names(manager, "PETROL") == ["Petrol Pump 1", "Petrol Pump 2", "Petrol Pump 3"]

    r = manager.put(f"/api/pumps/count/{fuels['DIESEL']}", json={"count": 1}).json
    assert r["deactivated"] == ["Diesel Pump 2"]
    assert active_names(manager, "DIESEL") == ["Diesel Pump 1"]

    # Increasing again re-activates the removed pump instead of creating a duplicate.
    r = manager.put(f"/api/pumps/count/{fuels['DIESEL']}", json={"count": 3}).json
    assert r["added"] == ["Diesel Pump 2", "Diesel Pump 3"]
    all_diesel = [p for p in manager.get("/api/pumps").json["pumps"] if p["fuel_code"] == "DIESEL"]
    assert len(all_diesel) == 3

    assert manager.put(f"/api/pumps/count/{fuels['DIESEL']}", json={"count": -1}).status_code == 400
    assert manager.put(f"/api/pumps/count/{fuels['DIESEL']}", json={"count": "two"}).status_code == 400


def test_open_shift_follows_pump_changes(app, manager):
    fuels = {f["code"]: f["id"] for f in manager.get("/api/fuel-types").json["fuel_types"]}
    manager.post("/api/prices", json={"fuel_type_id": fuels["PETROL"], "price_per_litre": 3000})
    shift_id = manager.get("/api/shifts").json["current_shift_id"]
    manager.post(f"/api/shifts/{shift_id}/start")

    # A pump added while the shift is running appears in that shift.
    manager.put(f"/api/pumps/count/{fuels['PETROL']}", json={"count": 2})
    names = [r["pump_name"] for r in manager.get(f"/api/shifts/{shift_id}").json["shift"]["readings"]]
    assert "Petrol Pump 2" in names

    # A pump with an opening reading but no closing reading cannot be removed.
    pump2 = next(p["id"] for p in manager.get("/api/pumps").json["pumps"] if p["pump_name"] == "Petrol Pump 2")
    manager.put(f"/api/shifts/{shift_id}/readings/{pump2}", json={"opening_meter": 500})
    r = manager.put(f"/api/pumps/count/{fuels['PETROL']}", json={"count": 1})
    assert r.status_code == 409 and "Petrol Pump 2" in r.json["error"]

    # After the closing reading it can be removed; its sales stay in the shift.
    manager.put(f"/api/shifts/{shift_id}/readings/{pump2}", json={"closing_meter": 600})
    assert manager.put(f"/api/pumps/count/{fuels['PETROL']}", json={"count": 1}).status_code == 200
    reading = next(r for r in manager.get(f"/api/shifts/{shift_id}").json["shift"]["readings"] if r["pump_id"] == pump2)
    assert reading["fuel_sold"] == 100


def test_staff_cannot_change_pump_count(app, manager):
    manager.post("/api/users", json={"full_name": "Peter", "username": "peter02", "password": "Temp1234"})
    peter = login(app, "peter02", "Temp1234")
    assert peter.put("/api/pumps/count/1", json={"count": 5}).status_code == 403
