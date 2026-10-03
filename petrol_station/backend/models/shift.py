from ..extensions import db
from .base import LITRES, METER, MONEY, RATE, SerializerMixin, TimestampMixin, utcnow

# Default shift pattern for a new station; the manager can add more (e.g. Afternoon).
DEFAULT_SHIFT_DEFINITIONS = (("MORNING", "Morning", "06:00"), ("NIGHT", "Night", "18:00"))
# SCHEDULED -> OPEN -> SUBMITTED -> CLOSED   (FLAGGED when the manager rejects;
# CANCELLED when the manager switches off a shift type before the shift started)
SHIFT_STATUSES = ("SCHEDULED", "OPEN", "SUBMITTED", "FLAGGED", "CLOSED", "CANCELLED")


class ShiftDefinition(TimestampMixin, SerializerMixin, db.Model):
    """A shift type the station works, e.g. Morning 06:00, Afternoon 14:00, Night 22:00.

    Each active shift ends when the next active shift starts; the last one of
    the day ends at the first one's start time the next day.
    """

    __tablename__ = "shift_definitions"
    __table_args__ = (db.UniqueConstraint("station_id", "code", name="uq_shift_definition_code"),)

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False, index=True)
    code = db.Column(db.String(30), nullable=False)
    name = db.Column(db.String(60), nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    sequence = db.Column(db.Integer, nullable=False, default=1)
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class Shift(TimestampMixin, SerializerMixin, db.Model):
    __tablename__ = "shifts"
    __table_args__ = (
        db.UniqueConstraint("station_id", "shift_date", "shift_type", name="uq_shift_station_date_type"),
        db.CheckConstraint(
            "status IN ('SCHEDULED','OPEN','SUBMITTED','FLAGGED','CLOSED','CANCELLED')", name="ck_shift_status"
        ),
        db.Index("ix_shifts_date", "station_id", "shift_date"),
    )
    local_datetime_fields = ("scheduled_start", "scheduled_end")

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    shift_date = db.Column(db.Date, nullable=False)
    shift_type = db.Column(db.String(30), nullable=False)   # ShiftDefinition.code
    sequence = db.Column(db.Integer, nullable=False, default=1)  # order within the day
    status = db.Column(db.String(12), nullable=False, default="SCHEDULED")
    scheduled_start = db.Column(db.DateTime, nullable=False)  # station local time
    scheduled_end = db.Column(db.DateTime, nullable=False)    # station local time
    started_at = db.Column(db.DateTime)
    ended_at = db.Column(db.DateTime)
    submitted_at = db.Column(db.DateTime)
    approved_at = db.Column(db.DateTime)
    opened_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    submitted_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    closed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    flag_reason = db.Column(db.String(500))
    notes = db.Column(db.String(500))

    staff = db.relationship("ShiftStaff", backref="shift", lazy="selectin", cascade="all, delete-orphan")

    @property
    def sort_key(self):
        return (self.shift_date, self.sequence)

    @property
    def type_name(self):
        return self.shift_type.replace("_", " ").title()

    @property
    def label(self):
        return f"{self.shift_date.strftime('%d %b %Y')} {self.type_name}"

    @property
    def is_locked(self):
        return self.status == "CLOSED"


class ShiftStaff(SerializerMixin, db.Model):
    __tablename__ = "shift_staff"
    __table_args__ = (db.UniqueConstraint("shift_id", "user_id", name="uq_shift_staff"),)

    id = db.Column(db.Integer, primary_key=True)
    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    assigned_role = db.Column(db.String(40), nullable=False, default="PUMP_ATTENDANT")
    assigned_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    assigned_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    # ROSTER = added automatically from the staff member's regular shift; MANUAL = chosen for this day.
    source = db.Column(db.String(10), nullable=False, default="MANUAL")

    user = db.relationship("User", foreign_keys=[user_id], lazy="joined")
    pumps = db.relationship("ShiftStaffPump", lazy="selectin", cascade="all, delete-orphan")


class WeeklyShiftChoice(TimestampMixin, SerializerMixin, db.Model):
    """The shift a staff member chose to work for one week (Monday to Sunday).

    It overrides their regular shift for that week; shift changes always start on a Monday.
    """

    __tablename__ = "weekly_shift_choices"
    __table_args__ = (db.UniqueConstraint("user_id", "week_start", name="uq_weekly_choice_user_week"),)

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    week_start = db.Column(db.Date, nullable=False)          # always a Monday
    shift_definition_id = db.Column(db.Integer, db.ForeignKey("shift_definitions.id"), nullable=False)


class ShiftStaffPump(db.Model):
    """Which pumps a staff member is responsible for during a shift."""

    __tablename__ = "shift_staff_pumps"
    __table_args__ = (db.UniqueConstraint("shift_staff_id", "pump_id", name="uq_shift_staff_pump"),)

    id = db.Column(db.Integer, primary_key=True)
    shift_staff_id = db.Column(db.Integer, db.ForeignKey("shift_staff.id", ondelete="CASCADE"), nullable=False)
    pump_id = db.Column(db.Integer, db.ForeignKey("pumps.id"), nullable=False)


class PumpReading(TimestampMixin, SerializerMixin, db.Model):
    __tablename__ = "pump_readings"
    __table_args__ = (
        db.UniqueConstraint("shift_id", "pump_id", name="uq_reading_shift_pump"),
        db.CheckConstraint("opening_meter >= 0", name="ck_opening_meter_positive"),
        db.CheckConstraint(
            "closing_meter IS NULL OR closing_meter >= opening_meter", name="ck_closing_ge_opening"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id"), nullable=False, index=True)
    pump_id = db.Column(db.Integer, db.ForeignKey("pumps.id"), nullable=False, index=True)
    fuel_type_id = db.Column(db.Integer, db.ForeignKey("fuel_types.id"), nullable=False)
    # Tank the pump drew from during this shift (kept even if the pump is later moved to another tank).
    tank_id = db.Column(db.Integer, db.ForeignKey("tanks.id"), index=True)
    opening_meter = db.Column(METER, nullable=False)
    closing_meter = db.Column(METER)
    fuel_sold = db.Column(LITRES, nullable=False, default=0)
    price_per_litre = db.Column(RATE)
    revenue = db.Column(MONEY, nullable=False, default=0)
    expected_opening_meter = db.Column(METER)
    recorded_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    opening_recorded_at = db.Column(db.DateTime)
    closing_recorded_at = db.Column(db.DateTime)


class FuelStock(TimestampMixin, SerializerMixin, db.Model):
    """Tank stock movement for one fuel type during one shift.

    calculated_closing_stock = opening_stock + refilled_quantity - fuel_sold + adjustments
    """

    __tablename__ = "fuel_stock"
    __table_args__ = (
        db.UniqueConstraint("shift_id", "fuel_type_id", name="uq_stock_shift_fuel"),
        db.CheckConstraint("opening_stock >= 0", name="ck_opening_stock_positive"),
        db.CheckConstraint("refilled_quantity >= 0", name="ck_refilled_positive"),
        db.CheckConstraint("fuel_sold >= 0", name="ck_sold_positive"),
    )

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    fuel_type_id = db.Column(db.Integer, db.ForeignKey("fuel_types.id"), nullable=False)
    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id"), nullable=False, index=True)
    expected_opening_stock = db.Column(LITRES)
    opening_stock = db.Column(LITRES, nullable=False, default=0)
    opening_difference = db.Column(LITRES, nullable=False, default=0)
    opening_confirmed = db.Column(db.Boolean, nullable=False, default=False)
    refilled_quantity = db.Column(LITRES, nullable=False, default=0)
    fuel_sold = db.Column(LITRES, nullable=False, default=0)
    adjustments = db.Column(LITRES, nullable=False, default=0)
    calculated_closing_stock = db.Column(LITRES, nullable=False, default=0)
    actual_closing_stock = db.Column(LITRES)
    stock_difference = db.Column(LITRES)
    status = db.Column(db.String(15), nullable=False, default="OPEN")  # OPEN / BALANCED / MISMATCH
    recorded_by = db.Column(db.Integer, db.ForeignKey("users.id"))


class ShiftCash(TimestampMixin, SerializerMixin, db.Model):
    """Money paid online (mobile money, card, bank) during a shift, typed by staff.

    online_expression keeps exactly what was typed, e.g. "4000+60000+5000";
    online_total is its sum (69000).
    """

    __tablename__ = "shift_cash"
    __table_args__ = (db.CheckConstraint("online_total >= 0", name="ck_online_total_positive"),)

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id"), nullable=False, unique=True)
    online_expression = db.Column(db.String(1000), nullable=False, default="")
    online_total = db.Column(MONEY, nullable=False, default=0)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"))


class ShiftDeposit(TimestampMixin, SerializerMixin, db.Model):
    __tablename__ = "shift_deposits"
    __table_args__ = (db.CheckConstraint("actual_amount >= 0", name="ck_deposit_positive"),)
    local_datetime_fields = ("deposited_at",)

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id"), nullable=False, index=True)
    expected_amount = db.Column(MONEY, nullable=False)
    actual_amount = db.Column(MONEY, nullable=False)
    difference = db.Column(MONEY, nullable=False)
    deposit_reference = db.Column(db.String(120))
    deposit_method = db.Column(db.String(60))
    deposit_account = db.Column(db.String(120))
    deposited_at = db.Column(db.DateTime, nullable=False)  # station local time
    recorded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    notes = db.Column(db.String(500))
