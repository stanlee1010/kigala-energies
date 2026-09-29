"""CAPITAL: profit from selling price and buying price (manager only)."""
from backend.services.clock import now_local

from .conftest import login


def test_capital_profit_and_calculator(app, manager):
    fuels = {f["code"]: f["id"] for f in manager.get("/api/fuel-types").json["fuel_types"]}
    manager.post("/api/prices", json={"fuel_type_id": fuels["DIESEL"], "price_per_litre": 2900})
    manager.post("/api/stock/adjustments", json={"fuel_type_id": fuels["DIESEL"], "adjustment_type": "OPENING_BALANCE",
                                                 "quantity_litres": 4000, "cost_per_litre": 2500, "reason": "dip"})
    with app.app_context():
        now = now_local(1)
    r = manager.post("/api/refills", json={"fuel_type_id": fuels["DIESEL"], "quantity_litres": 6000, "cost_per_litre": 2800,
                                           "supplier_name": "Puma", "invoice_number": "A1",
                                           "refill_date": now.date().isoformat(), "refill_time": now.strftime("%H:%M")})
    manager.post(f"/api/refills/{r.json['refill']['id']}/confirm", json={"answer": "YES"})
    shift_id = manager.get("/api/shifts").json["current_shift_id"]
    manager.post(f"/api/shifts/{shift_id}/start")
    pump = next(p for p in manager.get("/api/pumps").json["pumps"] if p["fuel_code"] == "DIESEL")
    manager.put(f"/api/shifts/{shift_id}/readings/{pump['id']}", json={"opening_meter": 1000, "closing_meter": 1500})

    c = manager.get("/api/capital").json
    diesel = next(f for f in c["fuels"] if f["fuel"] == "Diesel")
    # Buying price = weighted average (4000 x 2500 + 6000 x 2800) / 10000 = 2680
    assert diesel["selling_price"] == 2900 and diesel["buying_price"] == 2680
    assert diesel["last_buying_price"] == 2800
    assert diesel["profit_per_litre"] == 220                       # 2900 - 2680
    assert diesel["litres_in_tanks"] == 9500                       # 4000 + 6000 - 500
    assert diesel["capital_in_stock"] == 9500 * 2680
    assert diesel["expected_profit"] == 9500 * 220

    sold = next(f for f in c["period"]["fuels"] if f["fuel"] == "Diesel")
    assert (sold["litres_sold"], sold["sales"], sold["cost_of_fuel_sold"], sold["profit"]) == (
        500, 1_450_000, 1_340_000, 110_000)

    calc = manager.post("/api/capital/calculate", json={"litres": 10000, "buying_price": 2800, "selling_price": 2950}).json
    assert (calc["capital_needed"], calc["sales"], calc["profit"], calc["profit_per_litre"]) == (
        28_000_000, 29_500_000, 1_500_000, 150)
    assert round(calc["return_on_capital_percent"], 2) == 5.36

    # Manager only.
    manager.post("/api/users", json={"full_name": "Asha", "username": "asha01", "password": "Temp1234"})
    asha = login(app, "asha01", "Temp1234")
    assert asha.get("/api/capital").status_code == 403
    assert asha.post("/api/capital/calculate", json={"litres": 1, "buying_price": 1, "selling_price": 2}).status_code == 403
