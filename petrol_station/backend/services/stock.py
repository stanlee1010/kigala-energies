"""Tank stock per shift.

The closing stock of one shift becomes the expected opening stock of the next.
Refills confirmed by staff update the stock row of their shift automatically.
"""
from decimal import Decimal

from sqlalchemy import func

from ..calculations import ZERO, D, litres
from ..calculations.fuel import closing_stock
from ..extensions import db
from ..models import (STOCK_REFILL_STATUSES, FuelRefill, FuelStock, FuelType, PumpReading, Shift,
                      StockAdjustment)
from . import alerts
from .settings import get_setting
from .shifts import NEWEST_FIRST, earlier_than


def active_fuel_types():
    return FuelType.query.filter_by(is_active=True).order_by(FuelType.id).all()


def closing_value(row):
    return row.actual_closing_stock if row.actual_closing_stock is not None else row.calculated_closing_stock


def opening_balance(station_id, fuel_type_id):
    adj = (StockAdjustment.query
           .filter_by(station_id=station_id, fuel_type_id=fuel_type_id, adjustment_type="OPENING_BALANCE")
           .order_by(StockAdjustment.adjusted_at.desc(), StockAdjustment.id.desc()).first())
    return adj.quantity_litres if adj else None


def previous_stock_row(shift, fuel_type_id):
    """Most recent stock row for this fuel before `shift` -> (FuelStock, Shift) or (None, None)."""
    result = (db.session.query(FuelStock, Shift)
              .join(Shift, Shift.id == FuelStock.shift_id)
              .filter(Shift.station_id == shift.station_id, FuelStock.fuel_type_id == fuel_type_id,
                      earlier_than(shift))
              .order_by(*NEWEST_FIRST).first())
    return result if result else (None, None)


def expected_opening(shift, fuel_type_id):
    row, prev_shift = previous_stock_row(shift, fuel_type_id)
    if row is not None:
        return closing_value(row), prev_shift
    return D(opening_balance(shift.station_id, fuel_type_id), ZERO), None


def get_stock_row(shift_id, fuel_type_id):
    return FuelStock.query.filter_by(shift_id=shift_id, fuel_type_id=fuel_type_id).first()


def ensure_stock_row(shift, fuel_type_id, user_id=None):
    row = get_stock_row(shift.id, fuel_type_id)
    if row:
        return row
    expected, _ = expected_opening(shift, fuel_type_id)
    # Opening stock is carried over automatically from the previous closing (staff no longer type it).
    row = FuelStock(station_id=shift.station_id, shift_id=shift.id, fuel_type_id=fuel_type_id,
                    expected_opening_stock=expected, opening_stock=expected, opening_difference=ZERO,
                    opening_confirmed=True, recorded_by=user_id)
    db.session.add(row)
    db.session.flush()
    recompute(row)
    return row


def counted_refills(shift_id, fuel_type_id):
    total = (db.session.query(func.coalesce(func.sum(FuelRefill.quantity_litres), 0))
             .filter(FuelRefill.shift_id == shift_id, FuelRefill.fuel_type_id == fuel_type_id,
                     FuelRefill.status.in_(STOCK_REFILL_STATUSES)).scalar())
    return D(total, ZERO)


def recompute(row):
    """Refresh refilled/sold/adjustments and the calculated closing stock of a stock row."""
    row.refilled_quantity = litres(counted_refills(row.shift_id, row.fuel_type_id))
    sold = (db.session.query(func.coalesce(func.sum(PumpReading.fuel_sold), 0))
            .filter(PumpReading.shift_id == row.shift_id, PumpReading.fuel_type_id == row.fuel_type_id,
                    PumpReading.closing_meter.isnot(None)).scalar())
    row.fuel_sold = litres(sold)
    adjusted = (db.session.query(func.coalesce(func.sum(StockAdjustment.quantity_litres), 0))
                .filter(StockAdjustment.shift_id == row.shift_id,
                        StockAdjustment.fuel_type_id == row.fuel_type_id,
                        StockAdjustment.adjustment_type != "OPENING_BALANCE").scalar())
    row.adjustments = litres(adjusted)
    row.calculated_closing_stock = closing_stock(row.opening_stock, row.refilled_quantity,
                                                 row.fuel_sold, row.adjustments)
    if row.actual_closing_stock is not None:
        row.stock_difference = litres(D(row.actual_closing_stock) - row.calculated_closing_stock)
        tolerance = D(get_setting(row.station_id, "stock_mismatch_tolerance_litres"), ZERO)
        row.status = "BALANCED" if abs(row.stock_difference) <= tolerance else "MISMATCH"
    else:
        row.stock_difference = None
        row.status = "OPEN"
    return row


def set_opening_stock(row, shift, value, fuel_name):
    """Record the opening tank quantity and warn if it differs from the previous closing."""
    value = litres(value)
    if value < 0:
        raise ValueError("Opening stock cannot be negative")
    expected, prev_shift = expected_opening(shift, row.fuel_type_id)
    row.expected_opening_stock = expected
    row.opening_stock = value
    row.opening_difference = litres(value - D(expected, ZERO))
    row.opening_confirmed = True
    recompute(row)

    key = f"STOCK_MISMATCH:{shift.id}:{row.fuel_type_id}"
    tolerance = D(get_setting(shift.station_id, "stock_mismatch_tolerance_litres"), ZERO)
    if prev_shift is not None and abs(row.opening_difference) > tolerance:
        details = {
            "fuel": fuel_name,
            "previous_date": prev_shift.shift_date, "previous_shift": prev_shift.shift_type,
            "previous_closing": expected,
            "current_date": shift.shift_date, "current_shift": shift.shift_type,
            "current_opening": value, "difference": row.opening_difference,
        }
        alerts.raise_alert(
            shift.station_id, "STOCK_MISMATCH", f"FUEL STOCK MISMATCH — {fuel_name}",
            "The opening fuel stock does not match the closing fuel stock from the previous shift. "
            f"Previous closing ({prev_shift.label}): {expected:,.0f} L. "
            f"Current opening ({shift.label}): {value:,.0f} L. Difference: {row.opening_difference:+,.0f} L.",
            severity="CRITICAL", dedupe_key=key, shift_id=shift.id, fuel_type_id=row.fuel_type_id,
            related_record_id=row.id, details=details)
        return details
    alerts.resolve_by_key(shift.station_id, key, "Opening stock now matches previous closing")
    return None


def set_actual_closing(row, value):
    value = litres(value)
    if value < 0:
        raise ValueError("Closing stock cannot be negative")
    row.actual_closing_stock = value
    recompute(row)
    return row


def sync_shift(shift):
    """Make sure every fuel with readings/refills in this shift has an up-to-date stock row."""
    fuel_ids = {r.fuel_type_id for r in PumpReading.query.filter_by(shift_id=shift.id).all()}
    fuel_ids |= {r.fuel_type_id for r in FuelRefill.query.filter_by(shift_id=shift.id).all()}
    rows = []
    for fuel_type_id in sorted(fuel_ids):
        row = ensure_stock_row(shift, fuel_type_id)
        if not shift.is_locked:
            recompute(row)
        rows.append(row)
    return rows


def current_stock(station_id):
    """Latest known tank quantity per active fuel type."""
    result = []
    for fuel in active_fuel_types():
        found = (db.session.query(FuelStock, Shift)
                 .join(Shift, Shift.id == FuelStock.shift_id)
                 .filter(Shift.station_id == station_id, FuelStock.fuel_type_id == fuel.id)
                 .order_by(*NEWEST_FIRST).first())
        if found:
            row, shift = found
            qty, source = closing_value(row), f"{shift.label} ({'dip' if row.actual_closing_stock is not None else 'calculated'})"
        else:
            qty, source = D(opening_balance(station_id, fuel.id), ZERO), "Opening balance"
        result.append({"fuel_type_id": fuel.id, "fuel": fuel.name, "code": fuel.code, "color": fuel.color,
                       "litres": Decimal(qty), "source": source})
    return result
