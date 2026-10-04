"""SHIFT MANAGEMENT, pump readings, tank stock and shift money."""
from datetime import datetime, timedelta

from flask import Blueprint, current_app, jsonify, request

from ..auth import current_user, login_required, manager_required
from ..calculations import ZERO, D, money
from ..calculations.fuel import InvalidMeterReading, expected_shift_money, fuel_sold, revenue
from ..extensions import db
from ..models import (FuelRefill, FuelStock, FuelType, Pump, PumpReading, Shift, ShiftCash, ShiftDeposit,
                      ShiftStaff, ShiftStaffPump, User, utcnow)
from ..services import alerts, audit, cash, stock, tanks
from ..services.clock import now_local, today_local
from ..services.periods import current_price
from ..services.settings import get_setting
from ..services.shifts import NEWEST_FIRST, current_shift, earlier_than, generate_shifts, previous_shift
from .common import (ApiError, body, date_field, decimal_field, get_or_404, optional_text, require_text)

bp = Blueprint("shifts", __name__, url_prefix="/api")

STAFF_EDITABLE = ("OPEN", "FLAGGED")


# ------------------------------------------------------------------ helpers

def _assignment(shift, user_id):
    return next((a for a in shift.staff if a.user_id == user_id), None)


def _assigned_pump_ids(shift, user):
    """Pumps the user works on. No explicit pumps assigned = all active pumps."""
    assignment = _assignment(shift, user.id)
    if assignment and assignment.pumps:
        return {p.pump_id for p in assignment.pumps}
    return None


def _check_can_edit(shift, user, pump_id=None):
    if user.is_manager:
        if shift.status == "CLOSED":
            raise ApiError("This shift is closed. Reopen it first to make a correction.", 409)
        return
    if _assignment(shift, user.id) is None:
        raise ApiError("You are not assigned to this shift", 403)
    if shift.status not in STAFF_EDITABLE:
        msg = {"SCHEDULED": "Start the shift first.",
               "SUBMITTED": "This shift has been submitted and is waiting for manager review.",
               "CLOSED": "This shift is closed and can no longer be changed."}.get(shift.status, "")
        raise ApiError(f"You cannot change this shift. {msg}", 409)
    allowed = _assigned_pump_ids(shift, user)
    if pump_id is not None and allowed is not None and pump_id not in allowed:
        raise ApiError("That pump is not assigned to you for this shift", 403)


def previous_closing_meter(shift, pump_id):
    found = (db.session.query(PumpReading).join(Shift, Shift.id == PumpReading.shift_id)
             .filter(PumpReading.pump_id == pump_id, PumpReading.closing_meter.isnot(None), earlier_than(shift),
                     Shift.station_id == shift.station_id)
             .order_by(*NEWEST_FIRST).first())
    return found.closing_meter if found else None


def _ensure_reading(shift, pump, user_id=None):
    reading = PumpReading.query.filter_by(shift_id=shift.id, pump_id=pump.id).first()
    if reading:
        return reading
    expected = previous_closing_meter(shift, pump.id)
    reading = PumpReading(station_id=shift.station_id, shift_id=shift.id, pump_id=pump.id,
                          fuel_type_id=pump.fuel_type_id, tank_id=pump.tank_id, opening_meter=expected or 0,
                          expected_opening_meter=expected, recorded_by=user_id)
    db.session.add(reading)
    db.session.flush()
    return reading


def _prepare_shift(shift, user_id):
    """Create reading + stock rows so staff can fill them in."""
    for pump in Pump.query.filter_by(station_id=shift.station_id, is_active=True).all():
        _ensure_reading(shift, pump, user_id)
    for fuel in stock.active_fuel_types():
        stock.ensure_stock_row(shift, fuel.id, user_id)


def shift_detail(shift, viewer):
    fuels = {f.id: f for f in FuelType.query.all()}
    pumps = {p.id: p for p in Pump.query.filter_by(station_id=shift.station_id).all()}
    allowed = None if viewer.is_manager else _assigned_pump_ids(shift, viewer)
    prices = {fid: current_price(shift.station_id, fid) for fid in fuels}

    readings = []
    for r in PumpReading.query.filter_by(shift_id=shift.id).all():
        if allowed is not None and r.pump_id not in allowed:
            continue
        pump, fuel = pumps.get(r.pump_id), fuels.get(r.fuel_type_id)
        data = r.to_dict()
        data.update(pump_name=pump.pump_name if pump else "?", pump_number=pump.pump_number if pump else 0,
                    fuel=fuel.name if fuel else "?", fuel_color=fuel.color if fuel else None,
                    current_price=prices[r.fuel_type_id].price_per_litre if prices.get(r.fuel_type_id) else None)
        readings.append(data)
    readings.sort(key=lambda x: (x["fuel_type_id"], x["pump_number"]))

    stock_rows = []
    for row in FuelStock.query.filter_by(shift_id=shift.id).order_by(FuelStock.fuel_type_id).all():
        data = row.to_dict()
        fuel = fuels.get(row.fuel_type_id)
        data["fuel"] = fuel.name if fuel else "?"
        data["fuel_color"] = fuel.color if fuel else None
        stock_rows.append(data)

    revenue_by_fuel = {}
    for r in readings:
        revenue_by_fuel.setdefault(r["fuel"], ZERO)
        revenue_by_fuel[r["fuel"]] += D(r["revenue"], ZERO)
    expected = expected_shift_money(revenue_by_fuel.values())

    staff = []
    for a in shift.staff:
        staff.append({"user_id": a.user_id, "full_name": a.user.full_name, "username": a.user.username,
                      "assigned_role": a.assigned_role, "source": a.source,
                      "pump_ids": [p.pump_id for p in a.pumps],
                      "pumps": [pumps[p.pump_id].pump_name for p in a.pumps if p.pump_id in pumps]})

    refills = FuelRefill.query.filter_by(shift_id=shift.id).order_by(FuelRefill.created_at).all()
    deposit = ShiftDeposit.query.filter_by(shift_id=shift.id).first()
    prev = previous_shift(shift)
    names = {u.id: u.full_name for u in User.query}

    data = shift.to_dict()
    data.update(
        label=shift.label,
        staff=staff,
        readings=readings,
        money=cash.shift_money(shift),
        online_view=cash.online_view(shift),     # night online (fixed) + this shift's online
        # Tank quantities are for the manager only; staff never see or type tank stock.
        stock=stock_rows if viewer.is_manager else [],
        tanks=tanks.shift_breakdown(shift) if viewer.is_manager else [],
        revenue_by_fuel=[{"fuel": k, "revenue": money(v)} for k, v in revenue_by_fuel.items()],
        expected_money=expected,
        total_litres=sum((D(r["fuel_sold"], ZERO) for r in readings), ZERO),
        refills=[dict(r.to_dict(), fuel=r.fuel_type.name, recorded_by_name=names.get(r.recorded_by)) for r in refills],
        deposit=deposit.to_dict() if deposit else None,
        previous_shift={"id": prev.id, "label": prev.label, "status": prev.status} if prev else None,
        opened_by_name=names.get(shift.opened_by), submitted_by_name=names.get(shift.submitted_by),
        closed_by_name=names.get(shift.closed_by),
        tasks={
            "started": shift.status != "SCHEDULED",
            "opening_readings": bool(readings) and all(r["opening_recorded_at"] for r in readings),
            "closing_readings": bool(readings) and all(r["closing_meter"] is not None for r in readings),
            "refills": len(refills),
            "submitted": shift.status in ("SUBMITTED", "CLOSED"),
        },
    )
    return data


def _shift_summary(shift, names):
    total = (db.session.query(db.func.coalesce(db.func.sum(PumpReading.revenue), 0))
             .filter(PumpReading.shift_id == shift.id).scalar())
    litres = (db.session.query(db.func.coalesce(db.func.sum(PumpReading.fuel_sold), 0))
              .filter(PumpReading.shift_id == shift.id).scalar())
    deposit = ShiftDeposit.query.filter_by(shift_id=shift.id).first()
    data = shift.to_dict()
    data.update(label=shift.label, staff=[names.get(a.user_id, "?") for a in shift.staff],
                expected_money=money(total), total_litres=D(litres, ZERO),
                deposited=deposit.actual_amount if deposit else None,
                deposit_difference=deposit.difference if deposit else None)
    return data


# ------------------------------------------------------------------ listing

@bp.get("/shifts")
@manager_required
def list_shifts():
    me = current_user()
    today = today_local(me.station_id)
    start = date_field(request.args.get("start"), "Start", required=False) or today - timedelta(days=6)
    end = date_field(request.args.get("end"), "End", required=False) or today + timedelta(days=1)
    q = Shift.query.filter(Shift.station_id == me.station_id, Shift.shift_date >= start, Shift.shift_date <= end)
    if request.args.get("status"):
        q = q.filter(Shift.status == request.args["status"].upper())
    else:
        q = q.filter(Shift.status != "CANCELLED")
    shifts = q.order_by(Shift.shift_date.desc(), Shift.sequence.desc()).all()
    names = {u.id: u.full_name for u in User.query}
    cur = current_shift(me.station_id)
    db.session.commit()
    return jsonify({"shifts": [_shift_summary(s, names) for s in shifts], "current_shift_id": cur.id,
                    "start": start, "end": end})


@bp.post("/shifts/generate")
@manager_required
def generate():
    me = current_user()
    data = body()
    start = date_field(data.get("start_date"), "Start date")
    end = date_field(data.get("end_date"), "End date")
    try:
        shifts, created = generate_shifts(me.station_id, start, end)
    except ValueError as exc:
        raise ApiError(str(exc))
    audit.log(me.station_id, me.id, "MANAGER GENERATED SHIFTS", "shifts", None,
              new={"start": start, "end": end, "created": created})
    db.session.commit()
    return jsonify({"created": created, "total": len(shifts),
                    "shifts": [{"id": s.id, "label": s.label, "status": s.status} for s in shifts]})


@bp.get("/shifts/<int:shift_id>")
@login_required
def get_shift(shift_id):
    me = current_user()
    shift = get_or_404(Shift, shift_id, me.station_id, "Shift")
    if not me.is_manager and _assignment(shift, me.id) is None:
        raise ApiError("You are not assigned to this shift", 403)
    return jsonify({"shift": shift_detail(shift, me)})


@bp.get("/staff/my-shift")
@login_required
def my_shift():
    """Staff dashboard: the shift this user should be working on now."""
    me = current_user()
    today = today_local(me.station_id)
    cur = current_shift(me.station_id)
    db.session.commit()
    mine = (db.session.query(Shift).join(ShiftStaff, ShiftStaff.shift_id == Shift.id)
            .filter(ShiftStaff.user_id == me.id, Shift.station_id == me.station_id, Shift.status != "CANCELLED",
                    Shift.shift_date >= today - timedelta(days=2))
            .order_by(Shift.shift_date, Shift.sequence).all())
    active = next((s for s in mine if s.status in STAFF_EDITABLE), None)
    if active is None and current_app.config.get("DEMO_MODE") and not me.is_manager:
        # Training portal: the trainee's clock is paused inside their own shift - make sure it is theirs.
        from ..services.demo import put_trainee_on_shift
        put_trainee_on_shift(me, cur)
        db.session.commit()
        db.session.refresh(cur)
        mine.append(cur)
    if active is None:
        active = next((s for s in mine if s.id == cur.id), None)
    # Not on the shift that is running now: the time of day decides, so offer "Start shift" for it
    # (see start_current_shift) instead of showing a later shift from the roster.
    upcoming = [{"id": s.id, "label": s.label, "status": s.status, "scheduled_start": s.scheduled_start.isoformat()}
                for s in mine if s.shift_date >= today]
    prev = previous_shift(cur)
    return jsonify({
        "shift": shift_detail(active, me) if active else None,
        "is_current": bool(active and active.id == cur.id),
        "current_shift": {"id": cur.id, "label": cur.label, "shift_type": cur.shift_type, "status": cur.status,
                          "scheduled_start": cur.scheduled_start.isoformat(),
                          "scheduled_end": cur.scheduled_end.isoformat(),
                          "staff": [a.user.full_name for a in cur.staff],
                          "can_start": active is None and not me.is_manager and cur.status in ("SCHEDULED", "OPEN")},
        "previous_shift": {"label": prev.label, "status": prev.status,
                           "staff": [a.user.full_name for a in prev.staff]} if prev else None,
        "my_shifts": upcoming,
    })


@bp.post("/staff/start-shift")
@login_required
def start_current_shift():
    """Staff press "Start shift": they join the shift that is running now (by the server clock).

    The first person opens it; anyone after that joins the open shift. The assignment is
    SELF, so roster changes never move it.
    """
    me = current_user()
    if me.is_manager:
        raise ApiError("Managers assign shifts on the Shifts page", 403)
    cur = current_shift(me.station_id)
    busy = (db.session.query(Shift).join(ShiftStaff, ShiftStaff.shift_id == Shift.id)
            .filter(ShiftStaff.user_id == me.id, Shift.station_id == me.station_id, Shift.id != cur.id,
                    Shift.status.in_(STAFF_EDITABLE)).order_by(*NEWEST_FIRST).first())
    if busy is not None:
        raise ApiError(f"Please submit your {busy.label} shift before starting a new one", 409)
    if cur.status not in ("SCHEDULED", "OPEN"):
        raise ApiError(f"The {cur.label} shift has already been {cur.status.lower()}. "
                       "Ask the manager if you need to work on it.", 409)
    joined = _assignment(cur, me.id) is None
    if joined:
        cur.staff.append(ShiftStaff(user_id=me.id, assigned_by=me.id, source="SELF"))
    if cur.status == "SCHEDULED":
        cur.status, cur.started_at, cur.opened_by = "OPEN", utcnow(), me.id
        action = "STAFF OPENED SHIFT"
    else:
        action = "STAFF JOINED SHIFT"
    _prepare_shift(cur, me.id)
    audit.log(me.station_id, me.id, action, "shifts", cur.id, None,
              {"status": cur.status, "staff": me.full_name, "self_assigned": joined})
    db.session.commit()
    return jsonify({"shift": shift_detail(cur, me)})


# --------------------------------------------------------------- assignment

@bp.post("/shifts/<int:shift_id>/assign")
@manager_required
def assign_staff(shift_id):
    me = current_user()
    shift = get_or_404(Shift, shift_id, me.station_id, "Shift")
    if shift.status in ("CLOSED", "CANCELLED"):
        raise ApiError(f"Cannot change staff on a {shift.status.lower()} shift")
    data = body()
    user = get_or_404(User, int(data.get("user_id") or 0), me.station_id, "Staff member")
    if not user.is_active:
        raise ApiError("That account is disabled")
    pump_ids = [int(p) for p in data.get("pump_ids") or []]
    valid = {p.id for p in Pump.query.filter(Pump.station_id == me.station_id, Pump.id.in_(pump_ids or [0]))}
    assignment = _assignment(shift, user.id)
    action = "MANAGER CHANGED ASSIGNED STAFF" if assignment else "MANAGER ASSIGNED STAFF"
    if assignment is None:
        assignment = ShiftStaff(shift_id=shift.id, user_id=user.id, assigned_by=me.id)
        db.session.add(assignment)
        db.session.flush()
    assignment.assigned_role = optional_text(data, "assigned_role", 40) or "PUMP_ATTENDANT"
    assignment.assigned_at = utcnow()
    assignment.assigned_by = me.id
    assignment.source = "MANUAL"  # a day-specific choice is never moved by roster changes
    assignment.pumps = [ShiftStaffPump(shift_staff_id=assignment.id, pump_id=p) for p in pump_ids if p in valid]
    audit.log(me.station_id, me.id, action, "shift_staff", assignment.id,
              new={"shift": shift.label, "user": user.username, "pump_ids": sorted(valid)})
    db.session.commit()
    db.session.refresh(shift)
    return jsonify({"shift": shift_detail(shift, me)})


@bp.delete("/shifts/<int:shift_id>/assign/<int:user_id>")
@manager_required
def unassign_staff(shift_id, user_id):
    me = current_user()
    shift = get_or_404(Shift, shift_id, me.station_id, "Shift")
    assignment = _assignment(shift, user_id)
    if assignment is None:
        raise ApiError("That staff member is not assigned to this shift", 404)
    if shift.status == "CLOSED":
        raise ApiError("Cannot change staff on a closed shift")
    audit.log(me.station_id, me.id, "MANAGER REMOVED STAFF FROM SHIFT", "shift_staff", assignment.id,
              old={"shift": shift.label, "user_id": user_id})
    db.session.delete(assignment)
    db.session.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- lifecycle

@bp.post("/shifts/<int:shift_id>/start")
@login_required
def start_shift(shift_id):
    me = current_user()
    shift = get_or_404(Shift, shift_id, me.station_id, "Shift")
    if not me.is_manager and _assignment(shift, me.id) is None:
        raise ApiError("You are not assigned to this shift", 403)
    if shift.status != "SCHEDULED":
        raise ApiError(f"This shift is already {shift.status.lower()}", 409)
    now = now_local(me.station_id)
    if not me.is_manager and now < shift.scheduled_start - timedelta(hours=1):
        raise ApiError(f"This shift starts at {shift.scheduled_start:%d/%m %H:%M}. You can start it up to one hour early.")
    shift.status, shift.started_at, shift.opened_by = "OPEN", utcnow(), me.id
    _prepare_shift(shift, me.id)
    audit.log(me.station_id, me.id, f"{me.role} OPENED SHIFT", "shifts", shift.id, {"status": "SCHEDULED"}, {"status": "OPEN"})
    db.session.commit()
    return jsonify({"shift": shift_detail(shift, me)})


@bp.put("/shifts/<int:shift_id>/readings/<int:pump_id>")
@login_required
def save_reading(shift_id, pump_id):
    me = current_user()
    shift = get_or_404(Shift, shift_id, me.station_id, "Shift")
    pump = get_or_404(Pump, pump_id, me.station_id, "Pump")
    _check_can_edit(shift, me, pump.id)
    data = body()
    reading = _ensure_reading(shift, pump, me.id)
    before = audit.snapshot(reading, ["opening_meter", "closing_meter", "fuel_sold", "revenue", "price_per_litre"])
    reason = optional_text(data, "reason", 500)
    if me.is_manager and shift.status == "SUBMITTED" and not reason:
        raise ApiError("Please give a reason for correcting a submitted shift")

    opening = decimal_field(data, "opening_meter", "Opening meter", required=False, minimum=0)
    closing = decimal_field(data, "closing_meter", "Closing meter", required=False, minimum=0)
    if opening is not None:
        reading.opening_meter = opening
        reading.opening_recorded_at = utcnow()
        tol = D(get_setting(me.station_id, "meter_mismatch_tolerance"), ZERO)
        key = f"METER_OPEN:{shift.id}:{pump.id}"
        if reading.expected_opening_meter is not None and abs(opening - D(reading.expected_opening_meter)) > tol:
            alerts.raise_alert(
                me.station_id, "METER_ERROR", f"PUMP METER MISMATCH — {pump.pump_name}",
                f"Opening meter {opening:,.2f} does not match the previous shift's closing meter "
                f"{D(reading.expected_opening_meter):,.2f} (difference {opening - D(reading.expected_opening_meter):+,.2f}).",
                dedupe_key=key, shift_id=shift.id, fuel_type_id=pump.fuel_type_id, related_record_id=reading.id)
        else:
            alerts.resolve_by_key(me.station_id, key, "Opening meter corrected")

    if closing is not None:
        try:
            sold = fuel_sold(reading.opening_meter, closing)
        except InvalidMeterReading as exc:
            alerts.raise_alert(
                me.station_id, "METER_ERROR", f"INVALID METER READING — {pump.pump_name}",
                f"{me.full_name} entered a closing reading of {closing:,.2f} which is lower than the opening "
                f"reading {D(reading.opening_meter):,.2f} ({shift.label}).",
                dedupe_key=f"METER_CLOSE:{shift.id}:{pump.id}", shift_id=shift.id,
                fuel_type_id=pump.fuel_type_id, related_record_id=reading.id)
            db.session.commit()
            raise ApiError(exc.message, 400, title=exc.title)
        price = current_price(me.station_id, pump.fuel_type_id)
        if price is None:
            raise ApiError("No selling price is set for this fuel. Ask the manager to set the price.")
        reading.closing_meter = closing
        reading.price_per_litre = price.price_per_litre
        reading.fuel_sold = sold
        reading.revenue = revenue(sold, price.price_per_litre)
        reading.closing_recorded_at = utcnow()
        alerts.resolve_by_key(me.station_id, f"METER_CLOSE:{shift.id}:{pump.id}", "Valid closing reading entered")
    elif reading.closing_meter is not None and opening is not None:
        # Opening changed after closing was captured: recompute.
        try:
            reading.fuel_sold = fuel_sold(reading.opening_meter, reading.closing_meter)
        except InvalidMeterReading as exc:
            raise ApiError(exc.message, 400, title=exc.title)
        reading.revenue = revenue(reading.fuel_sold, reading.price_per_litre)

    reading.updated_by = me.id
    reading.tank_id = pump.tank_id
    db.session.flush()
    stock.recompute(stock.ensure_stock_row(shift, pump.fuel_type_id, me.id))
    after = audit.snapshot(reading, ["opening_meter", "closing_meter", "fuel_sold", "revenue", "price_per_litre"])
    old, new = audit.changed_fields(before, after)
    if new:
        action = "MANAGER CORRECTED READING" if me.is_manager else "STAFF RECORDED READING"
        audit.log(me.station_id, me.id, action, "pump_readings", reading.id, old, new, reason=reason)
    db.session.commit()
    return jsonify({"shift": shift_detail(shift, me)})


@bp.put("/shifts/<int:shift_id>/stock/<int:fuel_type_id>")
@manager_required
def save_stock(shift_id, fuel_type_id):
    me = current_user()
    shift = get_or_404(Shift, shift_id, me.station_id, "Shift")
    fuel = get_or_404(FuelType, fuel_type_id, label="Fuel type")
    _check_can_edit(shift, me)
    data = body()
    row = stock.ensure_stock_row(shift, fuel.id, me.id)
    before = audit.snapshot(row, ["opening_stock", "actual_closing_stock"])
    warning = None
    opening = decimal_field(data, "opening_stock", "Opening stock", required=False, minimum=0)
    closing = decimal_field(data, "actual_closing_stock", "Closing stock", required=False, minimum=0)
    try:
        if opening is not None:
            warning = stock.set_opening_stock(row, shift, opening, fuel.name)
        if closing is not None:
            stock.set_actual_closing(row, closing)
    except ValueError as exc:
        raise ApiError(str(exc))
    old, new = audit.changed_fields(before, audit.snapshot(row, ["opening_stock", "actual_closing_stock"]))
    if new:
        audit.log(me.station_id, me.id, f"{me.role} RECORDED TANK STOCK", "fuel_stock", row.id, old, new,
                  reason=optional_text(data, "reason", 500))
    db.session.commit()
    from ..services.monitor import check_low_stock, check_refill_reconciliation
    check_refill_reconciliation(me.station_id)
    check_low_stock(me.station_id)
    db.session.commit()
    result = {"shift": shift_detail(shift, me)}
    if warning:
        result["warning"] = {"title": "FUEL STOCK MISMATCH",
                             "message": "The opening fuel stock does not match the closing fuel stock from the "
                                        "previous shift. Please check carefully.",
                             "details": warning}
    return jsonify(result)


@bp.post("/shifts/<int:shift_id>/submit")
@login_required
def submit_shift(shift_id):
    me = current_user()
    shift = get_or_404(Shift, shift_id, me.station_id, "Shift")
    _check_can_edit(shift, me)
    if shift.status not in STAFF_EDITABLE:
        raise ApiError("Only an open shift can be submitted", 409)
    readings = PumpReading.query.filter_by(shift_id=shift.id).all()
    if not readings:
        raise ApiError("No pump readings have been recorded for this shift")
    missing = [r for r in readings if r.closing_meter is None]
    if missing:
        names = {p.id: p.pump_name for p in Pump.query.filter(Pump.id.in_([r.pump_id for r in missing]))}
        raise ApiError("Enter closing readings for: " + ", ".join(names.get(r.pump_id, "?") for r in missing))
    stock.sync_shift(shift)
    old_status = shift.status
    shift.status, shift.submitted_at, shift.submitted_by, shift.ended_at = "SUBMITTED", utcnow(), me.id, utcnow()
    audit.log(me.station_id, me.id, f"{me.role} SUBMITTED SHIFT", "shifts", shift.id,
              {"status": old_status}, {"status": "SUBMITTED"}, reason=optional_text(body(), "notes", 500))
    db.session.commit()
    return jsonify({"shift": shift_detail(shift, me)})


@bp.post("/shifts/<int:shift_id>/approve")
@manager_required
def approve_shift(shift_id):
    """Manager review: approve and close the shift."""
    me = current_user()
    shift = get_or_404(Shift, shift_id, me.station_id, "Shift")
    if shift.status not in ("SUBMITTED", "FLAGGED", "OPEN"):
        raise ApiError(f"A {shift.status.lower()} shift cannot be approved", 409)
    if PumpReading.query.filter_by(shift_id=shift.id, closing_meter=None).count():
        raise ApiError("All pumps need closing readings before the shift can be closed")
    stock.sync_shift(shift)
    old = shift.status
    shift.status, shift.closed_by, shift.approved_at = "CLOSED", me.id, utcnow()
    shift.ended_at = shift.ended_at or utcnow()
    audit.log(me.station_id, me.id, "MANAGER APPROVED SHIFT", "shifts", shift.id, {"status": old}, {"status": "CLOSED"},
              reason=optional_text(body(), "notes", 500))
    db.session.commit()
    return jsonify({"shift": shift_detail(shift, me)})


@bp.post("/shifts/<int:shift_id>/flag")
@manager_required
def flag_shift(shift_id):
    me = current_user()
    shift = get_or_404(Shift, shift_id, me.station_id, "Shift")
    if shift.status not in ("SUBMITTED", "OPEN"):
        raise ApiError("Only an open or submitted shift can be flagged", 409)
    reason = require_text(body(), "reason", "Reason", 500)
    old = shift.status
    shift.status, shift.flag_reason = "FLAGGED", reason
    audit.log(me.station_id, me.id, "MANAGER FLAGGED SHIFT", "shifts", shift.id, {"status": old}, {"status": "FLAGGED"},
              reason=reason)
    db.session.commit()
    return jsonify({"shift": shift_detail(shift, me)})


@bp.post("/shifts/<int:shift_id>/reopen")
@manager_required
def reopen_shift(shift_id):
    me = current_user()
    shift = get_or_404(Shift, shift_id, me.station_id, "Shift")
    if shift.status != "CLOSED":
        raise ApiError("Only a closed shift can be reopened", 409)
    reason = require_text(body(), "reason", "Reason", 500)
    shift.status = "SUBMITTED"
    audit.log(me.station_id, me.id, "MANAGER REOPENED SHIFT", "shifts", shift.id, {"status": "CLOSED"},
              {"status": "SUBMITTED"}, reason=reason)
    db.session.commit()
    return jsonify({"shift": shift_detail(shift, me)})


# ------------------------------------------------------ online money / cash

@bp.put("/shifts/<int:shift_id>/online")
@login_required
def save_online(shift_id):
    """Money paid online during the shift. '4000+60000+5000' is added up to 69,000."""
    me = current_user()
    shift = get_or_404(Shift, shift_id, me.station_id, "Shift")
    _check_can_edit(shift, me)
    data = body()
    try:
        total, expression = cash.parse_amount_expression(data.get("online_expression"))
    except ValueError as exc:
        raise ApiError(str(exc))
    row = ShiftCash.query.filter_by(shift_id=shift.id).first()
    before = {"online_expression": row.online_expression, "online_total": row.online_total} if row else None
    if row is None:
        row = ShiftCash(station_id=shift.station_id, shift_id=shift.id)
    row.online_expression, row.online_total, row.updated_by = expression, total, me.id
    db.session.add(row)
    db.session.flush()
    audit.log(me.station_id, me.id, f"{me.role} RECORDED ONLINE MONEY", "shift_cash", row.id, before,
              {"shift": shift.label, "online_expression": expression, "online_total": total},
              reason=optional_text(data, "reason", 500))
    db.session.commit()
    return jsonify({"shift": shift_detail(shift, me)})


@bp.get("/money/day")
@manager_required
def money_for_day():
    """MANAGER ONLY: each shift's sales / online / cash for the day, and the totals for all shifts."""
    me = current_user()
    day = date_field(request.args.get("date"), "Date", required=False) or today_local(me.station_id)
    return jsonify(cash.day_money(me.station_id, day))


# --------------------------------------------------------------- deposits

@bp.post("/shifts/<int:shift_id>/deposit")
@manager_required
def record_deposit(shift_id):
    me = current_user()
    shift = get_or_404(Shift, shift_id, me.station_id, "Shift")
    data = body()
    # Cash to hand in = Total - online money - expenses paid from the drawer (online money never reaches the till).
    expected = cash.shift_money(shift)["balance"]
    actual = money(decimal_field(data, "actual_amount", "Actual amount deposited", minimum=0))
    difference = money(actual - expected)
    deposit = ShiftDeposit.query.filter_by(shift_id=shift.id).first()
    before = audit.snapshot(deposit) if deposit else None
    if deposit is None:
        deposit = ShiftDeposit(station_id=me.station_id, shift_id=shift.id, recorded_by=me.id)
    deposited_at = data.get("deposited_at")
    try:
        deposit.deposited_at = datetime.fromisoformat(deposited_at) if deposited_at else now_local(me.station_id)
    except ValueError:
        raise ApiError("Deposit date/time is invalid")
    deposit.expected_amount, deposit.actual_amount, deposit.difference = expected, actual, difference
    deposit.deposit_reference = optional_text(data, "deposit_reference", 120)
    deposit.deposit_method = optional_text(data, "deposit_method", 60)
    deposit.deposit_account = optional_text(data, "deposit_account", 120)
    deposit.notes = optional_text(data, "notes", 500)
    deposit.recorded_by = me.id
    db.session.add(deposit)
    db.session.flush()
    audit.log(me.station_id, me.id, "MANAGER RECORDED DEPOSIT" if before is None else "MANAGER CORRECTED DEPOSIT",
              "shift_deposits", deposit.id, before, audit.snapshot(deposit), reason=deposit.notes)

    key = f"DEPOSIT:{shift.id}"
    tolerance = D(get_setting(me.station_id, "deposit_tolerance"), ZERO)
    warning = None
    if abs(difference) > tolerance:
        warning = {"title": "SHIFT MONEY DIFFERENCE", "expected": expected, "actual": actual, "difference": difference}
        alerts.raise_alert(
            me.station_id, "DEPOSIT_DIFFERENCE", f"SHIFT MONEY DIFFERENCE — {shift.label}",
            f"Expected: {expected:,.2f}. Actual: {actual:,.2f}. Difference: {difference:+,.2f}.",
            severity="CRITICAL" if difference < 0 else "WARNING", dedupe_key=key, shift_id=shift.id,
            related_record_id=deposit.id, details=warning)
    else:
        alerts.resolve_by_key(me.station_id, key, "Deposit matches expected money", me.id)
    db.session.commit()
    return jsonify({"deposit": deposit.to_dict(), "warning": warning})
