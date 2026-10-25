"""Several people on one shift: the manager sets how many; each person works their own pumps."""
from .conftest import login


def _setup(app, manager, people=2, staff=("asha01", "juma01", "neema01")):
    for t in manager.get("/api/shift-types").json["shift_types"]:
        manager.put(f"/api/shift-types/{t['id']}", json={"staff_needed": people})
    pumps = [p["id"] for p in manager.get("/api/pumps").json["pumps"] if p["is_active"]]
    clients = []
    for u in staff:
        manager.post("/api/users", json={"full_name": u[:-2].title(), "username": u, "password": "Temp1234"})
        clients.append(login(app, u, "Temp1234"))
    return pumps, clients


def test_manager_sets_people_per_shift(app, manager):
    t = manager.get("/api/shift-types").json["shift_types"][0]
    assert t["staff_needed"] == 1                                       # default: one person
    assert manager.put(f"/api/shift-types/{t['id']}", json={"staff_needed": 3}).status_code == 200
    assert manager.get("/api/shift-types").json["shift_types"][0]["staff_needed"] == 3
    assert manager.put(f"/api/shift-types/{t['id']}", json={"staff_needed": 0}).status_code == 400
    assert manager.put(f"/api/shift-types/{t['id']}", json={"staff_needed": "two"}).status_code == 400


def test_each_person_works_their_own_pumps(app, manager):
    pumps, (asha, juma, neema) = _setup(app, manager)
    choice = asha.get("/api/staff/my-shift").json["pump_choice"]
    assert choice["needed"] == 2 and choice["must_choose"] and len(choice["suggested"]) == 2   # 3 pumps, 2 people
    r = asha.post("/api/staff/start-shift")
    assert r.status_code == 400 and "choose the pumps" in r.json["error"]          # must choose with 2 people
    asha_shift = asha.post("/api/staff/start-shift", json={"pump_ids": pumps[:2]}).json["shift"]
    assert sorted(x["pump_id"] for x in asha_shift["readings"]) == sorted(pumps[:2])   # sees only her pumps

    choice = juma.get("/api/staff/my-shift").json["pump_choice"]
    assert choice["suggested"] == pumps[2:] and [p["taken_by"] for p in choice["pumps"]][:2] == ["Asha", "Asha"]
    clash = juma.post("/api/staff/start-shift", json={"pump_ids": [pumps[0]]})
    assert clash.status_code == 409 and "Asha" in clash.json["error"]              # a pump is one person's
    juma_shift = juma.post("/api/staff/start-shift", json={"pump_ids": pumps[2:]}).json["shift"]
    assert [x["pump_id"] for x in juma_shift["readings"]] == pumps[2:]
    sid = juma_shift["id"]
    assert juma.put(f"/api/shifts/{sid}/readings/{pumps[0]}", json={"opening_meter": 1}).status_code == 403

    full = neema.post("/api/staff/start-shift", json={"pump_ids": []})            # shift has its 2 people
    assert full.status_code == 409 and "already has 2 people" in full.json["error"]
    assert neema.get("/api/staff/my-shift").json["current_shift"]["can_start"] is False


def test_changing_my_pumps_and_manager_assignment(app, manager):
    pumps, (asha, juma, _) = _setup(app, manager)
    sid = asha.post("/api/staff/start-shift", json={"pump_ids": pumps[:2]}).json["shift"]["id"]
    juma.post("/api/staff/start-shift", json={"pump_ids": pumps[2:]})
    assert asha.put(f"/api/shifts/{sid}/my-pumps", json={"pump_ids": [pumps[2]]}).status_code == 409
    r = asha.put(f"/api/shifts/{sid}/my-pumps", json={"pump_ids": [pumps[0]]})     # gives one back
    assert r.status_code == 200 and [x["pump_id"] for x in r.json["shift"]["readings"]] == [pumps[0]]
    users = {u["username"]: u["id"] for u in manager.get("/api/users").json["users"]}
    clash = manager.post(f"/api/shifts/{sid}/assign", json={"user_id": users["juma01"], "pump_ids": [pumps[0]]})
    assert clash.status_code == 409                                                  # manager: same rule
    ok = manager.post(f"/api/shifts/{sid}/assign", json={"user_id": users["juma01"], "pump_ids": [pumps[1], pumps[2]]})
    staff = {a["full_name"]: sorted(a["pump_ids"]) for a in ok.json["shift"]["staff"]}
    assert staff == {"Asha": [pumps[0]], "Juma": sorted([pumps[1], pumps[2]])}


def test_working_alone_still_means_all_pumps(app, manager):
    pumps, (asha,) = _setup(app, manager, people=1, staff=("asha01",))
    shift = asha.post("/api/staff/start-shift").json["shift"]                       # no choice needed
    assert sorted(x["pump_id"] for x in shift["readings"]) == sorted(pumps)


def test_pumps_are_shared_when_both_people_are_already_on_the_shift(app, manager):
    pumps, (asha, juma) = _setup(app, manager, staff=("asha01", "juma01"))
    sid = asha.get("/api/staff/my-shift").json["current_shift"]["id"]
    users = {u["username"]: u["id"] for u in manager.get("/api/users").json["users"]}
    for name in ("asha01", "juma01"):                                               # e.g. put on by the roster
        assert manager.post(f"/api/shifts/{sid}/assign", json={"user_id": users[name]}).status_code == 200
    choice = asha.get("/api/staff/my-shift").json["pump_choice"]
    assert choice["must_choose"] and len(choice["suggested"]) == 2                 # not all 3: Juma needs one
