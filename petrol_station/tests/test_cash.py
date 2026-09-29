"""Online money, total cash and balance — per shift and per day."""
from decimal import Decimal

import pytest

from backend.services.cash import parse_amount_expression

from .conftest import login


def test_plus_sign_adds_up_online_money():
    assert parse_amount_expression("4000+60000+5000") == (Decimal("69000.00"), "4000+60000+5000")
    assert parse_amount_expression(" 4,000 + 60,000 ")[0] == Decimal("64000.00")
    assert parse_amount_expression("")[0] == 0
    for bad in ("4000-1000", "4000++5", "abc", "+5"):
        with pytest.raises(ValueError):
            parse_amount_expression(bad)


def test_shift_and_day_money(app, manager):
    fuels = {f["code"]: f["id"] for f in manager.get("/api/fuel-types").json["fuel_types"]}
    manager.post("/api/prices", json={"fuel_type_id": fuels["PETROL"], "price_per_litre": 3000})
    petrol_pump = next(p["id"] for p in manager.get("/api/pumps").json["pumps"] if p["fuel_code"] == "PETROL")
    uid = manager.post("/api/users", json={"full_name": "Asha", "username": "asha01", "password": "Temp1234"}).json["user"]["id"]
    cats = {c["name"]: c["id"] for c in manager.get("/api/expense-categories").json["categories"]}

    # The night shift that ENDS this morning belongs with today's day shift.
    with app.app_context():
        from datetime import date, timedelta
        from backend.services.clock import today_local
        today = today_local(1)
    day, yesterday = today.isoformat(), (today - timedelta(days=1)).isoformat()
    generated = manager.post("/api/shifts/generate", json={"start_date": yesterday, "end_date": day}).json["shifts"]
    listed = manager.get(f"/api/shifts?start={yesterday}&end={day}").json["shifts"]
    ids = {s["id"] for s in generated}
    shifts = [s for s in listed if s["id"] in ids]
    morning = next(s for s in shifts if s["shift_date"] == day and s["shift_type"] == "MORNING")
    night = next(s for s in shifts if s["shift_date"] == yesterday and s["shift_type"] == "NIGHT")
    tonight = next(s for s in shifts if s["shift_date"] == day and s["shift_type"] == "NIGHT")
    manager.post(f"/api/shifts/{morning['id']}/assign", json={"user_id": uid})
    for s in (morning, night, tonight):
        manager.post(f"/api/shifts/{s['id']}/start")

    asha = login(app, "asha01", "Temp1234")
    # Day shift: sells 400 L x 3000 = 1,200,000; online 4000+60000+5000 = 69,000; expense 20,000.
    asha.put(f"/api/shifts/{morning['id']}/readings/{petrol_pump}", json={"opening_meter": 100, "closing_meter": 500})
    r = asha.put(f"/api/shifts/{morning['id']}/online", json={"online_expression": "4000+60000+5000"})
    assert r.json["shift"]["money"]["online"] == 69000
    assert asha.put(f"/api/shifts/{morning['id']}/online", json={"online_expression": "4000-1"}).status_code == 400
    r = asha.post("/api/expenses", json={"shift_id": morning["id"], "category_id": cats["Cleaning"],
                                         "description": "Soap", "amount": 20000})
    assert r.status_code == 201 and r.json["expense"]["expense_date"] == day
    m = asha.get(f"/api/shifts/{morning['id']}").json["shift"]["money"]
    assert (m["total"], m["online"], m["total_cash"], m["expenses"], m["balance"]) == (
        1_200_000, 69_000, 1_131_000, 20_000, 1_111_000)

    # Last night's shift (ended this morning): sells 100 L = 300,000; online 30,000.
    manager.put(f"/api/shifts/{night['id']}/readings/{petrol_pump}", json={"opening_meter": 50, "closing_meter": 150})
    manager.put(f"/api/shifts/{night['id']}/online", json={"online_expression": "10000+20000"})

    # STAFF: only the online money — last night's online (fixed) + this shift's own. No day totals.
    staff_view = asha.get(f"/api/shifts/{morning['id']}").json["shift"]
    v = staff_view["online_view"]
    assert (v["previous"]["name"], v["previous"]["online"], v["own"], v["total"]) == ("Night", 30_000, 69_000, 99_000)
    assert staff_view["money"]["total_cash"] == 1_131_000          # the night's online is NOT taken off
    assert asha.get(f"/api/money/day?date={day}").status_code == 403   # staff never see the day's totals

    # MANAGER: the Morning shift opens the day; the night of the same date follows.
    manager.put(f"/api/shifts/{tonight['id']}/readings/{petrol_pump}", json={"opening_meter": 500, "closing_meter": 550})
    manager.put(f"/api/shifts/{tonight['id']}/online", json={"online_expression": "15000"})
    d = manager.get(f"/api/money/day?date={day}").json
    by_name = {s["name"]: s for s in d["shifts"]}
    assert [s["name"] for s in d["shifts"]] == ["Morning", "Night"]
    assert by_name["Morning"]["total_cash"] == 1_200_000 - 69_000
    assert by_name["Night"]["total_cash"] == 150_000 - 15_000
    assert d["total"] == 1_350_000                              # total money obtained by all shifts
    assert d["online_total"] == 84_000
    assert d["total_cash"] == 1_131_000 + 135_000
    assert d["expenses"] == 20_000
    assert d["balance"] == 1_266_000 - 20_000

    # The manager's deposit check compares with the cash to hand in, not the full sales.
    r = manager.post(f"/api/shifts/{morning['id']}/deposit", json={"actual_amount": 1_111_000})
    assert r.json["deposit"]["expected_amount"] == 1_111_000 and r.json["warning"] is None

    report = manager.get(f"/api/reports/daily_money?start={day}&end={day}").json["report"]
    row = report["tables"][0]["rows"][0]
    assert row["online_Morning"] == 69_000 and row["online_Night"] == 15_000
    assert row["cash_Morning"] == 1_131_000 and row["cash_Night"] == 135_000 and row["balance"] == 1_246_000
