"""Real-time business checks, run by the background scheduler and on dashboard loads.

Every run:
  * creates today's and tomorrow's Morning + Night shifts (never duplicates)
  * detects the first-Wednesday period rollover (profit/loss + price review)
  * raises MISSING_SHIFT for shifts that ended without being started
  * raises / clears LOW_STOCK against manager thresholds
  * raises / clears REFILL_NOT_RECONCILED and MISSING_STOCK_UPDATE
"""
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func

from ..calculations import ZERO, D
from ..extensions import db
from ..models import (STOCK_REFILL_STATUSES, Alert, FuelRefill, FuelStock, FuelType, Shift, Station,
                      StockThreshold)
from . import alerts
from .clock import now_local, today_local, utc_to_local
from .periods import ensure_periods
from .settings import get_setting
from .shifts import definition_windows, get_or_create_shift
from .stock import current_stock

LOOKBACK_DAYS = 60


def ensure_upcoming_shifts(station_id):
    """Today's and tomorrow's shifts for every active shift type (with the roster applied)."""
    today = today_local(station_id)
    for day in (today, today + timedelta(days=1)):
        for definition, _, _ in definition_windows(station_id, day):
            get_or_create_shift(station_id, day, definition.code)


def _go_live_date(station_id):
    value = get_setting(station_id, "go_live_date")
    if value:
        return date.fromisoformat(value)
    station = db.session.get(Station, station_id)
    return utc_to_local(station_id, station.created_at).date()


def check_missing_shifts(station_id):
    now = now_local(station_id)
    since = max(_go_live_date(station_id), now.date() - timedelta(days=14))
    missing = Shift.query.filter(Shift.station_id == station_id, Shift.status == "SCHEDULED",
                                 Shift.scheduled_end <= now, Shift.shift_date >= since).all()
    for shift in missing:
        alerts.raise_alert(
            station_id, "MISSING_SHIFT", f"MISSING SHIFT — {shift.label}",
            f"The {shift.shift_type.title()} shift of {shift.shift_date:%d/%m/%Y} ended without being "
            "opened. No readings were recorded.",
            dedupe_key=f"MISSING_SHIFT:{shift.id}", shift_id=shift.id, related_record_id=shift.id)
    for alert in Alert.query.filter_by(station_id=station_id, alert_type="MISSING_SHIFT", is_resolved=False).all():
        shift = db.session.get(Shift, alert.shift_id) if alert.shift_id else None
        if shift is None or shift.status != "SCHEDULED":
            alerts.resolve(alert, None, "Shift has since been opened")


def check_low_stock(station_id):
    thresholds = {t.fuel_type_id: D(t.minimum_litres) for t in StockThreshold.query.filter_by(station_id=station_id)}
    for item in current_stock(station_id):
        minimum = thresholds.get(item["fuel_type_id"])
        key = f"LOW_STOCK:{item['fuel_type_id']}"
        if minimum is not None and item["litres"] <= minimum:
            alerts.raise_alert(
                station_id, "LOW_STOCK", f"LOW {item['fuel'].upper()} STOCK",
                f"Current stock: {item['litres']:,.0f} L (minimum {minimum:,.0f} L). Please arrange a refill.",
                severity="CRITICAL" if item["litres"] <= minimum / 2 else "WARNING",
                dedupe_key=key, fuel_type_id=item["fuel_type_id"],
                details={"current": item["litres"], "minimum": minimum})
        else:
            alerts.resolve_by_key(station_id, key, "Stock is above the minimum level")


def check_refill_reconciliation(station_id):
    since = today_local(station_id) - timedelta(days=LOOKBACK_DAYS)
    groups = (db.session.query(FuelRefill.shift_id, FuelRefill.fuel_type_id,
                               func.sum(FuelRefill.quantity_litres), func.min(FuelRefill.refill_date))
              .filter(FuelRefill.station_id == station_id, FuelRefill.status.in_(STOCK_REFILL_STATUSES),
                      FuelRefill.refill_date >= since)
              .group_by(FuelRefill.shift_id, FuelRefill.fuel_type_id).all())
    fuel_names = {f.id: f.name for f in FuelType.query.all()}
    pct = D(get_setting(station_id, "refill_dip_tolerance_pct"), Decimal("5")) / 100

    for shift_id, fuel_type_id, total, refill_date in groups:
        total = D(total, ZERO)
        fuel = fuel_names.get(fuel_type_id, "Fuel")
        key = f"REFILL_RECON:{shift_id}:{fuel_type_id}"
        row = FuelStock.query.filter_by(shift_id=shift_id, fuel_type_id=fuel_type_id).first() if shift_id else None
        details = {"fuel": fuel, "quantity": total, "date": refill_date, "shift_id": shift_id}
        if row is None:
            alerts.raise_alert(
                station_id, "MISSING_STOCK_UPDATE", "REFILL DETECTED BUT STOCK WAS NOT UPDATED",
                f"{fuel}: {total:,.0f} L. Date: {refill_date:%d/%m/%Y}. Please enter or verify the fuel stock change.",
                severity="CRITICAL", dedupe_key=key, shift_id=shift_id, fuel_type_id=fuel_type_id, details=details)
        elif D(row.refilled_quantity, ZERO) + Decimal("0.001") < total:
            missing = total - D(row.refilled_quantity, ZERO)
            alerts.raise_alert(
                station_id, "REFILL_NOT_RECONCILED", "FUEL REFILL NOT RECONCILED",
                f"A {fuel} refill of {missing:,.0f} L was recorded on {refill_date:%d/%m/%Y}, but the stock "
                "ledger has not been updated accordingly. Please review the fuel movement.",
                severity="CRITICAL", dedupe_key=key, shift_id=shift_id, fuel_type_id=fuel_type_id,
                related_record_id=row.id, details=details)
        else:
            alerts.resolve_by_key(station_id, key, "Stock ledger includes the refill")

        # Physical dip check: the tank is short by (almost) exactly the refilled amount.
        if row is not None and row.actual_closing_stock is not None and total > 0:
            dip_key = f"REFILL_DIP:{row.id}"
            shortfall = -D(row.stock_difference, ZERO)
            if abs(shortfall - total) <= total * pct:
                alerts.raise_alert(
                    station_id, "MISSING_STOCK_UPDATE", "REFILL DETECTED BUT STOCK WAS NOT UPDATED",
                    f"{fuel}: {total:,.0f} L was recorded as refilled on {refill_date:%d/%m/%Y}, but the closing "
                    f"tank reading is {shortfall:,.0f} L below the calculated stock. Please enter or verify the "
                    "fuel stock change.",
                    severity="CRITICAL", dedupe_key=dip_key, shift_id=shift_id, fuel_type_id=fuel_type_id,
                    related_record_id=row.id, details=details)
            else:
                alerts.resolve_by_key(station_id, dip_key, "Tank reading reflects the refill")


def check_tank_levels(station_id):
    """Calculated stock must stay between 0 and the tank's capacity."""
    from .tanks import tank_overview
    for t in tank_overview(station_id):
        over_key, under_key = f"TANK_OVER:{t['id']}", f"TANK_NEGATIVE:{t['id']}"
        if t["litres"] > t["capacity_litres"]:
            alerts.raise_alert(
                station_id, "TANK_OVER_CAPACITY", f"TANK OVER CAPACITY — {t['name']}",
                f"The calculated stock is {t['litres']:,.0f} L but {t['name']} only holds {t['capacity_litres']:,.0f} L. "
                "A refill or reading is probably wrong. Please check the refills and pump readings.",
                severity="CRITICAL", dedupe_key=over_key, fuel_type_id=t["fuel_type_id"], related_record_id=t["id"])
        else:
            alerts.resolve_by_key(station_id, over_key, "Tank stock is within capacity")
        if t["litres"] < 0:
            alerts.raise_alert(
                station_id, "TANK_STOCK_NEGATIVE", f"TANK BELOW ZERO — {t['name']}",
                f"The pumps have sold {abs(t['litres']):,.0f} L more than was recorded in {t['name']}. "
                "Record the tank's opening balance (Tanks & Stock) or check for a missing refill.",
                severity="WARNING", dedupe_key=under_key, fuel_type_id=t["fuel_type_id"], related_record_id=t["id"])
        else:
            alerts.resolve_by_key(station_id, under_key, "Tank stock is no longer negative")


def run_checks(station_id):
    ensure_upcoming_shifts(station_id)
    ensure_periods(station_id)
    check_missing_shifts(station_id)
    check_low_stock(station_id)
    check_tank_levels(station_id)
    check_refill_reconciliation(station_id)
    db.session.commit()


def run_all_stations():
    for station in Station.query.filter_by(is_active=True).all():
        try:
            run_checks(station.id)
        except Exception:
            db.session.rollback()
            raise
