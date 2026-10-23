import json

from ..extensions import db
from .base import SerializerMixin, utcnow

ALERT_TYPES = (
    "LOW_STOCK", "STOCK_MISMATCH", "METER_ERROR", "MISSING_SHIFT", "DEPOSIT_DIFFERENCE",
    "REFILL_NOT_RECONCILED", "FUEL_PRICE_UPDATE_REQUIRED", "MONTHLY_REVIEW_REQUIRED",
    "MISSING_STOCK_UPDATE", "TANK_OVER_CAPACITY", "TANK_STOCK_NEGATIVE", "SECURITY",
)
SEVERITIES = ("INFO", "WARNING", "CRITICAL")


class Alert(SerializerMixin, db.Model):
    __tablename__ = "alerts"
    __table_args__ = (
        db.CheckConstraint("severity IN ('INFO','WARNING','CRITICAL')", name="ck_alert_severity"),
        db.Index("ix_alerts_open", "station_id", "is_resolved", "created_at"),
        db.Index("ix_alerts_dedupe", "station_id", "dedupe_key", "is_resolved"),
    )

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id"))
    fuel_type_id = db.Column(db.Integer, db.ForeignKey("fuel_types.id"))
    alert_type = db.Column(db.String(40), nullable=False)
    severity = db.Column(db.String(10), nullable=False, default="WARNING")
    title = db.Column(db.String(150), nullable=False)
    message = db.Column(db.Text, nullable=False)
    details = db.Column(db.Text)  # JSON
    related_record_id = db.Column(db.Integer)
    dedupe_key = db.Column(db.String(120))
    is_resolved = db.Column(db.Boolean, nullable=False, default=False)
    resolved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    resolved_at = db.Column(db.DateTime)
    resolution_note = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def to_dict(self, *extra):
        data = super().to_dict(*extra)
        data["details"] = json.loads(self.details) if self.details else None
        return data


class AuditLog(SerializerMixin, db.Model):
    """Append-only history: who did what, when, previous value, new value and why."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        db.Index("ix_audit_record", "table_name", "record_id"),
        db.Index("ix_audit_station_time", "station_id", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(80), nullable=False)
    table_name = db.Column(db.String(60))
    record_id = db.Column(db.Integer)
    old_values = db.Column(db.Text)
    new_values = db.Column(db.Text)
    reason = db.Column(db.String(500))
    ip_address = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def to_dict(self, *extra):
        data = super().to_dict(*extra)
        data["old_values"] = json.loads(self.old_values) if self.old_values else None
        data["new_values"] = json.loads(self.new_values) if self.new_values else None
        return data
