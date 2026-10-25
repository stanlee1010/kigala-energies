"""Salaries: the manager sets each salary and records money taken; staff see their own figures."""
from datetime import timedelta

from backend.services.clock import today_local

from .conftest import login


def _staff(app, manager, username="asha01", name="Asha"):
    uid = manager.post("/api/users", json={"full_name": name, "username": username, "password": "Temp1234"}).json["user"]["id"]
    client = login(app, username, "Temp1234")
    client.post("/api/auth/change-password", json={"current_password": "Temp1234", "new_password": "Asha12345x"})
    return uid, client


def _month_of(app, days_ago=0):
    with app.app_context():
        return (today_local(1) - timedelta(days=days_ago)).strftime("%Y-%m")


def test_money_taken_is_deducted_from_the_salary(app, manager):
    uid, asha = _staff(app, manager)
    month = _month_of(app)
    assert manager.put(f"/api/salaries/{uid}", json={"month": month, "salary": "300000"}).status_code == 200
    r = manager.post(f"/api/salaries/{uid}/advances", json={"amount": "50000", "note": "School fees"})
    assert r.status_code == 201
    r = manager.post(f"/api/salaries/{uid}/advances", json={"amount": "20000.50"})
    row = r.json["row"]
    assert (row["salary"], row["taken"], row["remaining"]) == (300000, 70000.5, 229999.5)

    table = manager.get(f"/api/salaries?month={month}").json
    mine = next(x for x in table["rows"] if x["user_id"] == uid)
    assert mine["remaining"] == 229999.5 and table["totals"]["taken"] == 70000.5

    seen = asha.get(f"/api/staff/my-salary?month={month}").json["row"]           # the staff member sees it
    assert seen["remaining"] == 229999.5 and [a["note"] for a in seen["advances"]] == ["School fees", None]


def test_a_wrong_entry_is_cancelled_with_a_reason_never_deleted(app, manager):
    uid, asha = _staff(app, manager)
    manager.put(f"/api/salaries/{uid}", json={"salary": "100000"})
    entry = manager.post(f"/api/salaries/{uid}/advances", json={"amount": "40000"}).json["row"]["advances"][0]
    assert manager.post(f"/api/salary-advances/{entry['id']}/void", json={}).status_code == 400    # reason needed
    row = manager.post(f"/api/salary-advances/{entry['id']}/void", json={"reason": "Typed twice"}).json["row"]
    assert row["taken"] == 0 and row["remaining"] == 100000
    assert row["advances"][0]["status"] == "VOID" and row["advances"][0]["void_reason"] == "Typed twice"
    assert asha.get("/api/staff/my-salary").json["row"]["advances"][0]["status"] == "VOID"
    assert manager.post(f"/api/salary-advances/{entry['id']}/void", json={"reason": "x"}).status_code == 409


def test_salary_carries_to_the_next_month_until_changed(app, manager):
    uid, _ = _staff(app, manager)
    manager.put(f"/api/salaries/{uid}", json={"month": "2026-01", "salary": "250000"})
    later = manager.get("/api/salaries?month=2026-03").json["rows"][0]
    assert later["salary"] == 250000 and later["salary_carried"] is True
    manager.put(f"/api/salaries/{uid}", json={"month": "2026-03", "salary": "280000"})
    assert manager.get("/api/salaries?month=2026-02").json["rows"][0]["salary"] == 250000   # earlier month kept
    assert manager.get("/api/salaries?month=2026-04").json["rows"][0]["salary"] == 280000


def test_staff_only_see_their_own_salary_and_cannot_change_anything(app, manager):
    uid, asha = _staff(app, manager)
    juma_id, juma = _staff(app, manager, "juma01", "Juma")
    manager.put(f"/api/salaries/{uid}", json={"salary": "300000"})
    assert juma.get("/api/staff/my-salary").json["row"]["salary"] is None              # not Asha's
    assert asha.get("/api/salaries").status_code == 403
    assert asha.put(f"/api/salaries/{uid}", json={"salary": "999999"}).status_code == 403
    assert asha.post(f"/api/salaries/{uid}/advances", json={"amount": "1"}).status_code == 403


def test_bad_amounts_dates_and_people_are_refused(app, manager):
    uid, _ = _staff(app, manager)
    with app.app_context():
        tomorrow = (today_local(1) + timedelta(days=1)).isoformat()
    for bad in ({"amount": "0"}, {"amount": "-5"}, {"amount": "abc"}, {"amount": "5", "taken_on": tomorrow}):
        assert manager.post(f"/api/salaries/{uid}/advances", json=bad).status_code == 400, bad
    assert manager.put(f"/api/salaries/{uid}", json={"salary": "-1"}).status_code == 400
    assert manager.put(f"/api/salaries/{uid}", json={"salary": "100", "month": "2026-13"}).status_code == 400
    me = manager.get("/api/auth/me").json["user"]["id"]
    assert manager.put(f"/api/salaries/{me}", json={"salary": "100"}).status_code == 400          # not a staff member
    assert manager.put("/api/salaries/9999", json={"salary": "100"}).status_code == 404


def test_money_taken_counts_in_the_month_it_was_taken(app, manager):
    uid, _ = _staff(app, manager)
    manager.put(f"/api/salaries/{uid}", json={"month": "2026-01", "salary": "200000"})
    manager.post(f"/api/salaries/{uid}/advances", json={"amount": "30000", "taken_on": "2026-02-10"})
    jan = manager.get("/api/salaries?month=2026-01").json["rows"][0]
    feb = manager.get("/api/salaries?month=2026-02").json["rows"][0]
    assert jan["taken"] == 0 and feb["taken"] == 30000 and feb["remaining"] == 170000
    actions = [e["action"] for e in manager.get("/api/audit").json["logs"]]
    assert "MONEY TAKEN FROM SALARY" in actions and "SALARY SET" in actions


def _row(manager, month):
    return manager.get(f"/api/salaries?month={month}").json["rows"][0]


def test_a_negative_balance_is_deducted_from_the_next_month(app, manager):
    uid, asha = _staff(app, manager)
    manager.put(f"/api/salaries/{uid}", json={"month": "2026-01", "salary": "100000"})
    manager.post(f"/api/salaries/{uid}/advances", json={"amount": "130000", "taken_on": "2026-01-20"})
    jan, feb, mar = _row(manager, "2026-01"), _row(manager, "2026-02"), _row(manager, "2026-03")
    assert jan["remaining"] == -30000
    assert (feb["salary"], feb["owed_before"], feb["remaining"]) == (100000, 30000, 70000)   # same salary, reduced
    assert (mar["owed_before"], mar["remaining"]) == (0, 100000)                             # paid back: normal again
    assert asha.get("/api/staff/my-salary?month=2026-02").json["row"]["owed_before"] == 30000


def test_a_positive_balance_is_not_carried(app, manager):
    uid, _ = _staff(app, manager)
    manager.put(f"/api/salaries/{uid}", json={"month": "2026-01", "salary": "100000"})
    manager.post(f"/api/salaries/{uid}/advances", json={"amount": "40000", "taken_on": "2026-01-05"})
    feb = _row(manager, "2026-02")
    assert (feb["owed_before"], feb["remaining"]) == (0, 100000)


def test_a_big_shortfall_keeps_reducing_until_paid_and_follows_salary_changes(app, manager):
    uid, _ = _staff(app, manager)
    manager.put(f"/api/salaries/{uid}", json={"month": "2026-01", "salary": "100000"})
    manager.post(f"/api/salaries/{uid}/advances", json={"amount": "250000", "taken_on": "2026-01-10"})
    assert _row(manager, "2026-02")["remaining"] == -50000      # owes 150,000: February is not enough
    manager.put(f"/api/salaries/{uid}", json={"month": "2026-03", "salary": "120000"})   # the manager raised it
    mar = _row(manager, "2026-03")
    assert (mar["salary"], mar["owed_before"], mar["remaining"]) == (120000, 50000, 70000)
    assert _row(manager, "2026-04")["owed_before"] == 0


def test_cancelling_the_entry_removes_the_carried_shortfall(app, manager):
    uid, _ = _staff(app, manager)
    manager.put(f"/api/salaries/{uid}", json={"month": "2026-01", "salary": "100000"})
    entry = manager.post(f"/api/salaries/{uid}/advances", json={"amount": "130000", "taken_on": "2026-01-20"}).json
    assert _row(manager, "2026-02")["owed_before"] == 30000
    manager.post(f"/api/salary-advances/{entry['row']['advances'][0]['id']}/void", json={"reason": "Wrong person"})
    assert _row(manager, "2026-02")["owed_before"] == 0 and _row(manager, "2026-02")["remaining"] == 100000


def test_money_taken_before_any_salary_is_owed_once_a_salary_is_set(app, manager):
    uid, _ = _staff(app, manager)
    manager.post(f"/api/salaries/{uid}/advances", json={"amount": "20000", "taken_on": "2026-01-15"})
    manager.put(f"/api/salaries/{uid}", json={"month": "2026-02", "salary": "100000"})
    feb = _row(manager, "2026-02")
    assert (feb["owed_before"], feb["remaining"]) == (20000, 80000)
