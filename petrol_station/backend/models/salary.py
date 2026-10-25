from ..extensions import db
from .base import MONEY, SerializerMixin, TimestampMixin

SALARY_ADVANCE_STATUSES = ("ACTIVE", "VOID")


class SalaryMonth(TimestampMixin, SerializerMixin, db.Model):
    """A staff member's salary for one month (YYYY-MM).

    A month without its own row uses the latest earlier salary (it carries forward),
    so the manager only types a salary again when it changes.
    """

    __tablename__ = "salary_months"
    __table_args__ = (
        db.UniqueConstraint("station_id", "user_id", "month", name="uq_salary_month"),
        db.CheckConstraint("amount >= 0", name="ck_salary_amount_not_negative"),
    )

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    month = db.Column(db.String(7), nullable=False)
    amount = db.Column(MONEY, nullable=False)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)


class SalaryAdvance(TimestampMixin, SerializerMixin, db.Model):
    """Money a staff member took, deducted from the salary of the month it was taken in.

    Never deleted: a wrong entry is VOIDed by the manager with a reason.
    """

    __tablename__ = "salary_advances"
    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_salary_advance_amount_positive"),
        db.CheckConstraint("status IN ('ACTIVE','VOID')", name="ck_salary_advance_status"),
        db.Index("ix_salary_advances_month", "station_id", "month"),
    )

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    month = db.Column(db.String(7), nullable=False)
    taken_on = db.Column(db.Date, nullable=False)
    amount = db.Column(MONEY, nullable=False)
    note = db.Column(db.String(255))
    recorded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(8), nullable=False, default="ACTIVE")
    void_reason = db.Column(db.String(255))
    voided_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    voided_at = db.Column(db.DateTime)
