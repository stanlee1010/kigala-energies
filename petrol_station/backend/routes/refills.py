"""FUEL REFILL ENTRY with mandatory confirmation.

Workflow:
  staff enters refill  -> AWAITING_CONFIRMATION (still editable)
  "NO - go back"       -> DRAFT (editable)            + refill_confirmations row
  "YES - confirm"      -> CONFIRMED (locked for staff) + refill_confirmations row + audit + stock update
  manager              -> APPROVED / FLAGGED / CORRECTED (always with audit)
"""
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from ..auth import current_user, login_required, manager_required
from ..calculations import litres, money
from ..calculations.fuel import refill_total_cost
from ..extensions import db
from ..models import (STAFF_EDITABLE_REFILL_STATUSES, STOCK_REFILL_STATUSES, AuditLog, FuelRefill, FuelType, RefillConfirmation,
                      Shift, Supplier, User, utcnow)
from ..services import audit, stock
from ..services.audit import client_ip
from ..services.clock import now_local
from ..services.monitor import check_low_stock, check_refill_reconciliation, check_tank_levels
from ..services.shifts import get_or_create_shift, locate
from ..services.tanks import OverCapacity, check_capacity, resolve_tank
from .common import (ApiError, body, date_field, decimal_field, get_or_404, optional_text, require_text,
                     time_field)

bp = Blueprint("refills", __name__, url_prefix="/api/refills")

LOCKED_MESSAGE = ("This record can no longer be changed by you. Please contact the manager if a correction "
                  "is required.")


def _refill_dict(refill, names=None, with_history=False):
    names = names or {u.id: u.full_name for u in User.query}
    data = refill.to_dict()
    shift = db.session.get(Shift, refill.shift_id) if refill.shift_id else None
    data.update(fuel=refill.fuel_type.name, fuel_color=refill.fuel_type.color,
                tank=refill.tank.name if refill.tank else None,
                supplier=refill.supplier.name if refill.supplier else None,
                shift_label=shift.label if shift else None,
                recorded_by_name=names.get(refill.recorded_by), confirmed_by_name=names.get(refill.confirmed_by),
                approved_by_name=names.get(refill.approved_by), locked=refill.is_locked_for_staff)
    if with_history:
        data["confirmations"] = [dict(c.to_dict(), user=names.get(c.user_id)) for c in
                                 RefillConfirmation.query.filter_by(refill_id=refill.id).order_by(RefillConfirmation.id)]
        data["audit"] = [dict(a.to_dict(), user=names.get(a.user_id)) for a in
                         AuditLog.query.filter_by(table_name="fuel_refills", record_id=refill.id).order_by(AuditLog.id)]
    return data


def _apply_fields(refill, data, me):
    fuel = get_or_404(FuelType, int(data.get("fuel_type_id") or 0), label="Fuel type")
    if not fuel.is_active:
        raise ApiError("That fuel type is not active")
    refill_date = date_field(data.get("refill_date"), "Date")
    refill_time = time_field(data.get("refill_time"), "Time")
    when = datetime.combine(refill_date, refill_time)
    now = now_local(me.station_id)
    if when > now + timedelta(minutes=10):
        raise ApiError("The refill date/time cannot be in the future")
    if not me.is_manager and when < now - timedelta(days=3):
        raise ApiError("Refills older than 3 days must be entered by the manager")
    qty = decimal_field(data, "quantity_litres", "Quantity refilled", minimum=0, allow_zero=False)
    cost = decimal_field(data, "cost_per_litre", "Cost per litre", minimum=0, allow_zero=False)

    supplier = None
    if data.get("supplier_id"):
        supplier = get_or_404(Supplier, int(data["supplier_id"]), me.station_id, "Supplier")
    elif optional_text(data, "supplier_name"):
        name = optional_text(data, "supplier_name", 150)
        supplier = Supplier.query.filter_by(station_id=me.station_id, name=name).first()
        if supplier is None:
            supplier = Supplier(station_id=me.station_id, name=name)
            db.session.add(supplier)
            db.session.flush()
    if supplier is None:
        raise ApiError("Supplier is required")

    try:
        tank = resolve_tank(me.station_id, fuel.id, data.get("tank_id"))
    except ValueError as exc:
        raise ApiError(str(exc))
    # Calculated stock can never exceed the tank's capacity. (A correction of a refill that is
    # already in the tank only counts the difference.)
    counted = (refill.quantity_litres if refill.id and refill.status in STOCK_REFILL_STATUSES
               and refill.tank_id == tank.id else 0)
    try:
        check_capacity(tank, qty, counted)
    except OverCapacity as exc:
        raise ApiError(str(exc), 400, title="TANK CAPACITY EXCEEDED")

    shift, _ = get_or_create_shift(me.station_id, *locate(me.station_id, when))
    refill.fuel_type, refill.supplier, refill.shift_id = fuel, supplier, shift.id
    refill.tank = tank
    refill.refill_date, refill.refill_time = refill_date, refill_time
    refill.quantity_litres, refill.cost_per_litre = litres(qty), money(cost)
    refill.total_cost = refill_total_cost(qty, cost)     # Total Cost = Quantity x Cost Per Litre
    refill.invoice_number = require_text(data, "invoice_number", "Invoice / delivery number", 80)
    refill.delivery_reference = optional_text(data, "delivery_reference", 80)
    refill.notes = optional_text(data, "notes", 500)


def _summary(refill):
    return {
        "fuel_type": refill.fuel_type.name, "tank": refill.tank.name if refill.tank else None,
        "quantity_litres": refill.quantity_litres,
        "cost_per_litre": refill.cost_per_litre, "total_cost": refill.total_cost,
        "supplier": refill.supplier.name if refill.supplier else None, "invoice_number": refill.invoice_number,
        "date": refill.refill_date, "time": refill.refill_time.strftime("%H:%M"),
    }


@bp.get("")
@login_required
def list_refills():
    me = current_user()
    q = FuelRefill.query.filter_by(station_id=me.station_id)
    if not me.is_manager or request.args.get("mine"):
        q = q.filter_by(recorded_by=me.id)
    if request.args.get("status"):
        q = q.filter(FuelRefill.status == request.args["status"].upper())
    start = date_field(request.args.get("start"), "Start", required=False)
    end = date_field(request.args.get("end"), "End", required=False)
    if start:
        q = q.filter(FuelRefill.refill_date >= start)
    if end:
        q = q.filter(FuelRefill.refill_date <= end)
    names = {u.id: u.full_name for u in User.query}
    rows = q.order_by(FuelRefill.refill_date.desc(), FuelRefill.refill_time.desc()).limit(300).all()
    return jsonify({"refills": [_refill_dict(r, names) for r in rows]})


@bp.get("/<int:refill_id>")
@login_required
def get_refill(refill_id):
    me = current_user()
    refill = get_or_404(FuelRefill, refill_id, me.station_id, "Refill")
    if not me.is_manager and refill.recorded_by != me.id:
        raise ApiError("You can only view your own refills", 403)
    return jsonify({"refill": _refill_dict(refill, with_history=me.is_manager)})


@bp.post("")
@login_required
def create_refill():
    """Step 1: record the refill. It is NOT final until confirmed."""
    me = current_user()
    refill = FuelRefill(station_id=me.station_id, recorded_by=me.id, status="AWAITING_CONFIRMATION")
    _apply_fields(refill, body(), me)
    db.session.add(refill)
    db.session.flush()
    audit.log(me.station_id, me.id, f"{me.role} ENTERED REFILL", "fuel_refills", refill.id, new=audit.snapshot(refill))
    db.session.commit()
    return jsonify({"refill": _refill_dict(refill), "confirm": _summary(refill),
                    "question": "Is this fuel refill information correct?"}), 201


@bp.put("/<int:refill_id>")
@login_required
def update_refill(refill_id):
    me = current_user()
    refill = get_or_404(FuelRefill, refill_id, me.station_id, "Refill")
    if refill.status not in STAFF_EDITABLE_REFILL_STATUSES:
        raise ApiError(LOCKED_MESSAGE, 409, title="REFILL LOCKED")
    if not me.is_manager and refill.recorded_by != me.id:
        raise ApiError("You can only change refills you entered", 403)
    before = audit.snapshot(refill)
    _apply_fields(refill, body(), me)
    refill.status = "AWAITING_CONFIRMATION"
    db.session.flush()
    old, new = audit.changed_fields(before, audit.snapshot(refill))
    audit.log(me.station_id, me.id, f"{me.role} EDITED UNCONFIRMED REFILL", "fuel_refills", refill.id, old, new)
    db.session.commit()
    return jsonify({"refill": _refill_dict(refill), "confirm": _summary(refill),
                    "question": "Is this fuel refill information correct?"})


@bp.post("/<int:refill_id>/confirm")
@login_required
def confirm_refill(refill_id):
    """Step 2: YES - CONFIRM locks the record; NO returns it for correction."""
    me = current_user()
    refill = get_or_404(FuelRefill, refill_id, me.station_id, "Refill")
    if not me.is_manager and refill.recorded_by != me.id:
        raise ApiError("You can only confirm refills you entered", 403)
    if refill.status not in STAFF_EDITABLE_REFILL_STATUSES:
        raise ApiError(LOCKED_MESSAGE, 409, title="REFILL ALREADY CONFIRMED")
    answer = str(body().get("answer", "")).upper()

    if answer == "NO":
        refill.status = "DRAFT"
        db.session.add(RefillConfirmation(refill_id=refill.id, user_id=me.id,
                                          entered_quantity=refill.quantity_litres,
                                          confirmation_result="RETURNED_FOR_CORRECTION", ip_address=client_ip()))
        audit.log(me.station_id, me.id, f"{me.role} RETURNED REFILL FOR CORRECTION", "fuel_refills", refill.id,
                  {"status": "AWAITING_CONFIRMATION"}, {"status": "DRAFT"})
        db.session.commit()
        return jsonify({"refill": _refill_dict(refill), "message": "Please correct the refill details."})

    if answer != "YES":
        raise ApiError("Answer must be YES or NO")

    # Re-check the tank room at the moment of confirming (another refill may have arrived meanwhile).
    if refill.tank is not None:
        try:
            check_capacity(refill.tank, refill.quantity_litres)
        except OverCapacity as exc:
            raise ApiError(str(exc) + " Press NO — GO BACK AND CORRECT.", 409, title="TANK CAPACITY EXCEEDED")

    # Everything below happens in one database transaction.
    before = audit.snapshot(refill)
    refill.status = "CONFIRMED"
    refill.confirmed_by = me.id
    refill.confirmed_at = utcnow()
    db.session.add(RefillConfirmation(refill_id=refill.id, user_id=me.id, entered_quantity=refill.quantity_litres,
                                      confirmed_quantity=refill.quantity_litres,
                                      confirmation_result="CONFIRMED_CORRECT", ip_address=client_ip()))
    audit.log(me.station_id, me.id, f"{me.role} CONFIRMED REFILL", "fuel_refills", refill.id,
              before, audit.snapshot(refill))
    shift = db.session.get(Shift, refill.shift_id)
    if shift and not shift.is_locked:
        # SYSTEM UPDATES FUEL STOCK. A closed shift is left untouched and the
        # reconciliation check below warns the manager instead.
        stock.recompute(stock.ensure_stock_row(shift, refill.fuel_type_id, me.id))
    db.session.commit()
    check_refill_reconciliation(me.station_id)
    check_low_stock(me.station_id)
    check_tank_levels(me.station_id)
    db.session.commit()
    return jsonify({"refill": _refill_dict(refill),
                    "message": f"Fuel refill successfully confirmed. {LOCKED_MESSAGE}"})


# ------------------------------------------------------------ manager review

@bp.post("/<int:refill_id>/approve")
@manager_required
def approve_refill(refill_id):
    me = current_user()
    refill = get_or_404(FuelRefill, refill_id, me.station_id, "Refill")
    if refill.status not in ("CONFIRMED", "FLAGGED", "CORRECTED"):
        raise ApiError("Only confirmed refills can be approved", 409)
    old = refill.status
    refill.status, refill.approved_by, refill.approved_at = "APPROVED", me.id, utcnow()
    refill.manager_note = optional_text(body(), "note", 500) or refill.manager_note
    audit.log(me.station_id, me.id, "MANAGER APPROVED REFILL", "fuel_refills", refill.id,
              {"status": old}, {"status": "APPROVED"}, reason=refill.manager_note)
    db.session.commit()
    return jsonify({"refill": _refill_dict(refill)})


@bp.post("/<int:refill_id>/flag")
@manager_required
def flag_refill(refill_id):
    me = current_user()
    refill = get_or_404(FuelRefill, refill_id, me.station_id, "Refill")
    if refill.status in STAFF_EDITABLE_REFILL_STATUSES:
        raise ApiError("The staff member has not confirmed this refill yet", 409)
    reason = require_text(body(), "reason", "Reason", 500)
    old = refill.status
    refill.status, refill.manager_note = "FLAGGED", reason
    audit.log(me.station_id, me.id, "MANAGER FLAGGED REFILL", "fuel_refills", refill.id,
              {"status": old}, {"status": "FLAGGED"}, reason=reason)
    db.session.commit()
    return jsonify({"refill": _refill_dict(refill)})


@bp.post("/<int:refill_id>/correct")
@manager_required
def correct_refill(refill_id):
    """Controlled correction of a confirmed refill (never a silent edit)."""
    me = current_user()
    refill = get_or_404(FuelRefill, refill_id, me.station_id, "Refill")
    if refill.status in STAFF_EDITABLE_REFILL_STATUSES:
        raise ApiError("Unconfirmed refills can simply be edited", 409)
    data = body()
    reason = require_text(data, "reason", "Reason for correction", 500)
    before = audit.snapshot(refill)
    old_shift_id, old_fuel = refill.shift_id, refill.fuel_type_id
    merged = {
        "fuel_type_id": data.get("fuel_type_id", refill.fuel_type_id),
        "tank_id": data.get("tank_id", refill.tank_id if int(data.get("fuel_type_id", refill.fuel_type_id)) == refill.fuel_type_id else None),
        "refill_date": data.get("refill_date", refill.refill_date.isoformat()),
        "refill_time": data.get("refill_time", refill.refill_time.strftime("%H:%M")),
        "quantity_litres": data.get("quantity_litres", refill.quantity_litres),
        "cost_per_litre": data.get("cost_per_litre", refill.cost_per_litre),
        "supplier_id": data.get("supplier_id", refill.supplier_id),
        "invoice_number": data.get("invoice_number", refill.invoice_number),
        "delivery_reference": data.get("delivery_reference", refill.delivery_reference),
        "notes": data.get("notes", refill.notes),
    }
    _apply_fields(refill, merged, me)
    refill.status, refill.manager_note = "CORRECTED", reason
    refill.approved_by, refill.approved_at = me.id, utcnow()
    db.session.add(RefillConfirmation(refill_id=refill.id, user_id=me.id,
                                      entered_quantity=before["quantity_litres"],
                                      confirmed_quantity=refill.quantity_litres,
                                      confirmation_result="MANAGER_CORRECTED", ip_address=client_ip()))
    db.session.flush()
    old, new = audit.changed_fields(before, audit.snapshot(refill))
    audit.log(me.station_id, me.id, "MANAGER CORRECTED REFILL", "fuel_refills", refill.id, old, new, reason=reason)
    # Manager corrections always flow into the stock ledger (both old and new shift/fuel).
    for shift_id, fuel_id in {(old_shift_id, old_fuel), (refill.shift_id, refill.fuel_type_id)}:
        shift = db.session.get(Shift, shift_id) if shift_id else None
        if shift:
            stock.recompute(stock.ensure_stock_row(shift, fuel_id, me.id))
    db.session.commit()
    check_refill_reconciliation(me.station_id)
    check_low_stock(me.station_id)
    check_tank_levels(me.station_id)
    db.session.commit()
    return jsonify({"refill": _refill_dict(refill, with_history=True)})
