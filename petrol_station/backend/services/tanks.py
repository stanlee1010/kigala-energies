"""Storage tanks: levels, per-shift movement per pump, and the number of tanks.

Tank level is always CALCULATED, never typed in by staff:

    level = opening balance + confirmed refills into the tank
            - litres sold by the pumps that draw from the tank
            +/- manager stock adjustments on the tank

For one shift:  closing = opening + refilled - sold by each pump (+/- adjustments)
"""
from sqlalchemy import func

from ..calculations import ZERO, D, litres
from ..extensions import db
from ..models import (STOCK_REFILL_STATUSES, FuelRefill, FuelType, Pump, PumpReading, Shift, StockAdjustment,
                      Tank)
from .shifts import earlier_than

DEFAULT_CAPACITY = 30000
MAX_TANKS_PER_FUEL = 10


# ---------------------------------------------------------------- lookups

def active_tanks(station_id, fuel_type_id=None):
    q = Tank.query.filter_by(station_id=station_id, is_active=True)
    if fuel_type_id:
        q = q.filter_by(fuel_type_id=fuel_type_id)
    return q.order_by(Tank.fuel_type_id, Tank.tank_number).all()


def default_tank(station_id, fuel_type_id):
    tanks = active_tanks(station_id, fuel_type_id)
    return tanks[0] if tanks else ensure_tank(station_id, fuel_type_id)


def ensure_tank(station_id, fuel_type_id):
    """Every active fuel has at least one tank (Tank 1)."""
    existing = Tank.query.filter_by(station_id=station_id, fuel_type_id=fuel_type_id).order_by(Tank.tank_number).first()
    if existing:
        existing.is_active = True
        return existing
    fuel = db.session.get(FuelType, fuel_type_id)
    tank = Tank(station_id=station_id, fuel_type_id=fuel_type_id, tank_number=1, name=f"{fuel.name} Tank 1",
                capacity_litres=DEFAULT_CAPACITY)
    db.session.add(tank)
    db.session.flush()
    return tank


def ensure_default_tanks(station_id):
    """One tank per active fuel, and every pump/refill/adjustment linked to a tank."""
    for fuel in FuelType.query.filter_by(is_active=True).all():
        if not active_tanks(station_id, fuel.id):
            ensure_tank(station_id, fuel.id)
    for model in (Pump, FuelRefill, StockAdjustment):
        for row in model.query.filter(model.station_id == station_id, model.tank_id.is_(None)).all():
            row.tank_id = default_tank(station_id, row.fuel_type_id).id
    db.session.flush()
    for reading in PumpReading.query.filter(PumpReading.station_id == station_id, PumpReading.tank_id.is_(None)).all():
        pump = db.session.get(Pump, reading.pump_id)
        reading.tank_id = pump.tank_id if pump else default_tank(station_id, reading.fuel_type_id).id
    db.session.flush()


def resolve_tank(station_id, fuel_type_id, tank_id=None):
    """The tank for a refill/adjustment: the chosen one (must hold that fuel) or the fuel's first tank."""
    if tank_id in (None, "", 0, "0"):
        return default_tank(station_id, fuel_type_id)
    tank = db.session.get(Tank, int(tank_id))
    if tank is None or tank.station_id != station_id or not tank.is_active:
        raise ValueError("Choose an active tank")
    if tank.fuel_type_id != fuel_type_id:
        raise ValueError(f"{tank.name} does not hold that fuel")
    return tank


class OverCapacity(ValueError):
    """Calculated stock cannot exceed the configured tank capacity."""


def check_capacity(tank, adding_litres, already_counted=0):
    """Refuse a refill/adjustment that would push the calculated stock above the tank's capacity.

    `already_counted` is the part of this same record already inside the level (a correction).
    """
    level = tank_level(tank) - D(already_counted, ZERO)
    capacity = D(tank.capacity_litres)
    new_level = level + D(adding_litres)
    if new_level > capacity:
        room = max(capacity - level, ZERO)
        raise OverCapacity(
            f"{tank.name} holds {capacity:,.0f} L and already has about {level:,.0f} L, so it can take at most "
            f"{room:,.0f} L. {D(adding_litres):,.0f} L would make it {new_level:,.0f} L, which is more than the tank "
            "can hold. Please check the quantity (or the tank chosen).")
    return new_level


# ------------------------------------------------------------------ levels

def _sum(query):
    return D(query.scalar(), ZERO)


def tank_level(tank, before_shift=None):
    """Litres in the tank now, or at the start of `before_shift`."""
    refills = (db.session.query(func.coalesce(func.sum(FuelRefill.quantity_litres), 0))
               .filter(FuelRefill.tank_id == tank.id, FuelRefill.status.in_(STOCK_REFILL_STATUSES)))
    sales = (db.session.query(func.coalesce(func.sum(PumpReading.fuel_sold), 0))
             .filter(PumpReading.tank_id == tank.id, PumpReading.closing_meter.isnot(None)))
    adjustments = (db.session.query(func.coalesce(func.sum(StockAdjustment.quantity_litres), 0))
                   .filter(StockAdjustment.tank_id == tank.id))
    if before_shift is not None:
        earlier = [s.id for s in Shift.query.filter(Shift.station_id == tank.station_id, earlier_than(before_shift))]
        refills = refills.filter(FuelRefill.shift_id.in_(earlier or [0]))
        sales = sales.filter(PumpReading.shift_id.in_(earlier or [0]))
        # An opening balance is the tank's contents at go-live, so it counts as "at the start" of the
        # shift during which it was recorded; other shift-less adjustments count from when they were made.
        is_opening = StockAdjustment.adjustment_type == "OPENING_BALANCE"
        adjustments = adjustments.filter(
            (StockAdjustment.shift_id.in_(earlier or [0]))
            | (StockAdjustment.shift_id.is_(None) & is_opening
               & (StockAdjustment.adjusted_at < before_shift.scheduled_end))
            | (StockAdjustment.shift_id.is_(None) & ~is_opening
               & (StockAdjustment.adjusted_at < before_shift.scheduled_start)))
    return litres(_sum(adjustments) + _sum(refills) - _sum(sales))


def tank_overview(station_id):
    """All active tanks with current level, capacity, % full and the pumps they feed."""
    from .fuel_thresholds import minimum_for
    result = []
    for tank in active_tanks(station_id):
        level = tank_level(tank)
        capacity = D(tank.capacity_litres)
        pumps = Pump.query.filter_by(station_id=station_id, tank_id=tank.id, is_active=True).order_by(Pump.pump_number)
        minimum = minimum_for(station_id, tank.fuel_type_id)
        result.append({
            "id": tank.id, "name": tank.name, "tank_number": tank.tank_number, "fuel_type_id": tank.fuel_type_id,
            "fuel": tank.fuel_type.name, "code": tank.fuel_type.code, "color": tank.fuel_type.color,
            "capacity_litres": capacity, "litres": level,
            "percent_full": float(level / capacity * 100) if capacity else 0,
            "space_litres": litres(capacity - level),
            "pumps": [p.pump_name for p in pumps],
            "minimum_litres": minimum,
            "is_low": minimum is not None and level <= minimum,
        })
    return result


def shift_breakdown(shift):
    """Per tank for one shift: opening, refilled, sold by each pump, adjustments, closing."""
    rows = []
    for tank in Tank.query.filter_by(station_id=shift.station_id).order_by(Tank.fuel_type_id, Tank.tank_number):
        refilled = _sum(db.session.query(func.coalesce(func.sum(FuelRefill.quantity_litres), 0))
                        .filter(FuelRefill.tank_id == tank.id, FuelRefill.shift_id == shift.id,
                                FuelRefill.status.in_(STOCK_REFILL_STATUSES)))
        per_pump = (db.session.query(Pump.pump_name, func.coalesce(func.sum(PumpReading.fuel_sold), 0))
                    .join(PumpReading, PumpReading.pump_id == Pump.id)
                    .filter(PumpReading.tank_id == tank.id, PumpReading.shift_id == shift.id,
                            PumpReading.closing_meter.isnot(None))
                    .group_by(Pump.pump_name, Pump.pump_number).order_by(Pump.pump_number).all())
        adjusted = _sum(db.session.query(func.coalesce(func.sum(StockAdjustment.quantity_litres), 0))
                        .filter(StockAdjustment.tank_id == tank.id, StockAdjustment.shift_id == shift.id))
        if not tank.is_active and not refilled and not per_pump and not adjusted:
            continue
        opening = tank_level(tank, before_shift=shift)
        sold = sum((D(q, ZERO) for _, q in per_pump), ZERO)
        closing = litres(opening + refilled - sold + adjusted)   # expected tank balance
        capacity = D(tank.capacity_litres)
        rows.append({
            "tank_id": tank.id, "tank": tank.name, "fuel": tank.fuel_type.name, "color": tank.fuel_type.color,
            "capacity": capacity, "opening": opening, "refilled": litres(refilled),
            "pumps": [{"pump": name, "sold": litres(q)} for name, q in per_pump],
            "sold": litres(sold), "adjustments": litres(adjusted), "closing": closing,
            "over_capacity": closing > capacity, "below_zero": closing < 0,
        })
    return rows


# ---------------------------------------------------------- number of tanks

def set_tank_count(station_id, fuel_type_id, target):
    """Add tanks (re-activating removed ones first) or remove the highest-numbered empty tanks.

    Returns (added_names, removed_names). Pumps on a removed tank move to the fuel's first tank.
    """
    if target < 1 or target > MAX_TANKS_PER_FUEL:
        raise ValueError(f"Each fuel needs between 1 and {MAX_TANKS_PER_FUEL} tanks")
    fuel = db.session.get(FuelType, fuel_type_id)
    tanks = Tank.query.filter_by(station_id=station_id, fuel_type_id=fuel_type_id).order_by(Tank.tank_number).all()
    active = [t for t in tanks if t.is_active]
    added, removed = [], []
    if target > len(active):
        for tank in (t for t in tanks if not t.is_active):
            if len(active) + len(added) >= target:
                break
            tank.is_active = True
            added.append(tank)
        number = max((t.tank_number for t in tanks), default=0) + 1
        while len(active) + len(added) < target:
            tank = Tank(station_id=station_id, fuel_type_id=fuel_type_id, tank_number=number,
                        name=f"{fuel.name} Tank {number}", capacity_litres=DEFAULT_CAPACITY)
            db.session.add(tank)
            added.append(tank)
            number += 1
    elif target < len(active):
        to_remove = sorted(active, key=lambda t: t.tank_number, reverse=True)[:len(active) - target]
        not_empty = [f"{t.name} ({tank_level(t):,.0f} L)" for t in to_remove if tank_level(t) > 0]
        if not_empty:
            raise ValueError("A tank can only be removed when it is empty. Still holding fuel: " + ", ".join(not_empty))
        keep = [t for t in active if t not in to_remove][0]
        for tank in to_remove:
            tank.is_active = False
            removed.append(tank)
            for pump in Pump.query.filter_by(tank_id=tank.id).all():
                pump.tank_id = keep.id
    db.session.flush()
    return [t.name for t in added], [t.name for t in removed]
