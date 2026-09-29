"""Automatic first-Wednesday period rollover.

Nobody tells the system that a new month started. Every check (background
monitor + dashboard loads) compares the real station date with the stored
periods. When the date passes a period's end, the period is closed, its
totals are frozen, and the manager gets the monthly review + fuel price
review notifications.
"""
from ..calculations.periods import is_first_wednesday, period_bounds, period_label
from ..extensions import db
from ..models import FuelPrice, FuelType, ReportingPeriod, utcnow
from . import alerts
from .clock import today_local
from .finance import financial_summary


def get_or_create_period(station_id, day):
    # The period that contains `day` (normally exactly the first-Wednesday bounds).
    period = (ReportingPeriod.query
              .filter(ReportingPeriod.station_id == station_id, ReportingPeriod.start_date <= day,
                      ReportingPeriod.end_date >= day)
              .order_by(ReportingPeriod.start_date.desc()).first())
    if period is not None:
        return period
    start, end = period_bounds(day)
    period = ReportingPeriod.query.filter_by(station_id=station_id, start_date=start).first()
    if period is None:
        period = ReportingPeriod(station_id=station_id, start_date=start, end_date=end,
                                 label=period_label(start), status="OPEN")
        db.session.add(period)
        db.session.flush()
    return period


def close_period(period):
    s = financial_summary(period.station_id, period.start_date, period.end_date)
    period.total_revenue = s["revenue"]
    period.fuel_sold_litres = s["fuel_sold_litres"]
    period.fuel_purchased_litres = s["fuel_purchased_litres"]
    period.fuel_purchase_cost = s["fuel_purchase_cost"]
    period.cost_of_fuel_sold = s["cost_of_fuel_sold"]
    period.operating_expenses = s["operating_expenses"]
    period.gross_profit = s["gross_profit"]
    period.net_profit = s["net_profit"]
    period.status = "CLOSED"
    period.closed_at = utcnow()
    return s


def ensure_periods(station_id):
    today = today_local(station_id)
    current = get_or_create_period(station_id, today)
    ended = (ReportingPeriod.query
             .filter(ReportingPeriod.station_id == station_id, ReportingPeriod.status == "OPEN",
                     ReportingPeriod.end_date < today)
             .order_by(ReportingPeriod.start_date).all())
    for period in ended:
        s = close_period(period)
        result = "Net Profit" if s["net_profit"] >= 0 else "Net Loss"
        alerts.raise_alert(
            station_id, "MONTHLY_REVIEW_REQUIRED", "MONTHLY FINANCIAL REVIEW",
            f"The previous reporting period ({period.label}: {period.start_date:%d %b} – {period.end_date:%d %b %Y}) "
            "has ended. Please review the station's revenue, expenses, fuel costs, and profit/loss. "
            f"{result}: {abs(s['net_profit']):,.2f}.",
            severity="CRITICAL", dedupe_key=f"MONTHLY_REVIEW:{period.id}", related_record_id=period.id,
            details={"period_id": period.id})
    if ended and current.price_reviewed_at is None:
        alerts.raise_alert(
            station_id, "FUEL_PRICE_UPDATE_REQUIRED", "FUEL PRICE UPDATE REQUIRED",
            "A new reporting period has started. Please review and update the current fuel selling "
            "prices before continuing.",
            severity="CRITICAL", dedupe_key=f"PRICE_REVIEW:{current.id}", related_record_id=current.id)
    return current


def previous_period(current):
    return (ReportingPeriod.query
            .filter(ReportingPeriod.station_id == current.station_id,
                    ReportingPeriod.start_date < current.start_date)
            .order_by(ReportingPeriod.start_date.desc()).first())


def period_status(station_id):
    today = today_local(station_id)
    current = ensure_periods(station_id)
    previous = previous_period(current)
    price_review_required = (previous is not None and previous.status == "CLOSED"
                             and current.price_reviewed_at is None)
    return {
        "today": today,
        "is_first_wednesday": is_first_wednesday(today),
        "current_period": current.to_dict(),
        "previous_period": previous.to_dict() if previous else None,
        "monthly_review_required": bool(previous and previous.status == "CLOSED" and previous.reviewed_at is None),
        "price_review_required": price_review_required,
        "current_prices": current_prices(station_id),
    }


def current_price(station_id, fuel_type_id):
    return (FuelPrice.query.filter_by(station_id=station_id, fuel_type_id=fuel_type_id)
            .order_by(FuelPrice.created_at.desc(), FuelPrice.id.desc()).first())


def current_prices(station_id):
    result = []
    for fuel in FuelType.query.filter_by(is_active=True).order_by(FuelType.id).all():
        price = current_price(station_id, fuel.id)
        result.append({"fuel_type_id": fuel.id, "fuel": fuel.name, "code": fuel.code, "color": fuel.color,
                       "price_per_litre": price.price_per_litre if price else None,
                       "since": price.created_at if price else None})
    return result
