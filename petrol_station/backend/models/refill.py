from ..extensions import db
from .base import LITRES, MONEY, RATE, SerializerMixin, TimestampMixin, utcnow

REFILL_STATUSES = ("DRAFT", "AWAITING_CONFIRMATION", "CONFIRMED", "APPROVED", "FLAGGED", "CORRECTED")
STAFF_EDITABLE_REFILL_STATUSES = ("DRAFT", "AWAITING_CONFIRMATION")
# Refills in these statuses represent fuel that physically entered the tanks.
STOCK_REFILL_STATUSES = ("CONFIRMED", "APPROVED", "FLAGGED", "CORRECTED")


class FuelRefill(TimestampMixin, SerializerMixin, db.Model):
    __tablename__ = "fuel_refills"
    __table_args__ = (
        db.CheckConstraint("quantity_litres > 0", name="ck_refill_qty_positive"),
        db.CheckConstraint("cost_per_litre >= 0", name="ck_refill_cost_positive"),
        db.CheckConstraint(
            "status IN ('DRAFT','AWAITING_CONFIRMATION','CONFIRMED','APPROVED','FLAGGED','CORRECTED')",
            name="ck_refill_status",
        ),
        db.Index("ix_refills_date", "station_id", "refill_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id"), index=True)
    fuel_type_id = db.Column(db.Integer, db.ForeignKey("fuel_types.id"), nullable=False)
    tank_id = db.Column(db.Integer, db.ForeignKey("tanks.id"), index=True)   # tank the fuel was delivered into
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"))
    refill_date = db.Column(db.Date, nullable=False)
    refill_time = db.Column(db.Time, nullable=False)
    quantity_litres = db.Column(LITRES, nullable=False)
    cost_per_litre = db.Column(RATE, nullable=False)
    total_cost = db.Column(MONEY, nullable=False)
    invoice_number = db.Column(db.String(80))
    delivery_reference = db.Column(db.String(80))
    recorded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    confirmed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    status = db.Column(db.String(25), nullable=False, default="DRAFT")
    confirmed_at = db.Column(db.DateTime)
    approved_at = db.Column(db.DateTime)
    notes = db.Column(db.String(500))
    manager_note = db.Column(db.String(500))

    fuel_type = db.relationship("FuelType", lazy="joined")
    supplier = db.relationship("Supplier", lazy="joined")
    tank = db.relationship("Tank", lazy="joined")

    @property
    def is_locked_for_staff(self):
        return self.status not in STAFF_EDITABLE_REFILL_STATUSES


class RefillConfirmation(SerializerMixin, db.Model):
    """Permanent record of every confirm / return-for-correction decision."""

    __tablename__ = "refill_confirmations"
    __table_args__ = (
        db.CheckConstraint(
            "confirmation_result IN ('CONFIRMED_CORRECT','RETURNED_FOR_CORRECTION','MANAGER_CORRECTED')",
            name="ck_confirmation_result",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    refill_id = db.Column(db.Integer, db.ForeignKey("fuel_refills.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    entered_quantity = db.Column(LITRES, nullable=False)
    confirmed_quantity = db.Column(LITRES)
    confirmation_result = db.Column(db.String(30), nullable=False)
    confirmed_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    ip_address = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


ADJUSTMENT_TYPES = ("OPENING_BALANCE", "DIP_CORRECTION", "LOSS", "RECONCILIATION", "OTHER")


class StockAdjustment(SerializerMixin, db.Model):
    """Manager-controlled stock changes. Quantity is signed (+ adds, - removes).

    OPENING_BALANCE sets the initial tank contents and their cost when the system
    goes live (used by the weighted-average costing).
    """

    __tablename__ = "stock_adjustments"
    __table_args__ = (
        db.CheckConstraint(
            "adjustment_type IN ('OPENING_BALANCE','DIP_CORRECTION','LOSS','RECONCILIATION','OTHER')",
            name="ck_adjustment_type",
        ),
    )
    local_datetime_fields = ("adjusted_at",)

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    fuel_type_id = db.Column(db.Integer, db.ForeignKey("fuel_types.id"), nullable=False)
    tank_id = db.Column(db.Integer, db.ForeignKey("tanks.id"), index=True)
    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id"))
    adjustment_type = db.Column(db.String(20), nullable=False)
    quantity_litres = db.Column(LITRES, nullable=False)
    cost_per_litre = db.Column(RATE)
    reason = db.Column(db.String(500), nullable=False)
    adjusted_at = db.Column(db.DateTime, nullable=False)  # station local time
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
