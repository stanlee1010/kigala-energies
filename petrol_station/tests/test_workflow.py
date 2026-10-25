"""End-to-end: the complete station workflow from section 43 of the specification."""
from datetime import timedelta

from backend.extensions import db
from backend.models import Alert, AuditLog, RefillConfirmation, ReportingPeriod
from backend.calculations.periods import period_bounds, period_label
from backend.services.clock import now_local, today_local
from backend.services.periods import ensure_periods

from .conftest import login


def open_alerts(app, alert_type):
    with app.app_context():
        return Alert.query.filter_by(alert_type=alert_type, is_resolved=False).all()


def setup_station(app, manager):
    r = manager.post("/api/users", json={"full_name": "John Mushi", "username": "john01", "password": "Temp1234", "role": "STAFF"})
    assert r.status_code == 201, r.json
    john_id = r.json["user"]["id"]
    fuels = {f["code"]: f["id"] for f in manager.get("/api/fuel-types").json["fuel_types"]}
    assert manager.post("/api/prices", json={"fuel_type_id": fuels["DIESEL"], "price_per_litre": 2900}).status_code == 201
    assert manager.post("/api/prices", json={"fuel_type_id": fuels["PETROL"], "price_per_litre": 3000}).status_code == 201
    for code, cost in (("DIESEL", 2500), ("PETROL", 2600)):
        r = manager.post("/api/stock/adjustments", json={"fuel_type_id": fuels[code], "adjustment_type": "OPENING_BALANCE",
                                                         "quantity_litres": 4000, "cost_per_litre": cost, "reason": "Go-live dip"})
        assert r.status_code == 201, r.json
    return john_id, fuels


def test_full_shift_refill_and_profit_workflow(app, manager):
    john_id, fuels = setup_station(app, manager)

    # Manager adds a pump (not hard-coded) and assigns John to the current shift.
    assert manager.post("/api/pumps", json={"fuel_type_id": fuels["PETROL"], "pump_number": 2}).status_code == 201
    shift_id = manager.get("/api/shifts").json["current_shift_id"]
    assert manager.post(f"/api/shifts/{shift_id}/assign", json={"user_id": john_id}).status_code == 200

    john = login(app, "john01", "Temp1234")
    assert john.first_login_user["must_change_password"] is True
    # Staff cannot reach manager functions.
    assert john.get("/api/users").status_code == 403
    assert john.post("/api/prices", json={"fuel_type_id": fuels["DIESEL"], "price_per_litre": 1}).status_code == 403

    my = john.get("/api/staff/my-shift").json
    assert my["shift"]["id"] == shift_id
    shift = john.post(f"/api/shifts/{shift_id}/start").json["shift"]
    assert shift["status"] == "OPEN"
    diesel_pump = next(r for r in shift["readings"] if r["fuel"] == "Diesel")
    other_pumps = [r for r in shift["readings"] if r["pump_id"] != diesel_pump["pump_id"]]

    # Staff never see or type tank stock: the opening is carried over automatically.
    assert john.put(f"/api/shifts/{shift_id}/stock/{fuels['DIESEL']}", json={"opening_stock": 4000}).status_code == 403
    assert john.get("/api/stock/current").status_code == 403
    assert shift["stock"] == [] and shift["tanks"] == []
    assert all("litres" not in t for t in john.get("/api/tanks").json["tanks"])

    for p in shift["readings"]:
        assert john.put(f"/api/shifts/{shift_id}/readings/{p['pump_id']}", json={"opening_meter": 1000}).status_code == 200

    # Closing lower than opening is rejected with the exact warning and a manager alert.
    r = john.put(f"/api/shifts/{shift_id}/readings/{diesel_pump['pump_id']}", json={"closing_meter": 400})
    assert r.status_code == 400 and r.json["title"] == "INVALID METER READING"
    assert r.json["error"] == "Closing meter reading cannot be lower than opening meter reading."
    assert open_alerts(app, "METER_ERROR")

    r = john.put(f"/api/shifts/{shift_id}/readings/{diesel_pump['pump_id']}", json={"closing_meter": 1500})
    reading = next(x for x in r.json["shift"]["readings"] if x["pump_id"] == diesel_pump["pump_id"])
    assert reading["fuel_sold"] == 500 and reading["revenue"] == 1_450_000
    assert not open_alerts(app, "METER_ERROR")  # cleared by the valid reading

    # ---- Refill: enter -> NO (correct) -> YES (locked)
    with app.app_context():
        now = now_local(1)
    refill = {"fuel_type_id": fuels["DIESEL"], "refill_date": now.date().isoformat(), "refill_time": now.strftime("%H:%M"),
              "quantity_litres": 5000, "cost_per_litre": 2800, "supplier_name": "Puma Energy", "invoice_number": "INV-001"}
    r = john.post("/api/refills", json=refill)
    assert r.status_code == 201 and r.json["refill"]["status"] == "AWAITING_CONFIRMATION"
    assert r.json["confirm"]["total_cost"] == 14_000_000
    rid = r.json["refill"]["id"]
    assert john.post(f"/api/refills/{rid}/confirm", json={"answer": "NO"}).json["refill"]["status"] == "DRAFT"
    assert john.put(f"/api/refills/{rid}", json={**refill, "quantity_litres": 6000}).status_code == 200
    r = john.post(f"/api/refills/{rid}/confirm", json={"answer": "YES"})
    assert r.json["refill"]["status"] == "CONFIRMED"
    assert "can no longer be changed by you" in r.json["message"]
    assert john.put(f"/api/refills/{rid}", json=refill).status_code == 409          # locked
    assert john.post(f"/api/refills/{rid}/confirm", json={"answer": "YES"}).status_code == 409
    with app.app_context():
        results = [c.confirmation_result for c in RefillConfirmation.query.filter_by(refill_id=rid).order_by(RefillConfirmation.id)]
        assert results == ["RETURNED_FOR_CORRECTION", "CONFIRMED_CORRECT"]
        assert AuditLog.query.filter_by(action="STAFF CONFIRMED REFILL", record_id=rid).count() == 1

    # Stock updated automatically: 4000 + 6000 - 500 = 9500 (manager view), per tank and per pump.
    shift = manager.get(f"/api/shifts/{shift_id}").json["shift"]
    diesel_stock = next(s for s in shift["stock"] if s["fuel_type_id"] == fuels["DIESEL"])
    assert diesel_stock["refilled_quantity"] == 6000 and diesel_stock["calculated_closing_stock"] == 9500
    tank = next(t for t in shift["tanks"] if t["fuel"] == "Diesel")
    assert (tank["opening"], tank["refilled"], tank["sold"], tank["closing"]) == (4000, 6000, 500, 9500)
    assert {p["pump"]: p["sold"] for p in tank["pumps"]} == {diesel_pump["pump_name"]: 500}
    assert not open_alerts(app, "REFILL_NOT_RECONCILED")

    # Submit is blocked until every pump has a closing reading.
    assert john.post(f"/api/shifts/{shift_id}/submit").status_code == 400
    for p in other_pumps:
        john.put(f"/api/shifts/{shift_id}/readings/{p['pump_id']}", json={"closing_meter": 1000})
    assert john.post(f"/api/shifts/{shift_id}/submit").json["shift"]["status"] == "SUBMITTED"
    assert john.put(f"/api/shifts/{shift_id}/readings/{diesel_pump['pump_id']}", json={"closing_meter": 1600}).status_code == 409

    # Manager approves, records a short deposit -> DEPOSIT_DIFFERENCE.
    assert manager.post(f"/api/refills/{rid}/approve").json["refill"]["status"] == "APPROVED"
    assert manager.post(f"/api/shifts/{shift_id}/approve").json["shift"]["status"] == "CLOSED"
    r = manager.post(f"/api/shifts/{shift_id}/deposit", json={"actual_amount": 1_400_000, "deposit_reference": "DEP-1"})
    assert r.json["warning"]["difference"] == -50_000
    assert open_alerts(app, "DEPOSIT_DIFFERENCE")

    # Profit uses weighted-average cost, not the whole refill:
    # avg = (4000*2500 + 6000*2800)/10000 = 2680 ; COGS = 500*2680 = 1,340,000
    with app.app_context():
        today = today_local(1).isoformat()
    report = manager.get(f"/api/reports/profit_loss?start={shift['shift_date']}&end={today}").json["report"]
    summary = {label: value for label, value, _ in report["summary"]}
    assert summary["Revenue"] == 1_450_000
    assert summary["Cost of fuel sold"] == 1_340_000
    assert summary["Gross profit"] == 110_000

    # A refill entered into an already closed shift is flagged as not reconciled.
    r = john.post("/api/refills", json={**refill, "quantity_litres": 1000, "invoice_number": "INV-002"})
    john.post(f"/api/refills/{r.json['refill']['id']}/confirm", json={"answer": "YES"})
    assert open_alerts(app, "REFILL_NOT_RECONCILED")
    manager.post(f"/api/stock/{diesel_stock['id']}/reconcile", json={"reason": "Late delivery entry"})
    assert not open_alerts(app, "REFILL_NOT_RECONCILED")

    # Exports work.
    for fmt in ("pdf", "xlsx"):
        r = manager.get(f"/api/reports/daily?start={shift['shift_date']}&format={fmt}")
        assert r.status_code == 200 and len(r.data) > 1000


def test_opening_stock_mismatch_warns_manager(app, manager):
    john_id, fuels = setup_station(app, manager)
    shift_id = manager.get("/api/shifts").json["current_shift_id"]
    manager.post(f"/api/shifts/{shift_id}/start")
    manager.put(f"/api/shifts/{shift_id}/stock/{fuels['DIESEL']}", json={"opening_stock": 4000, "actual_closing_stock": 5000})
    # Close the shift, then open the next one with a different opening quantity.
    for p in manager.get(f"/api/shifts/{shift_id}").json["shift"]["readings"]:
        manager.put(f"/api/shifts/{shift_id}/readings/{p['pump_id']}", json={"opening_meter": 0, "closing_meter": 0})
    manager.post(f"/api/shifts/{shift_id}/approve")

    shift = manager.get(f"/api/shifts/{shift_id}").json["shift"]
    from datetime import date
    d = date.fromisoformat(shift["shift_date"])
    nxt = (d, "NIGHT") if shift["shift_type"] == "MORNING" else (d + timedelta(days=1), "MORNING")
    manager.post("/api/shifts/generate", json={"start_date": str(nxt[0]), "end_date": str(nxt[0])})
    listing = manager.get(f"/api/shifts?start={nxt[0]}&end={nxt[0]}").json["shifts"]
    next_id = next(s["id"] for s in listing if s["shift_type"] == nxt[1])
    manager.post(f"/api/shifts/{next_id}/start")
    r = manager.put(f"/api/shifts/{next_id}/stock/{fuels['DIESEL']}", json={"opening_stock": 4800})
    w = r.json["warning"]
    assert w["title"] == "FUEL STOCK MISMATCH"
    assert w["details"]["previous_closing"] == 5000 and w["details"]["difference"] == -200
    assert open_alerts(app, "STOCK_MISMATCH")


def test_first_wednesday_rollover_and_price_review(app, manager):
    setup_station(app, manager)
    with app.app_context():
        today = today_local(1)
        prev_start, prev_end = period_bounds(period_bounds(today)[0] - timedelta(days=1))
        db.session.add(ReportingPeriod(station_id=1, start_date=prev_start, end_date=prev_end,
                                       label=period_label(prev_start), status="OPEN"))
        db.session.commit()
        ensure_periods(1)
        db.session.commit()

    status = manager.get("/api/periods/status").json
    assert status["previous_period"]["status"] == "CLOSED"
    assert status["monthly_review_required"] and status["price_review_required"]
    assert open_alerts(app, "MONTHLY_REVIEW_REQUIRED") and open_alerts(app, "FUEL_PRICE_UPDATE_REQUIRED")

    decisions = [{"fuel_type_id": p["fuel_type_id"], "action": "KEEP"} for p in status["current_prices"]]
    decisions[0] = {"fuel_type_id": decisions[0]["fuel_type_id"], "action": "UPDATE", "price_per_litre": 3100}
    assert manager.post("/api/prices/review", json={"decisions": decisions}).status_code == 200
    assert not open_alerts(app, "FUEL_PRICE_UPDATE_REQUIRED")
    history = manager.get("/api/prices").json["history"]
    assert {"KEPT", "UPDATED"} <= {h["confirmation_status"] for h in history}

    manager.post(f"/api/periods/{status['previous_period']['id']}/review")
    assert not open_alerts(app, "MONTHLY_REVIEW_REQUIRED")


def test_shift_generation_never_duplicates(app, manager):
    r = manager.post("/api/shifts/generate", json={"start_date": "2026-09-01", "end_date": "2026-09-10"})
    assert r.json["total"] == 20
    first = r.json["created"]
    r = manager.post("/api/shifts/generate", json={"start_date": "2026-09-01", "end_date": "2026-09-10"})
    assert r.json["created"] == 0 and first == 20
    assert r.json["shifts"][0]["label"] == "01 Sep 2026 Morning"
    assert r.json["shifts"][1]["label"] == "01 Sep 2026 Night"
