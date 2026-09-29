"""Several locations, managers with several stations, and the director portal."""
from .conftest import login


def director(app):
    return login(app, "director", "Director123!")


def test_director_adds_station_and_manager_with_two_stations(app, manager):
    d = director(app)
    me = d.get("/api/auth/me").json["user"]
    assert me["role"] == "DIRECTOR" and me["must_change_password"] is True

    # Director adds a second location: it gets the standard set-up.
    r = d.post("/api/director/stations", json={"name": "Kigala Mbezi", "address": "Mbezi Beach"})
    assert r.status_code == 201
    mbezi = r.json["station"]
    assert mbezi["pumps"] == 3 and mbezi["managers"] == 0
    main_id = next(s["id"] for s in d.get("/api/director/stations").json["stations"] if s["id"] != mbezi["id"])

    # Director adds a manager who runs both stations.
    r = d.post("/api/director/managers", json={"full_name": "Neema", "username": "neema", "password": "Temp1234",
                                               "station_ids": [main_id, mbezi["id"]]})
    assert r.status_code == 201 and sorted(r.json["manager"]["station_ids"]) == sorted([main_id, mbezi["id"]])
    neema = login(app, "neema", "Temp1234")
    assert len(neema.get("/api/auth/me").json["user"]["stations"]) == 2

    # The manager switches station; the set-up and data of each station stay separate.
    fuels = {f["code"]: f["id"] for f in neema.get("/api/fuel-types").json["fuel_types"]}
    neema.post("/api/prices", json={"fuel_type_id": fuels["PETROL"], "price_per_litre": 3000})   # main station
    assert neema.post("/api/auth/station", json={"station_id": mbezi["id"]}).status_code == 200
    assert all(p["price_per_litre"] is None for p in neema.get("/api/prices").json["current"])  # Mbezi has none yet
    assert {t["name"] for t in neema.get("/api/tanks").json["tanks"]} == {"Diesel Tank 1", "Petrol Tank 1"}

    # Workers are created at a chosen location.
    r = neema.post("/api/users", json={"full_name": "Juma", "username": "juma01", "password": "Temp1234",
                                       "station_id": mbezi["id"]})
    assert r.status_code == 201 and r.json["user"]["station_id"] == mbezi["id"]
    listing = neema.get("/api/users").json
    juma = next(u for u in listing["users"] if u["username"] == "juma01")
    assert juma["station_name"] == "Kigala Mbezi"

    # Managers cannot create managers or reach a station they do not run.
    assert neema.post("/api/users", json={"full_name": "X", "username": "xmgr", "password": "Temp1234",
                                          "role": "MANAGER"}).status_code == 403
    assert manager.post("/api/users", json={"full_name": "Y", "username": "yy01", "password": "Temp1234",
                                            "station_id": mbezi["id"]}).status_code == 403
    assert manager.post("/api/auth/station", json={"station_id": mbezi["id"]}).status_code == 403

    # Portals are separate.
    assert neema.get("/api/director/overview").status_code == 403
    assert d.get("/api/dashboard/manager").status_code == 403

    # Director removes the manager: they can no longer log in.
    manager_id = r_id = next(m["id"] for m in d.get("/api/director/managers").json["managers"] if m["username"] == "neema")
    assert d.post(f"/api/director/managers/{r_id}/disable").status_code == 200
    blocked = app.test_client().post("/api/auth/login", json={"username": "neema", "password": "Temp1234"})
    assert blocked.status_code == 403
    assert manager_id


def test_director_sees_sales_and_profit_per_location_or_all(app, manager):
    d = director(app)
    mbezi = d.post("/api/director/stations", json={"name": "Kigala Mbezi"}).json["station"]
    fuels = {f["code"]: f["id"] for f in manager.get("/api/fuel-types").json["fuel_types"]}
    manager.post("/api/prices", json={"fuel_type_id": fuels["PETROL"], "price_per_litre": 3000})
    manager.post("/api/stock/adjustments", json={"fuel_type_id": fuels["PETROL"], "adjustment_type": "OPENING_BALANCE",
                                                 "quantity_litres": 5000, "cost_per_litre": 2500, "reason": "dip"})
    shift_id = manager.get("/api/shifts").json["current_shift_id"]
    manager.post(f"/api/shifts/{shift_id}/start")
    pump = next(p for p in manager.get("/api/pumps").json["pumps"] if p["fuel_code"] == "PETROL")
    manager.put(f"/api/shifts/{shift_id}/readings/{pump['id']}", json={"opening_meter": 0, "closing_meter": 400})

    all_ = d.get("/api/director/overview").json
    assert len(all_["locations"]) == 2
    for period in ("today", "week", "month", "year"):
        p = all_["periods"][period]
        assert p["revenue"] == 1_200_000 and p["fuel_sold_litres"] == 400
        assert p["gross_profit"] == 200_000                     # 400 x (3000 - 2500)
    assert all_["periods"]["today"]["by_fuel"] == [{"fuel": "Petrol", "color": "#16a34a", "litres": 400, "sales": 1_200_000}]
    rows = {r["name"]: r for r in all_["by_location"]}
    assert rows["Kigala Mbezi"]["month"]["revenue"] == 0

    only_mbezi = d.get(f"/api/director/overview?station={mbezi['id']}").json
    assert only_mbezi["periods"]["year"]["revenue"] == 0

    chart = d.get("/api/director/charts?granularity=monthly").json
    assert sum(chart["revenue"]) == 1_200_000
