"""Storage tanks: two by default, manager changes the number, levels follow refills and pump sales."""
from backend.services.clock import now_local

from .conftest import login


def tank_by_name(manager, name):
    return next(t for t in manager.get("/api/tanks").json["tanks"] if t["name"] == name)


def test_default_tanks_and_count(app, manager):
    names = [t["name"] for t in manager.get("/api/tanks").json["tanks"]]
    assert sorted(names) == ["Diesel Tank 1", "Petrol Tank 1"]          # one tank per fuel = 2 tanks
    pumps = manager.get("/api/pumps").json["pumps"]
    assert all(p["tank"] == f"{p['fuel_type']} Tank 1" for p in pumps)

    fuels = {f["code"]: f["id"] for f in manager.get("/api/fuel-types").json["fuel_types"]}
    r = manager.put(f"/api/tanks/count/{fuels['DIESEL']}", json={"count": 2}).json
    assert r["added"] == ["Diesel Tank 2"]
    tank2 = tank_by_name(manager, "Diesel Tank 2")
    assert manager.put(f"/api/tanks/{tank2['id']}", json={"capacity_litres": 45000}).status_code == 200
    assert manager.put(f"/api/tanks/count/{fuels['DIESEL']}", json={"count": 0}).status_code == 400


def test_refill_into_a_tank_and_levels(app, manager):
    fuels = {f["code"]: f["id"] for f in manager.get("/api/fuel-types").json["fuel_types"]}
    manager.put(f"/api/tanks/count/{fuels['DIESEL']}", json={"count": 2})
    tank1, tank2 = tank_by_name(manager, "Diesel Tank 1"), tank_by_name(manager, "Diesel Tank 2")
    manager.post("/api/stock/adjustments", json={"fuel_type_id": fuels["DIESEL"], "tank_id": tank1["id"],
                                                 "adjustment_type": "OPENING_BALANCE", "quantity_litres": 5000,
                                                 "cost_per_litre": 2500, "reason": "dip"})
    manager.post("/api/users", json={"full_name": "Asha", "username": "asha01", "password": "Temp1234"})
    asha = login(app, "asha01", "Temp1234")
    staff_tanks = asha.get("/api/tanks").json["tanks"]
    assert {t["name"] for t in staff_tanks} >= {"Diesel Tank 1", "Diesel Tank 2"}
    assert all("litres" not in t and "capacity_litres" not in t for t in staff_tanks)   # names only for staff

    with app.app_context():
        now = now_local(1)
    r = asha.post("/api/refills", json={"fuel_type_id": fuels["DIESEL"], "tank_id": tank2["id"], "quantity_litres": 8000,
                                        "cost_per_litre": 2700, "supplier_name": "Puma", "invoice_number": "A1",
                                        "refill_date": now.date().isoformat(), "refill_time": now.strftime("%H:%M")})
    assert r.json["confirm"]["tank"] == "Diesel Tank 2"
    asha.post(f"/api/refills/{r.json['refill']['id']}/confirm", json={"answer": "YES"})

    assert tank_by_name(manager, "Diesel Tank 1")["litres"] == 5000
    assert tank_by_name(manager, "Diesel Tank 2")["litres"] == 8000
    # Wrong fuel for the tank is refused.
    petrol_tank = tank_by_name(manager, "Petrol Tank 1")
    bad = asha.post("/api/refills", json={"fuel_type_id": fuels["DIESEL"], "tank_id": petrol_tank["id"],
                                          "quantity_litres": 10, "cost_per_litre": 1, "supplier_name": "X",
                                          "invoice_number": "B", "refill_date": now.date().isoformat(),
                                          "refill_time": now.strftime("%H:%M")})
    assert bad.status_code == 400
    # A tank holding fuel cannot be removed.
    r = manager.put(f"/api/tanks/count/{fuels['DIESEL']}", json={"count": 1})
    assert r.status_code == 409 and "Diesel Tank 2" in r.json["error"]


def test_two_pumps_one_tank_and_capacity(app, manager):
    """The worked example: two pumps share one 10,000 L petrol tank."""
    fuels = {f["code"]: f["id"] for f in manager.get("/api/fuel-types").json["fuel_types"]}
    manager.post("/api/prices", json={"fuel_type_id": fuels["PETROL"], "price_per_litre": 3000})
    manager.put(f"/api/pumps/count/{fuels['PETROL']}", json={"count": 2})         # Pump 1 + Pump 2
    tank = tank_by_name(manager, "Petrol Tank 1")
    manager.put(f"/api/tanks/{tank['id']}", json={"capacity_litres": 10000})
    manager.post("/api/stock/adjustments", json={"fuel_type_id": fuels["PETROL"], "adjustment_type": "OPENING_BALANCE",
                                                 "quantity_litres": 10000, "cost_per_litre": 2500, "reason": "full tank"})
    shift_id = manager.get("/api/shifts").json["current_shift_id"]
    manager.post(f"/api/shifts/{shift_id}/start")
    pumps = sorted((p for p in manager.get("/api/pumps").json["pumps"] if p["fuel_code"] == "PETROL"),
                   key=lambda p: p["pump_number"])
    manager.put(f"/api/shifts/{shift_id}/readings/{pumps[0]['id']}", json={"opening_meter": 1000, "closing_meter": 1500})
    shift = manager.put(f"/api/shifts/{shift_id}/readings/{pumps[1]['id']}",
                        json={"opening_meter": 2000, "closing_meter": 2300}).json["shift"]

    # Each pump keeps its own meter record ...
    sales = {r["pump_name"]: (r["fuel_sold"], r["revenue"]) for r in shift["readings"] if r["fuel"] == "Petrol"}
    assert sales == {"Petrol Pump 1": (500, 1_500_000), "Petrol Pump 2": (300, 900_000)}
    # ... and the shared tank is Opening + Refills - Total sold.
    row = next(t for t in shift["tanks"] if t["tank"] == "Petrol Tank 1")
    assert (row["opening"], row["sold"], row["closing"]) == (10000, 800, 9200)
    assert row["pumps"] == [{"pump": "Petrol Pump 1", "sold": 500}, {"pump": "Petrol Pump 2", "sold": 300}]
    assert tank_by_name(manager, "Petrol Tank 1")["litres"] == 9200

    # 9,200 + 2,000 = 11,200 L > 10,000 L capacity -> refused.
    with app.app_context():
        now = now_local(1)
    refill = {"fuel_type_id": fuels["PETROL"], "quantity_litres": 2000, "cost_per_litre": 2600, "supplier_name": "Puma",
              "invoice_number": "X1", "refill_date": now.date().isoformat(), "refill_time": now.strftime("%H:%M")}
    r = manager.post("/api/refills", json=refill)
    assert r.status_code == 400 and r.json["title"] == "TANK CAPACITY EXCEEDED"
    assert "at most 800 L" in r.json["error"]
    # 800 L fits exactly.
    r = manager.post("/api/refills", json={**refill, "quantity_litres": 800})
    assert r.status_code == 201
    manager.post(f"/api/refills/{r.json['refill']['id']}/confirm", json={"answer": "YES"})
    assert tank_by_name(manager, "Petrol Tank 1")["litres"] == 10000


def test_pump_sales_come_out_of_its_tank(app, manager):
    fuels = {f["code"]: f["id"] for f in manager.get("/api/fuel-types").json["fuel_types"]}
    manager.post("/api/prices", json={"fuel_type_id": fuels["PETROL"], "price_per_litre": 3000})
    tank = tank_by_name(manager, "Petrol Tank 1")
    manager.post("/api/stock/adjustments", json={"fuel_type_id": fuels["PETROL"], "adjustment_type": "OPENING_BALANCE",
                                                 "quantity_litres": 6000, "cost_per_litre": 2500, "reason": "dip"})
    shift_id = manager.get("/api/shifts").json["current_shift_id"]
    manager.post(f"/api/shifts/{shift_id}/start")
    pump = next(p for p in manager.get("/api/pumps").json["pumps"] if p["fuel_code"] == "PETROL")
    manager.put(f"/api/shifts/{shift_id}/readings/{pump['id']}", json={"opening_meter": 100, "closing_meter": 1300})
    assert tank_by_name(manager, "Petrol Tank 1")["litres"] == 4800
    breakdown = manager.get(f"/api/tanks/shift/{shift_id}").json["tanks"]
    row = next(t for t in breakdown if t["tank_id"] == tank["id"])
    assert (row["opening"], row["sold"], row["closing"]) == (6000, 1200, 4800)
    assert row["pumps"] == [{"pump": pump["pump_name"], "sold": 1200}]
