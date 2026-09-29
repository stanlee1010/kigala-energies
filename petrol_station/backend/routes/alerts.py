"""Warnings and audit log (manager)."""
from flask import Blueprint, jsonify, request

from ..auth import current_user, manager_required
from ..extensions import db
from ..models import Alert, AuditLog, User
from ..services import alerts, audit
from .common import ApiError, body, date_field, get_or_404, optional_text

bp = Blueprint("alerts", __name__, url_prefix="/api")

SEVERITY_ORDER = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}


@bp.get("/alerts")
@manager_required
def list_alerts():
    me = current_user()
    q = Alert.query.filter_by(station_id=me.station_id)
    resolved = request.args.get("resolved", "0")
    if resolved in ("0", "1"):
        q = q.filter_by(is_resolved=resolved == "1")
    if request.args.get("type"):
        q = q.filter_by(alert_type=request.args["type"].upper())
    rows = q.order_by(Alert.created_at.desc()).limit(300).all()
    if resolved == "0":
        rows.sort(key=lambda a: (SEVERITY_ORDER.get(a.severity, 9), -a.id))
    names = {u.id: u.full_name for u in User.query}
    counts = dict(db.session.query(Alert.alert_type, db.func.count(Alert.id))
                  .filter_by(station_id=me.station_id, is_resolved=False).group_by(Alert.alert_type).all())
    return jsonify({"alerts": [dict(a.to_dict(), resolved_by_name=names.get(a.resolved_by)) for a in rows],
                    "open_counts": counts})


@bp.post("/alerts/<int:alert_id>/resolve")
@manager_required
def resolve_alert(alert_id):
    me = current_user()
    alert = get_or_404(Alert, alert_id, me.station_id, "Alert")
    if alert.is_resolved:
        raise ApiError("This warning is already resolved", 409)
    if alert.alert_type in ("FUEL_PRICE_UPDATE_REQUIRED",):
        raise ApiError("Complete the fuel price review to clear this warning", 409)
    note = optional_text(body(), "note", 500)
    if not note:
        raise ApiError("Please describe how the issue was resolved")
    alerts.resolve(alert, me.id, note)
    audit.log(me.station_id, me.id, "MANAGER RESOLVED WARNING", "alerts", alert.id,
              {"is_resolved": False}, {"is_resolved": True}, reason=note)
    db.session.commit()
    return jsonify({"alert": alert.to_dict()})


@bp.get("/audit")
@manager_required
def audit_log():
    me = current_user()
    q = AuditLog.query.filter_by(station_id=me.station_id)
    if request.args.get("user_id"):
        q = q.filter_by(user_id=int(request.args["user_id"]))
    if request.args.get("table"):
        q = q.filter_by(table_name=request.args["table"])
    if request.args.get("search"):
        q = q.filter(AuditLog.action.ilike(f"%{request.args['search']}%"))
    start = date_field(request.args.get("start"), "Start", required=False)
    end = date_field(request.args.get("end"), "End", required=False)
    if start:
        q = q.filter(AuditLog.created_at >= start)
    if end:
        from datetime import timedelta
        q = q.filter(AuditLog.created_at < end + timedelta(days=1))
    limit = min(int(request.args.get("limit", 200)), 1000)
    rows = q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit).all()
    names = {u.id: u.full_name for u in User.query}
    return jsonify({"logs": [dict(r.to_dict(), user=names.get(r.user_id, "System")) for r in rows]})
