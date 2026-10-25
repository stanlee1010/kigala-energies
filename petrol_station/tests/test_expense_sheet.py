"""Shift expense sheet ("Matumizi"): own words + amount per line, saved together, total adds up."""
from decimal import Decimal

from .conftest import login


def _open_shift(app, manager):
    manager.post("/api/users", json={"full_name": "Asha", "username": "asha01", "password": "Temp1234"})
    asha = login(app, "asha01", "Temp1234")
    return asha, asha.post("/api/staff/start-shift").json["shift"]["id"]


def test_sheet_adds_changes_and_removes_lines(app, manager):
    asha, sid = _open_shift(app, manager)
    r = asha.put(f"/api/shifts/{sid}/expenses", json={"lines": [
        {"name": "Umeme", "amount": "260000"}, {"name": "chai  wateja", "amount": 177500},
        {"name": "Fundi mlango", "amount": "20000"}]})
    assert r.status_code == 200 and r.json["added"] == 3
    money = asha.get(f"/api/shifts/{sid}").json["shift"]["money"]
    assert Decimal(money["expenses"]) == Decimal("457500")
    items = {e["category"]: e for e in money["expense_items"]}
    assert set(items) == {"Umeme", "Chai wateja", "Fundi mlango"} and items["Umeme"]["status"] == "PENDING"
    assert "Chai wateja" in {c["name"] for c in asha.get("/api/expense-categories").json["categories"]}

    # change one amount, drop "Fundi mlango", add a new line
    r = asha.put(f"/api/shifts/{sid}/expenses", json={"lines": [
        {"id": items["Umeme"]["id"], "name": "Umeme", "amount": "250000"},
        {"id": items["Chai wateja"]["id"], "name": "Chai wateja", "amount": "177500"},
        {"name": "Visu", "amount": "3000"}]})
    assert (r.json["added"], r.json["changed"], r.json["removed"]) == (1, 1, 1)
    money = asha.get(f"/api/shifts/{sid}").json["shift"]["money"]
    assert Decimal(money["expenses"]) == Decimal("430500")
    removed = manager.get("/api/expenses?status=CANCELLED").json["expenses"]
    assert [e["category"] for e in removed] == ["Fundi mlango"]                # cancelled, never deleted


def test_sheet_leaves_manager_decisions_alone(app, manager):
    asha, sid = _open_shift(app, manager)
    asha.put(f"/api/shifts/{sid}/expenses", json={"lines": [{"name": "Azam", "amount": "59000"}]})
    eid = asha.get(f"/api/shifts/{sid}").json["shift"]["money"]["expense_items"][0]["id"]
    manager.post(f"/api/expenses/{eid}/approve")
    # an approved line is not on the editable sheet; sending an empty sheet must not cancel it
    assert asha.put(f"/api/shifts/{sid}/expenses", json={"lines": []}).json["removed"] == 0
    r = asha.put(f"/api/shifts/{sid}/expenses", json={"lines": [{"id": eid, "name": "Azam", "amount": "1"}]})
    assert r.status_code == 409
    assert Decimal(asha.get(f"/api/shifts/{sid}").json["shift"]["money"]["expenses"]) == Decimal("59000")


def test_sheet_checks_every_line(app, manager):
    asha, sid = _open_shift(app, manager)
    assert asha.put(f"/api/shifts/{sid}/expenses", json={"lines": [{"name": "", "amount": "5"}]}).status_code == 400
    bad = asha.put(f"/api/shifts/{sid}/expenses", json={"lines": [{"name": "Battery", "amount": "5000"},
                                                                     {"name": "Visu", "amount": "-3"}]})
    assert bad.status_code == 400
    # nothing is saved when one line is wrong
    assert asha.get(f"/api/shifts/{sid}").json["shift"]["money"]["expense_items"] == []
    manager.post("/api/users", json={"full_name": "Juma", "username": "juma01", "password": "Temp1234"})
    juma = login(app, "juma01", "Temp1234")
    assert juma.put(f"/api/shifts/{sid}/expenses", json={"lines": []}).status_code == 403


def test_my_records_sheet_without_a_shift_uses_today(app, manager):
    manager.post("/api/users", json={"full_name": "Asha", "username": "asha01", "password": "Temp1234"})
    asha = login(app, "asha01", "Temp1234")
    r = asha.put("/api/staff/expenses", json={"lines": [{"name": "Room expenses", "amount": "73500"},
                                                        {"name": "Battery", "amount": "5000"}]})
    assert r.status_code == 200 and r.json["added"] == 2
    sheet = asha.get("/api/staff/expenses").json
    assert sheet["shift"] is None and {e["category"] for e in sheet["expenses"]} == {"Room expenses", "Battery"}
    assert all(e["shift_id"] is None and e["expense_date"] == sheet["day"] for e in sheet["expenses"])
    battery = next(e for e in sheet["expenses"] if e["category"] == "Battery")
    asha.put("/api/staff/expenses", json={"lines": [{"id": battery["id"], "name": "Battery", "amount": "6000"}]})
    sheet = asha.get("/api/staff/expenses").json
    assert [(e["category"], Decimal(e["amount"])) for e in sheet["expenses"]] == [("Battery", Decimal("6000"))]


def test_my_records_sheet_follows_the_open_shift(app, manager):
    asha, sid = _open_shift(app, manager)
    asha.put("/api/staff/expenses", json={"lines": [{"name": "Visu", "amount": "3000"}]})
    sheet = asha.get("/api/staff/expenses").json
    assert sheet["shift"] and sheet["expenses"][0]["shift_id"] == sid
    # the same line shows on the shift page and comes off that shift's cash
    assert Decimal(asha.get(f"/api/shifts/{sid}").json["shift"]["money"]["expenses"]) == Decimal("3000")


# ---------------------------------------------------------------- offline saves sent again
def test_a_save_sent_twice_is_applied_once_even_after_approval(app, manager):
    asha, sid = _open_shift(app, manager)
    key = {"Idempotency-Key": "9f1c2d3e-4b5a-6978-8a9b-0c1d2e3f4a5b"}
    sheet = {"lines": [{"name": "Umeme", "amount": "260000"}]}
    first = asha.put(f"/api/shifts/{sid}/expenses", json=sheet, headers=key)     # reply "lost"
    assert first.json["added"] == 1
    eid = asha.get(f"/api/shifts/{sid}").json["shift"]["money"]["expense_items"][0]["id"]
    manager.post(f"/api/expenses/{eid}/approve")                                   # meanwhile
    again = asha.put(f"/api/shifts/{sid}/expenses", json=sheet, headers=key)      # phone resends
    assert again.status_code == 200 and again.json["already_saved"] is True
    money = asha.get(f"/api/shifts/{sid}").json["shift"]["money"]
    assert Decimal(money["expenses"]) == Decimal("260000") and len(money["expense_items"]) == 1


def test_a_refused_save_can_be_sent_again_with_the_same_key(app, manager):
    asha, sid = _open_shift(app, manager)
    key = {"Idempotency-Key": "0a1b2c3d4e5f60718293a4b5c6d7e8f9"}
    bad = asha.put(f"/api/shifts/{sid}/expenses", json={"lines": [{"name": "", "amount": 5}]}, headers=key)
    assert bad.status_code == 400                                     # refused: the key is not used up
    ok = asha.put(f"/api/shifts/{sid}/expenses", json={"lines": [{"name": "Visu", "amount": 5}]}, headers=key)
    assert ok.status_code == 200 and ok.json["added"] == 1
    assert asha.put(f"/api/shifts/{sid}/online", json={"online_expression": "1"},
                    headers={"Idempotency-Key": "bad key!"}).status_code == 400


# ---------------------------------------------------------------- My Records: closed shifts stay closed
def _submit(app, sid):
    with app.app_context():
        from backend.extensions import db
        from backend.models import Shift
        db.session.get(Shift, sid).status = "SUBMITTED"
        db.session.commit()


def test_my_records_cannot_change_a_submitted_shifts_expenses(app, manager):
    asha, sid = _open_shift(app, manager)
    asha.put(f"/api/shifts/{sid}/expenses", json={"lines": [{"name": "Umeme", "amount": "1000"}]})
    _submit(app, sid)
    sheet = asha.get("/api/staff/expenses").json
    assert sheet["shift_id"] is None and [e["locked"] for e in sheet["expenses"]] == [True]
    r = asha.put("/api/staff/expenses", json={"lines": [], "day": sheet["day"], "shift_id": None})
    assert r.json["removed"] == 0                                     # the shift's expense is untouched
    assert len(asha.get(f"/api/shifts/{sid}").json["shift"]["money"]["expense_items"]) == 1


def test_a_sheet_saved_offline_lands_on_its_own_shift_and_day(app, manager):
    asha, sid = _open_shift(app, manager)
    made_during_shift = {"lines": [{"name": "Visu", "amount": "3000"}],
                         "day": asha.get("/api/staff/expenses").json["day"], "shift_id": sid}
    _submit(app, sid)                                                 # shift submitted before it arrives
    r = asha.put("/api/staff/expenses", json=made_during_shift)
    assert r.status_code == 409 and "submitted" in r.json["error"]   # refused, not put on another shift
    from datetime import date, timedelta
    yesterday = (date.fromisoformat(made_during_shift["day"]) - timedelta(days=1)).isoformat()
    r = asha.put("/api/staff/expenses", json={"lines": [{"name": "Chai", "amount": "500"}],
                                              "day": yesterday, "shift_id": None})
    assert r.status_code == 200
    with app.app_context():
        from backend.models import Expense
        assert Expense.query.filter_by(description="Chai").one().expense_date.isoformat() == yesterday
    old = (date.fromisoformat(made_during_shift["day"]) - timedelta(days=5)).isoformat()
    assert asha.put("/api/staff/expenses", json={"lines": [], "day": old, "shift_id": None}).status_code == 409
