"""Expenses and receipt attachments.

Staff: may record expenses (PENDING) and cancel their own pending ones.
Manager: full control - create (auto-approved), approve, reject, edit, void.
Financial records are never hard-deleted.
"""
import uuid
from io import BytesIO
from datetime import timedelta
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file
from werkzeug.utils import secure_filename

from ..auth import current_user, login_required, manager_required
from ..calculations import money
from ..extensions import db
from ..models import Attachment, Expense, ExpenseCategory, Shift, ShiftStaff, User, utcnow
from ..services import audit
from ..services.clock import today_local
from .common import (ApiError, body, clean_text, date_field, decimal_field, get_or_404, optional_text, require_text)

bp = Blueprint("expenses", __name__, url_prefix="/api")

# Receipts: extension -> the real file type its contents must have.
ALLOWED_EXTENSIONS = {".pdf": "pdf", ".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp", ".heic": "heic"}
CONTENT_TYPES = {"pdf": "application/pdf", "png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp",
                 "heic": "image/heic"}


def sniff_file_type(head):
    """Identify a file from its first bytes ("magic numbers")."""
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis"):
        return "heic"
    return None


def _expense_dict(e, names):
    data = e.to_dict()
    data.update(category=e.category.name, entered_by_name=names.get(e.entered_by),
                approved_by_name=names.get(e.approved_by))
    return data


def _names(station_id):
    return {u.id: u.full_name for u in User.query}


@bp.get("/expense-categories")
@login_required
def categories():
    me = current_user()
    rows = ExpenseCategory.query.filter_by(station_id=me.station_id).order_by(ExpenseCategory.name).all()
    return jsonify({"categories": [c.to_dict() for c in rows]})


@bp.post("/expense-categories")
@manager_required
def add_category():
    me = current_user()
    name = require_text(body(), "name", "Category name", 60)
    if ExpenseCategory.query.filter_by(station_id=me.station_id, name=name).first():
        raise ApiError("That category already exists")
    cat = ExpenseCategory(station_id=me.station_id, name=name)
    db.session.add(cat)
    db.session.flush()
    audit.log(me.station_id, me.id, "MANAGER ADDED EXPENSE CATEGORY", "expense_categories", cat.id, new={"name": name})
    db.session.commit()
    return jsonify({"category": cat.to_dict()}), 201


@bp.get("/expenses")
@login_required
def list_expenses():
    me = current_user()
    q = Expense.query.filter_by(station_id=me.station_id)
    if not me.is_manager:
        q = q.filter_by(entered_by=me.id)
    start = date_field(request.args.get("start"), "Start", required=False)
    end = date_field(request.args.get("end"), "End", required=False)
    if start:
        q = q.filter(Expense.expense_date >= start)
    if end:
        q = q.filter(Expense.expense_date <= end)
    if request.args.get("status"):
        q = q.filter(Expense.status == request.args["status"].upper())
    if request.args.get("category_id"):
        q = q.filter(Expense.category_id == int(request.args["category_id"]))
    rows = q.order_by(Expense.expense_date.desc(), Expense.id.desc()).limit(500).all()
    names = _names(me.station_id)
    total = money(sum((e.amount for e in rows if e.status == "APPROVED"), 0))
    return jsonify({"expenses": [_expense_dict(e, names) for e in rows], "approved_total": total})


def resolve_category(data, me):
    """Category typed in the person's own words (suggestions come from existing categories).

    An existing category is matched ignoring capitals; anything new becomes a new category
    so it is suggested next time. A category_id is still accepted.
    """
    name = " ".join(clean_text(data.get("category_name")).split())[:60]
    if not name:
        if data.get("category_id"):
            return get_or_404(ExpenseCategory, int(data["category_id"]), me.station_id, "Category")
        raise ApiError("Category is required — choose a suggestion or type your own")
    cat = (ExpenseCategory.query.filter(ExpenseCategory.station_id == me.station_id,
                                        db.func.lower(ExpenseCategory.name) == name.lower()).first())
    if cat is None:
        cat = ExpenseCategory(station_id=me.station_id, name=name[:1].upper() + name[1:])
        db.session.add(cat)
        db.session.flush()
        audit.log(me.station_id, me.id, f"{me.role} ADDED EXPENSE CATEGORY", "expense_categories", cat.id,
                  new={"name": cat.name})
    elif not cat.is_active:
        cat.is_active = True
    return cat


def _apply(expense, data, me):
    expense.category = resolve_category(data, me)
    raw_date = data.get("expense_date") or (expense.expense_date.isoformat() if expense.expense_date else None)
    expense.expense_date = date_field(raw_date, "Date")
    if expense.expense_date > today_local(me.station_id):
        raise ApiError("Expense date cannot be in the future")
    expense.description = require_text(data, "description", "Description")
    expense.amount = money(decimal_field(data, "amount", "Amount", minimum=0, allow_zero=False))
    expense.reference = optional_text(data, "reference", 80)
    if data.get("receipt_attachment_id"):
        att = get_or_404(Attachment, int(data["receipt_attachment_id"]), me.station_id, "Receipt")
        expense.receipt_attachment_id = att.id


@bp.post("/expenses")
@login_required
def create_expense():
    me = current_user()
    data = body()
    expense = Expense(station_id=me.station_id, entered_by=me.id)
    shift = None
    if data.get("shift_id"):
        # Paid from the till during a shift: counts in that shift's cash balance and that day's money.
        shift = get_or_404(Shift, int(data["shift_id"]), me.station_id, "Shift")
        if not me.is_manager:
            if all(a.user_id != me.id for a in shift.staff):
                raise ApiError("You are not assigned to this shift", 403)
            if shift.status not in ("OPEN", "FLAGGED"):
                raise ApiError("Expenses can only be added to your shift while it is open", 409)
        data["expense_date"] = shift.shift_date.isoformat()
    _apply(expense, data, me)
    expense.shift_id = shift.id if shift else None
    if me.is_manager:
        expense.status, expense.approved_by, expense.approved_at = "APPROVED", me.id, utcnow()
    else:
        expense.status = "PENDING"
    db.session.add(expense)
    db.session.flush()
    if expense.receipt_attachment_id:
        att = db.session.get(Attachment, expense.receipt_attachment_id)
        att.related_table, att.related_id = "expenses", expense.id
    audit.log(me.station_id, me.id, f"{me.role} RECORDED EXPENSE", "expenses", expense.id, new=audit.snapshot(expense))
    db.session.commit()
    return jsonify({"expense": _expense_dict(expense, _names(me.station_id))}), 201


def _save_sheet(me, scope, lines, new_expense):
    """Save an expense sheet ("Matumizi"): every line is own words + amount, saved together.

    `scope` is the query of expenses the sheet shows. Lines with an id update that expense,
    lines without one are new (made by `new_expense()`), and the person's own pending
    expenses in the scope left off the sheet are CANCELLED (never deleted). Expenses already
    decided by the manager, or entered by someone else, are not touched.
    """
    if not isinstance(lines, list) or len(lines) > 100:
        raise ApiError("Send the expense lines as a list (at most 100)")
    mine = {e.id: e for e in scope.filter(Expense.status == "PENDING", Expense.entered_by == me.id)}
    kept, created, changed = set(), 0, 0
    for n, line in enumerate(lines, start=1):
        if not isinstance(line, dict):
            raise ApiError(f"Line {n} is not valid")
        words = " ".join(clean_text(line.get("name")).split())[:60]
        if not words:
            raise ApiError(f"Line {n}: write what the money was for")
        data = {"category_name": words, "description": words, "amount": line.get("amount")}
        if line.get("id"):
            expense = mine.get(int(line["id"]))
            if expense is None:
                raise ApiError(f"Line {n} can no longer be changed (the manager may have decided it)", 409)
            kept.add(expense.id)
            data["expense_date"] = expense.expense_date.isoformat()
            before = audit.snapshot(expense)
            _apply(expense, data, me)
            expense.description = expense.category.name   # one spelling for the words on the sheet
            db.session.flush()
            old, new = audit.changed_fields(before, audit.snapshot(expense))
            if new:
                changed += 1
                audit.log(me.station_id, me.id, f"{me.role} EDITED EXPENSE", "expenses", expense.id, old, new)
        else:
            expense = new_expense()
            data["expense_date"] = expense.expense_date.isoformat()
            _apply(expense, data, me)
            expense.description = expense.category.name
            if me.is_manager:
                expense.status, expense.approved_by, expense.approved_at = "APPROVED", me.id, utcnow()
            db.session.add(expense)
            db.session.flush()
            created += 1
            audit.log(me.station_id, me.id, f"{me.role} RECORDED EXPENSE", "expenses", expense.id,
                      new=audit.snapshot(expense))
    removed = [e for i, e in mine.items() if i not in kept]
    for expense in removed:
        expense.status, expense.status_reason = "CANCELLED", "Removed from the expense sheet"
        audit.log(me.station_id, me.id, f"{me.role} CANCELLED EXPENSE", "expenses", expense.id,
                  {"status": "PENDING"}, {"status": "CANCELLED"})
    db.session.commit()
    return jsonify({"ok": True, "added": created, "changed": changed, "removed": len(removed)})


@bp.put("/shifts/<int:shift_id>/expenses")
@login_required
def save_shift_expenses(shift_id):
    """The shift page's expense sheet: the expenses paid from this shift's cash."""
    me = current_user()
    shift = get_or_404(Shift, shift_id, me.station_id, "Shift")
    if not me.is_manager:
        if all(a.user_id != me.id for a in shift.staff):
            raise ApiError("You are not assigned to this shift", 403)
        if shift.status not in ("OPEN", "FLAGGED"):
            raise ApiError("Expenses can only be changed while your shift is open", 409)
    scope = Expense.query.filter(Expense.station_id == me.station_id, Expense.shift_id == shift.id)
    return _save_sheet(me, scope, body().get("lines"), lambda: Expense(
        station_id=me.station_id, entered_by=me.id, shift_id=shift.id, status="PENDING",
        expense_date=shift.shift_date))


def _my_sheet(me):
    """A staff member's own expense sheet: today's expenses plus those of the shift they are working.

    New lines go on that open shift (so they come off its cash), otherwise on today.
    """
    today = today_local(me.station_id)
    shift = (Shift.query.join(ShiftStaff, ShiftStaff.shift_id == Shift.id)
             .filter(ShiftStaff.user_id == me.id, Shift.station_id == me.station_id,
                     Shift.status.in_(("OPEN", "FLAGGED")), Shift.shift_date >= today - timedelta(days=1))
             .order_by(Shift.shift_date.desc(), Shift.sequence.desc()).first())
    where = Expense.expense_date == today
    if shift is not None:
        where = db.or_(where, Expense.shift_id == shift.id)
    scope = Expense.query.filter(Expense.station_id == me.station_id, Expense.entered_by == me.id, where)
    return today, shift, scope


@bp.get("/staff/expenses")
@login_required
def my_expense_sheet():
    me = current_user()
    today, shift, scope = _my_sheet(me)
    rows = scope.filter(Expense.status != "CANCELLED").order_by(Expense.id).all()
    names = _names(me.station_id)
    return jsonify({"day": today.isoformat(), "shift": shift.label if shift else None,
                    "expenses": [_expense_dict(e, names) for e in rows]})


@bp.put("/staff/expenses")
@login_required
def save_my_expense_sheet():
    me = current_user()
    today, shift, scope = _my_sheet(me)
    return _save_sheet(me, scope, body().get("lines"), lambda: Expense(
        station_id=me.station_id, entered_by=me.id, status="PENDING",
        shift_id=shift.id if shift else None, expense_date=shift.shift_date if shift else today))


@bp.put("/expenses/<int:expense_id>")
@login_required
def update_expense(expense_id):
    me = current_user()
    expense = get_or_404(Expense, expense_id, me.station_id, "Expense")
    data = body()
    if not me.is_manager:
        if expense.entered_by != me.id or expense.status != "PENDING":
            raise ApiError("You can only change your own pending expenses", 403)
    elif expense.status == "APPROVED" and not optional_text(data, "reason"):
        raise ApiError("Please give a reason for changing an approved expense")
    before = audit.snapshot(expense)
    if expense.shift_id:   # paid during a shift: it stays on that shift's day
        data["expense_date"] = expense.expense_date.isoformat()
    _apply(expense, data, me)
    db.session.flush()
    old, new = audit.changed_fields(before, audit.snapshot(expense))
    audit.log(me.station_id, me.id, f"{me.role} EDITED EXPENSE", "expenses", expense.id, old, new,
              reason=optional_text(data, "reason", 500))
    db.session.commit()
    return jsonify({"expense": _expense_dict(expense, _names(me.station_id))})


@bp.post("/expenses/<int:expense_id>/<any(approve, reject, void, cancel):action>")
@login_required
def change_status(expense_id, action):
    me = current_user()
    expense = get_or_404(Expense, expense_id, me.station_id, "Expense")
    reason = optional_text(body(), "reason", 255)
    allowed = {
        "approve": (("PENDING", "REJECTED"), "APPROVED"),
        "reject": (("PENDING",), "REJECTED"),
        "void": (("APPROVED",), "VOID"),
        "cancel": (("PENDING",), "CANCELLED"),
    }
    from_states, to_state = allowed[action]
    if action == "cancel":
        if not me.is_manager and expense.entered_by != me.id:
            raise ApiError("You can only cancel your own expenses", 403)
    elif not me.is_manager:
        raise ApiError("Only the manager can do this", 403)
    if expense.status not in from_states:
        raise ApiError(f"A {expense.status.lower()} expense cannot be {to_state.lower()}", 409)
    if action in ("reject", "void") and not reason:
        raise ApiError("Please give a reason")
    old = expense.status
    expense.status, expense.status_reason = to_state, reason
    if to_state == "APPROVED":
        expense.approved_by, expense.approved_at = me.id, utcnow()
    audit.log(me.station_id, me.id, f"{me.role} {to_state} EXPENSE", "expenses", expense.id,
              {"status": old}, {"status": to_state}, reason=reason)
    db.session.commit()
    return jsonify({"expense": _expense_dict(expense, _names(me.station_id))})


# ------------------------------------------------------------ attachments

@bp.post("/attachments")
@login_required
def upload():
    me = current_user()
    file = request.files.get("file")
    if file is None or not file.filename:
        raise ApiError("Choose a file to upload")
    original = secure_filename(file.filename) or "receipt"
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ApiError("Receipts must be PDF or image files")
    # Check the real contents, not just the name: a web page renamed to .png is refused.
    head = file.stream.read(16)
    file.stream.seek(0)
    kind = sniff_file_type(head)
    if kind is None or kind != ALLOWED_EXTENSIONS[ext]:
        raise ApiError("That file is not a real PDF or photo. Please choose the receipt photo or PDF again.")
    stored = f"{uuid.uuid4().hex}{ext}"          # random name: never the uploader's file name
    if current_app.config["VERCEL"]:
        from vercel.blob import sync as blob
        content = file.read()
        uploaded = blob.put_blob(f"receipts/{stored}", content, access="private",
                                 content_type=CONTENT_TYPES[kind])
        stored, size_bytes = uploaded.pathname, len(content)
    else:
        folder = Path(current_app.config["UPLOAD_DIR"])
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / stored
        file.save(path)
        size_bytes = path.stat().st_size
    att = Attachment(station_id=me.station_id, file_name=original, stored_name=stored,
                     content_type=CONTENT_TYPES[kind],    # decided by the server, never by the browser
                     size_bytes=size_bytes, uploaded_by=me.id)
    db.session.add(att)
    db.session.commit()
    return jsonify({"attachment": att.to_dict()}), 201


@bp.get("/attachments/<int:attachment_id>")
@login_required
def download(attachment_id):
    me = current_user()
    att = get_or_404(Attachment, attachment_id, me.station_id, "File")
    if not me.is_manager and att.uploaded_by != me.id:
        raise ApiError("You cannot open this file", 403)
    # Only ever served as a PDF or an image type (older records: re-derived from the extension).
    mimetype = att.content_type if att.content_type in CONTENT_TYPES.values() else \
        CONTENT_TYPES.get(ALLOWED_EXTENSIONS.get(Path(att.stored_name).suffix.lower()), "application/octet-stream")
    if current_app.config["VERCEL"]:
        from vercel.blob import BlobNotFoundError, sync as blob
        try:
            content = blob.get_blob(att.stored_name, access="private").body
        except BlobNotFoundError:
            raise ApiError("File is missing from storage", 404) from None
        response = send_file(BytesIO(content), mimetype=mimetype, download_name=att.file_name)
    else:
        path = Path(current_app.config["UPLOAD_DIR"]) / att.stored_name
        if not path.exists():
            raise ApiError("File is missing from storage", 404)
        response = send_file(path, mimetype=mimetype, download_name=att.file_name)
    response.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
    return response
