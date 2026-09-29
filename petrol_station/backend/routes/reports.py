"""Report generation and export (manager only)."""
import io

from flask import Blueprint, jsonify, request, send_file

from ..auth import current_user, manager_required
from ..extensions import db
from ..models import Report, Station, User
from ..reports.builder import REPORT_TYPES, build
from ..reports.exporters import to_excel, to_pdf
from ..services import audit
from ..services.settings import get_setting
from .common import ApiError, date_field

bp = Blueprint("reports", __name__, url_prefix="/api/reports")


@bp.get("/types")
@manager_required
def report_types():
    return jsonify({"types": [{"key": k, "label": v} for k, v in REPORT_TYPES.items()]})


@bp.get("/history")
@manager_required
def history():
    me = current_user()
    names = {u.id: u.full_name for u in User.query}
    rows = Report.query.filter_by(station_id=me.station_id).order_by(Report.created_at.desc()).limit(100).all()
    return jsonify({"reports": [dict(r.to_dict(), generated_by_name=names.get(r.generated_by),
                                     label=REPORT_TYPES.get(r.report_type, r.report_type)) for r in rows]})


@bp.get("/<kind>")
@manager_required
def generate(kind):
    me = current_user()
    fmt = request.args.get("format", "json").lower()
    if fmt not in ("json", "pdf", "xlsx"):
        raise ApiError("Format must be json, pdf or xlsx")
    start = date_field(request.args.get("start"), "Start", required=False)
    end = date_field(request.args.get("end"), "End", required=False)
    shift_id = request.args.get("shift_id", type=int)
    try:
        report = build(kind, me.station_id, start, end, shift_id)
    except ValueError as exc:
        raise ApiError(str(exc))
    if fmt == "json":
        return jsonify({"report": report, "currency": get_setting(me.station_id, "currency")})

    station = db.session.get(Station, me.station_id)
    currency = get_setting(me.station_id, "currency")
    filename = f"{kind}_{report['start']}_{report['end']}.{fmt}"
    content = to_pdf(report, station.name, currency) if fmt == "pdf" else to_excel(report, station.name, currency)
    db.session.add(Report(station_id=me.station_id, report_type=kind, period_start=report["start"],
                          period_end=report["end"], format=fmt, file_name=filename, generated_by=me.id))
    audit.log(me.station_id, me.id, "MANAGER EXPORTED REPORT", "reports", None,
              new={"type": kind, "format": fmt, "start": report["start"], "end": report["end"]})
    db.session.commit()
    mimetype = ("application/pdf" if fmt == "pdf" else
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return send_file(io.BytesIO(content), mimetype=mimetype, as_attachment=True, download_name=filename)
