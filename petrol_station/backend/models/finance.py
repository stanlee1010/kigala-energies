from ..extensions import db
from .base import LITRES, MONEY, SerializerMixin, TimestampMixin, utcnow

EXPENSE_STATUSES = ("PENDING", "APPROVED", "REJECTED", "CANCELLED", "VOID")
DEFAULT_EXPENSE_CATEGORIES = (
    "Staff", "Electricity", "Water", "Maintenance", "Security", "Cleaning",
    "Transport", "Repairs", "Equipment", "Fuel delivery", "Other",
)


class ExpenseCategory(TimestampMixin, SerializerMixin, db.Model):
    __tablename__ = "expense_categories"
    __table_args__ = (db.UniqueConstraint("station_id", "name", name="uq_expense_category"),)

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    name = db.Column(db.String(60), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class Expense(TimestampMixin, SerializerMixin, db.Model):
    __tablename__ = "expenses"
    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_expense_amount_positive"),
        db.CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED','CANCELLED','VOID')", name="ck_expense_status"
        ),
        db.Index("ix_expenses_date", "station_id", "expense_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id"))
    expense_date = db.Column(db.Date, nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("expense_categories.id"), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    amount = db.Column(MONEY, nullable=False)
    reference = db.Column(db.String(80))
    receipt_attachment_id = db.Column(db.Integer, db.ForeignKey("attachments.id"))
    entered_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    approved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    status = db.Column(db.String(12), nullable=False, default="PENDING")
    status_reason = db.Column(db.String(255))

    category = db.relationship("ExpenseCategory", lazy="joined")


class ReportingPeriod(TimestampMixin, SerializerMixin, db.Model):
    """A monthly reporting period.

    Each period starts on the FIRST WEDNESDAY of a month and ends on the day
    before the first Wednesday of the following month. The totals are frozen
    into this row when the period is closed.
    """

    __tablename__ = "reporting_periods"
    __table_args__ = (
        db.UniqueConstraint("station_id", "start_date", name="uq_period_start"),
        db.CheckConstraint("end_date >= start_date", name="ck_period_dates"),
        db.CheckConstraint("status IN ('OPEN','CLOSED')", name="ck_period_status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    label = db.Column(db.String(60), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(10), nullable=False, default="OPEN")
    total_revenue = db.Column(MONEY)
    fuel_sold_litres = db.Column(LITRES)
    fuel_purchased_litres = db.Column(LITRES)
    fuel_purchase_cost = db.Column(MONEY)
    cost_of_fuel_sold = db.Column(MONEY)
    operating_expenses = db.Column(MONEY)
    gross_profit = db.Column(MONEY)
    net_profit = db.Column(MONEY)
    closed_at = db.Column(db.DateTime)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)
    price_reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    price_reviewed_at = db.Column(db.DateTime)


class Report(SerializerMixin, db.Model):
    __tablename__ = "reports"

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    report_type = db.Column(db.String(40), nullable=False)
    period_start = db.Column(db.Date)
    period_end = db.Column(db.Date)
    format = db.Column(db.String(10), nullable=False)
    file_name = db.Column(db.String(255))
    generated_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Attachment(SerializerMixin, db.Model):
    __tablename__ = "attachments"
    hidden_fields = ("stored_name",)

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), nullable=False, unique=True)
    content_type = db.Column(db.String(100))
    size_bytes = db.Column(db.Integer, nullable=False)
    related_table = db.Column(db.String(60))
    related_id = db.Column(db.Integer)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
