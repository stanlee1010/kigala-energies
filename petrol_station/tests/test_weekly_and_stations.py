"""Weekly shift choice (changes start on Monday), online money history as typed, and
the director removing / restoring stations."""
from datetime import date, timedelta

from .conftest import login


def _types(client):
    return {t["code"]: t["id"] for t in client.get("/api/shift-types").json["shift_types"] if t["is_active"]}


def test_staff_choose_next_weeks_shift_from_monday(app, manager):
    types = _types(manager)
    manager.post("/api/users", json={"full_name": "Asha", "username": "asha01", "password": "Temp1234",
                                     "shift_type_id": types["MORNING"]})          # regular shift: Morning
    asha = login(app, "asha01", "Temp1234")
    w = asha.get("/api/staff/week-choice").json
    monday = date.fromisoformat(w["next_week"]["start"])
    assert monday.weekday() == 0 and w["next_week"]["chosen"] is False
    assert w["next_week"]["shift"] == "Morning"                                       # regular shift until she chooses

    r = asha.put("/api/staff/week-choice", json={"shift_type_id": types["NIGHT"]}).json
    assert r["shift"] == "Night" and r["shifts_assigned"] == 7                        # every night Mon..Sun

    week = manager.get(f"/api/shifts?start={monday}&end={monday + timedelta(days=6)}").json["shifts"]
    on = {(s["shift_date"], s["shift_type"]) for s in week if "Asha" in s["staff"]}
    assert on == {((monday + timedelta(days=i)).isoformat(), "NIGHT") for i in range(7)}
    # The week after goes back to her regular Morning shift.
    after = monday + timedelta(days=7)
    manager.post("/api/shifts/generate", json={"start_date": str(after), "end_date": str(after)})
    nxt = manager.get(f"/api/shifts?start={after}&end={after}").json["shifts"]
    assert any(s["shift_type"] == "MORNING" and "Asha" in s["staff"] for s in nxt)

    plan = manager.get("/api/roster/week-choices").json
    row = next(s for s in plan["staff"] if s["username"] == "asha01")
    assert (row["shift"], row["chosen"]) == ("Night", True) and plan["counts"]["Night"] == 1

    # Changing her mind before Monday moves her.
    asha.put("/api/staff/week-choice", json={"shift_type_id": types["MORNING"]})
    week = manager.get(f"/api/shifts?start={monday}&end={monday + timedelta(days=6)}").json["shifts"]
    assert all(s["shift_type"] == "MORNING" for s in week if "Asha" in s["staff"])
    # The manager can set it too; staff cannot choose for this week (changes start on Monday).
    uid = row["id"]
    assert manager.put(f"/api/roster/week-choices/{uid}", json={"week": str(monday), "shift_type_id": types["NIGHT"]}).status_code == 200
    this_monday = monday - timedelta(days=7)
    assert manager.put(f"/api/roster/week-choices/{uid}", json={"week": str(this_monday),
                                                                "shift_type_id": types["NIGHT"]}).status_code == 400


def test_next_shift_sees_previous_online_money_as_typed(app, manager):
    shift_id = manager.get("/api/shifts").json["current_shift_id"]
    manager.post(f"/api/shifts/{shift_id}/start")
    manager.put(f"/api/shifts/{shift_id}/online", json={"online_expression": "4000+60000+5000"})
    current = manager.get(f"/api/shifts/{shift_id}").json["shift"]
    d = date.fromisoformat(current["shift_date"])
    nxt = (d, "NIGHT") if current["shift_type"] == "MORNING" else (d + timedelta(days=1), "MORNING")
    manager.post("/api/shifts/generate", json={"start_date": str(nxt[0]), "end_date": str(nxt[0])})
    next_id = next(s["id"] for s in manager.get(f"/api/shifts?start={nxt[0]}&end={nxt[0]}").json["shifts"]
                   if s["shift_type"] == nxt[1])
    manager.post(f"/api/shifts/{next_id}/start")
    manager.put(f"/api/shifts/{next_id}/online", json={"online_expression": "1000+2000"})
    v = manager.get(f"/api/shifts/{next_id}").json["shift"]["online_view"]
    assert v["previous"]["online_expression"] == "4000+60000+5000" and v["previous"]["online"] == 69_000
    assert (v["own_expression"], v["own"], v["total"]) == ("1000+2000", 3_000, 72_000)


def test_director_adds_removes_and_restores_a_station(app, manager):
    director = login(app, "director", "Director123!")
    main = director.get("/api/director/stations").json["stations"][0]
    assert director.post(f"/api/director/stations/{main['id']}/remove", json={}).status_code == 400  # last station

    second = director.post("/api/director/stations", json={"name": "Kigala Mbezi"}).json["station"]
    director.post("/api/director/managers", json={"full_name": "Neema", "username": "neema", "password": "Temp1234",
                                                  "station_ids": [main["id"], second["id"]]})
    neema = login(app, "neema", "Temp1234")
    neema.post("/api/auth/station", json={"station_id": second["id"]})
    neema.post("/api/users", json={"full_name": "Juma", "username": "juma01", "password": "Temp1234",
                                   "station_id": second["id"]})
    juma = login(app, "juma01", "Temp1234")

    r = director.post(f"/api/director/stations/{second['id']}/remove", json={"reason": "Lease ended"})
    assert r.status_code == 200 and r.json["station"]["is_active"] is False
    # Its staff are logged out and cannot log in; the manager is moved to their other station.
    assert juma.get("/api/auth/me").status_code == 401
    blocked = app.test_client().post("/api/auth/login", json={"username": "juma01", "password": "Temp1234"})
    assert blocked.status_code == 403 and "removed" in blocked.json["error"]
    me = neema.get("/api/auth/me").json["user"]
    assert me["station_id"] == main["id"] and [s["id"] for s in me["stations"]] == [main["id"]]
    assert [l["name"] for l in director.get("/api/director/overview").json["locations"]] == [main["name"]]

    # Restore: everything works again, nothing was lost.
    assert director.post(f"/api/director/stations/{second['id']}/restore", json={}).status_code == 200
    login(app, "juma01", "Temp1234")
    assert len(director.get("/api/director/overview").json["locations"]) == 2
