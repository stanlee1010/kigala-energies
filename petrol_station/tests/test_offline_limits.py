"""Saves made without internet: the time they were typed, not overwriting newer changes."""
from datetime import timedelta
from decimal import Decimal

from backend.models import utcnow

from .conftest import login


def _staff_on_shift(app, manager, username="asha01", pump_ids=None):
    for fuel in manager.get("/api/fuel-types").json["fuel_types"]:
        manager.post("/api/prices", json={"fuel_type_id": fuel["id"], "price_per_litre": 3000})
    manager.post("/api/users", json={"full_name": username.title(), "username": username, "password": "Temp1234"})
    client = login(app, username, "Temp1234")
    shift = client.post("/api/staff/start-shift", json={"pump_ids": pump_ids or []}).json["shift"]
    return client, shift["id"], shift["readings"][0]["pump_id"]


def _iso(dt):
    return dt.isoformat(timespec="seconds") + "Z"


def test_time_server_gives_utc(app, manager):
    t = manager.get("/api/time").json
    assert "utc" in t and abs((utcnow() - __import__("datetime").datetime.fromisoformat(t["utc"])).total_seconds()) < 5


def test_offline_reading_keeps_the_time_it_was_typed(app, manager):
    asha, sid, pump = _staff_on_shift(app, manager)
    typed = utcnow() - timedelta(minutes=40)
    with app.app_context():                     # the shift started before the reading was typed
        from backend.extensions import db
        from backend.models import Shift
        db.session.get(Shift, sid).started_at = utcnow() - timedelta(hours=2)
        db.session.commit()
    r = asha.put(f"/api/shifts/{sid}/readings/{pump}", json={"opening_meter": 1000},
                 headers={"X-Saved-At": _iso(typed)})
    reading = next(x for x in r.json["shift"]["readings"] if x["pump_id"] == pump)
    with app.app_context():
        from backend.models import AuditLog, PumpReading
        stored = PumpReading.query.filter_by(shift_id=sid, pump_id=pump).one().opening_recorded_at
        assert abs((stored - typed).total_seconds()) < 2                      # typed time, not arrival
        note = AuditLog.query.filter_by(action="STAFF RECORDED READING").one().reason
        assert note.startswith("Saved on the phone at") and "received" in note
    assert reading["opening_meter"] == 1000


def test_typed_time_is_checked(app, manager):
    asha, sid, pump = _staff_on_shift(app, manager)
    future = asha.put(f"/api/shifts/{sid}/readings/{pump}", json={"opening_meter": 1},
                      headers={"X-Saved-At": _iso(utcnow() + timedelta(hours=5))})
    with app.app_context():
        from backend.models import PumpReading
        stored = PumpReading.query.filter_by(shift_id=sid, pump_id=pump).one().opening_recorded_at
        assert stored <= utcnow()                                              # never in the future
    assert future.status_code == 200
    with app.app_context():                      # before the shift started -> the shift start
        from backend.extensions import db
        from backend.models import Shift
        started = db.session.get(Shift, sid).started_at
    asha.put(f"/api/shifts/{sid}/readings/{pump}", json={"opening_meter": 2},
             headers={"X-Saved-At": _iso(started - timedelta(hours=3))})
    with app.app_context():
        from backend.models import PumpReading
        assert PumpReading.query.filter_by(shift_id=sid, pump_id=pump).one().opening_recorded_at >= started
    old = asha.put(f"/api/shifts/{sid}/online", json={"online_expression": "5"},
                   headers={"X-Saved-At": _iso(utcnow() - timedelta(days=4))})
    assert old.status_code == 409 and "3 days" in old.json["error"]
    assert asha.put(f"/api/shifts/{sid}/online", json={"online_expression": "5"},
                    headers={"X-Saved-At": "yesterday"}).status_code == 400


def test_offline_save_does_not_overwrite_a_newer_reading(app, manager):
    asha, sid, pump = _staff_on_shift(app, manager)
    juma = manager                                                             # e.g. the manager corrects it
    seen = {"opening_meter": None, "closing_meter": None}                     # both saw an empty reading
    assert juma.put(f"/api/shifts/{sid}/readings/{pump}",
                    json={"opening_meter": 5000, "based_on": seen}).status_code == 200
    late = asha.put(f"/api/shifts/{sid}/readings/{pump}", json={"opening_meter": 4000, "based_on": seen})
    assert late.status_code == 409 and "Someone changed" in late.json["error"] and "5000" in late.json["error"]
    reading = next(x for x in juma.get(f"/api/shifts/{sid}").json["shift"]["readings"] if x["pump_id"] == pump)
    assert reading["opening_meter"] == 5000                                    # the newer value stays
    ok = asha.put(f"/api/shifts/{sid}/readings/{pump}",
                  json={"closing_meter": 5100, "based_on": {"opening_meter": 5000, "closing_meter": None}})
    assert ok.status_code == 200                                               # based on the latest: fine


def test_offline_online_money_does_not_overwrite_a_newer_figure(app, manager):
    for t in manager.get("/api/shift-types").json["shift_types"]:
        manager.put(f"/api/shift-types/{t['id']}", json={"staff_needed": 2})
    pumps = [p["id"] for p in manager.get("/api/pumps").json["pumps"] if p["is_active"]]
    asha, sid, _ = _staff_on_shift(app, manager, pump_ids=pumps[:1])
    juma, _, _ = _staff_on_shift(app, manager, "juma01", pump_ids=pumps[1:])
    juma.put(f"/api/shifts/{sid}/online", json={"online_expression": "1000+2000", "based_on": {"online_expression": ""}})
    late = asha.put(f"/api/shifts/{sid}/online", json={"online_expression": "500", "based_on": {"online_expression": ""}})
    assert late.status_code == 409 and "1000+2000" in late.json["error"]
    assert Decimal(str(asha.get(f"/api/shifts/{sid}").json["shift"]["money"]["online"])) == Decimal("3000")


def test_offline_sheet_does_not_undo_a_managers_correction(app, manager):
    asha, sid, _ = _staff_on_shift(app, manager)
    asha.put(f"/api/shifts/{sid}/expenses", json={"lines": [{"name": "Umeme", "amount": "260000"}]})
    eid = asha.get(f"/api/shifts/{sid}").json["shift"]["money"]["expense_items"][0]["id"]
    manager.put(f"/api/expenses/{eid}", json={"category_name": "Umeme", "description": "Umeme", "amount": "250000"})
    late = asha.put(f"/api/shifts/{sid}/expenses", json={"lines": [
        {"id": eid, "name": "Umeme", "amount": "270000", "was": {"name": "Umeme", "amount": "260000"}}]})
    assert late.status_code == 409 and "250000" in late.json["error"].replace(",", "")
    items = asha.get(f"/api/shifts/{sid}").json["shift"]["money"]["expense_items"]
    assert [Decimal(str(e["amount"])) for e in items] == [Decimal("250000")]
