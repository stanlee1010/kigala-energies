"""CAPITAL (manager only): profit from the selling price and the buying price of fuel.

Buying price = weighted-average cost of the fuel now in the tanks (see calculations/costing.py),
so it follows the real delivery prices. Profit per litre = selling price - buying price.
"""
from flask import Blueprint, jsonify, request

from ..auth import current_user, manager_required
from ..calculations import ZERO, D, litres, money
from ..extensions import db
from ..models import STOCK_REFILL_STATUSES, FuelRefill, FuelType
from ..services.clock import today_local
from ..services.finance import financial_summary, inventory_events
from ..services.periods import current_price, ensure_periods
from ..services.tanks import active_tanks, tank_level
from .common import ApiError, body, date_field, decimal_field

bp = Blueprint("capital", __name__, url_prefix="/api/capital")


def _percent(part, whole):
    return float(D(part) / D(whole) * 100) if D(whole, ZERO) else None


def fuel_capital(station_id, fuel):
    events = inventory_events(station_id, fuel.id)
    avg_cost = money(events[-1].avg_cost_after) if events else ZERO
    last = (FuelRefill.query.filter(FuelRefill.station_id == station_id, FuelRefill.fuel_type_id == fuel.id,
                                    FuelRefill.status.in_(STOCK_REFILL_STATUSES))
            .order_by(FuelRefill.refill_date.desc(), FuelRefill.refill_time.desc(), FuelRefill.id.desc()).first())
    price = current_price(station_id, fuel.id)
    selling = D(price.price_per_litre) if price else None
    in_tanks = litres(sum((tank_level(t) for t in active_tanks(station_id, fuel.id)), ZERO))
    stock_litres = max(in_tanks, ZERO)
    profit_per_litre = money(selling - avg_cost) if selling is not None and avg_cost else None
    return {
        "fuel_type_id": fuel.id, "fuel": fuel.name, "color": fuel.color,
        "selling_price": selling,
        "buying_price": avg_cost or None,                 # weighted-average cost of the fuel in stock
        "last_buying_price": D(last.cost_per_litre) if last else None,
        "last_buying_date": last.refill_date if last else None,
        "profit_per_litre": profit_per_litre,
        "margin_percent": _percent(profit_per_litre, selling) if profit_per_litre is not None else None,
        "litres_in_tanks": in_tanks,
        "capital_in_stock": money(stock_litres * avg_cost),              # money tied up in fuel
        "value_at_selling_price": money(stock_litres * selling) if selling is not None else None,
        "expected_profit": money(stock_litres * profit_per_litre) if profit_per_litre is not None else None,
    }


@bp.get("")
@manager_required
def capital():
    me = current_user()
    period = ensure_periods(me.station_id)
    db.session.commit()
    start = date_field(request.args.get("start"), "Start", required=False) or period.start_date
    end = date_field(request.args.get("end"), "End", required=False) or today_local(me.station_id)
    if end < start:
        raise ApiError("The end date must be on or after the start date")

    fuels = [fuel_capital(me.station_id, f) for f in FuelType.query.filter_by(is_active=True).order_by(FuelType.id)]
    total = lambda key: money(sum((D(f[key]) for f in fuels if f[key] is not None), ZERO))  # noqa: E731

    s = financial_summary(me.station_id, start, end)
    sold = []
    for f in s["by_fuel"]:
        if not D(f["sold_qty"], ZERO) and not D(f["sales_revenue"], ZERO):
            continue
        profit = money(D(f["sales_revenue"]) - D(f["cost_of_fuel_sold"]))
        sold.append({"fuel": f["fuel"], "color": f["color"], "litres_sold": f["sold_qty"],
                     "sales": f["sales_revenue"], "cost_of_fuel_sold": f["cost_of_fuel_sold"], "profit": profit,
                     "profit_per_litre": money(profit / D(f["sold_qty"])) if D(f["sold_qty"], ZERO) else None,
                     "margin_percent": _percent(profit, f["sales_revenue"])})
    return jsonify({
        "fuels": fuels,
        "totals": {"capital_in_stock": total("capital_in_stock"),
                   "value_at_selling_price": total("value_at_selling_price"),
                   "expected_profit": total("expected_profit")},
        "period": {"start": start, "end": end, "label": period.label if start == period.start_date else None,
                   "fuels": sold,
                   "litres_sold": s["fuel_sold_litres"], "sales": s["revenue"],
                   "cost_of_fuel_sold": s["cost_of_fuel_sold"], "profit": s["gross_profit"],
                   "margin_percent": _percent(s["gross_profit"], s["revenue"])},
    })


@bp.post("/calculate")
@manager_required
def calculate():
    """Profit calculator: litres x (selling price - buying price)."""
    data = body()
    qty = decimal_field(data, "litres", "Litres", minimum=0, allow_zero=False)
    buying = decimal_field(data, "buying_price", "Buying price per litre", minimum=0, allow_zero=False)
    selling = decimal_field(data, "selling_price", "Selling price per litre", minimum=0, allow_zero=False)
    capital_needed, sales = money(qty * buying), money(qty * selling)
    profit = money(sales - capital_needed)
    return jsonify({
        "litres": litres(qty), "buying_price": money(buying), "selling_price": money(selling),
        "capital_needed": capital_needed, "sales": sales, "profit": profit,
        "profit_per_litre": money(selling - buying),
        "margin_percent": _percent(profit, sales),                 # profit as % of the selling price
        "return_on_capital_percent": _percent(profit, capital_needed),
        "is_loss": profit < 0,
    })
