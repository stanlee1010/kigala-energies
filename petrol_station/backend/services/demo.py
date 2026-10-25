"""Training portal practice data (DEMO_MODE only, separate database). Staff-only.

The training station copies the REAL station's set-up (read-only): its shift
pattern, fuels, tanks, pumps (names, numbers and which tank they draw from) and
selling prices. It then creates practice attendants for every shift - as many as
the real shift's "people per shift" (each works their own pumps) - and builds a believable history through the same
services the real system uses. There is no manager login in the training portal.
"""
import hashlib
import json
import random
import secrets
from datetime import datetime, time, timedelta, timezone

from flask import current_app
from sqlalchemy import create_engine, text

from ..calculations import D, litres, money
from ..calculations.fuel import refill_total_cost, revenue
from ..extensions import db
from ..models import (Expense, ExpenseCategory, FuelPrice, FuelRefill, FuelStock, FuelType, Pump, PumpReading,
                      RefillConfirmation, ShiftCash, ShiftDeposit, ShiftDefinition, ShiftStaff, ShiftStaffPump, Station,
                      StockAdjustment,
                      StockThreshold, Supplier, SystemSetting, Tank, User, utcnow)
from . import alerts, stock
from .clock import now_local, practice_time_in_shift, station_tz, today_local
from .settings import get_setting, set_setting
from .shifts import active_definitions, definition_windows, get_or_create_shift, resequence
from .tanks import tank_level

HISTORY_DAYS = 10
TRAINEE_NAMES = ("Asha Juma", "Baraka Ali", "Upendo Mushi", "Daudi Mrema", "Neema Said", "Juma Hassani")
FALLBACK_PRICES = {"PETROL": 3100, "DIESEL": 2950}
COST_BELOW_PRICE = 450   # practice purchase cost per litre = selling price - this
MAX_TRAINEES_PER_SHIFT = 4


# ------------------------------------------------------------ real set-up

def _real_engine():
    url = current_app.config.get("REAL_DATABASE_URL") or ""
    if not url:
        return None
    if url.startswith("sqlite:///") and ":memory:" not in url:
        # Open the real SQLite database READ-ONLY (URI form; spaces in the folder name must be escaped).
        path = url[len("sqlite:///"):].replace("\\", "/").replace(" ", "%20")
        url = f"sqlite:///file:///{path.lstrip('/')}?mode=ro&uri=true"
    return create_engine(url)


def read_real_setup():
    """The real station's shifts, fuels, tanks, pumps and prices, or None if unavailable.

    Online, the training portal asks the real website for it (REAL_SETUP_URL, see
    routes/demo.py::setup) and never holds the real database's password. On the station
    computer it reads the real database directly, read-only (REAL_DATABASE_URL).
    """
    if current_app.config.get("REAL_SETUP_URL"):
        return _fetch_real_setup()
    engine = _real_engine()
    if engine is None:
        return None
    try:
        with engine.connect() as c:
            return read_setup(c)
    except Exception:
        current_app.logger.exception("Training portal: could not read the real station set-up")
        return None
    finally:
        engine.dispose()


def _fetch_real_setup():
    import urllib.request
    request = urllib.request.Request(current_app.config["REAL_SETUP_URL"], headers={
        "X-Training-Token": current_app.config["TRAINING_SYNC_TOKEN"], "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            setup = json.load(response).get("setup")
        return setup if setup and setup.get("shifts") and setup.get("pumps") else None
    except Exception:
        current_app.logger.exception("Training portal: could not fetch the real station set-up")
        return None


def read_setup(c):
    """The set-up of the FIRST station, read through an open database connection."""
    # Several locations: the training portal copies the FIRST station's set-up.
    sid = c.execute(text("SELECT id FROM stations ORDER BY id")).scalar()
    fuels = {r.id: {"code": r.code, "name": r.name, "color": r.color}
             for r in c.execute(text("SELECT id, code, name, color FROM fuel_types WHERE is_active"))}
    try:
        tanks = {r.id: {"fuel": fuels[r.fuel_type_id]["code"], "number": r.tank_number, "name": r.name,
                        "capacity": float(r.capacity_litres)}
                 for r in c.execute(text("SELECT id, fuel_type_id, tank_number, name, capacity_litres "
                                         "FROM tanks WHERE is_active AND station_id = :sid"), {"sid": sid}) if r.fuel_type_id in fuels}
    except Exception:   # real station not upgraded to tanks yet: one tank per fuel
        c.rollback()
        tanks = {}
    for fuel in fuels.values():
        if not any(t["fuel"] == fuel["code"] for t in tanks.values()):
            tanks[f"default-{fuel['code']}"] = {"fuel": fuel["code"], "number": 1,
                                                "name": f"{fuel['name']} Tank 1", "capacity": 30000.0}
    pump_sql = "SELECT fuel_type_id, pump_number, pump_name, tank_id FROM pumps WHERE is_active AND station_id = :sid"
    try:
        pump_rows = list(c.execute(text(pump_sql), {"sid": sid}))
    except Exception:
        c.rollback()
        pump_rows = [(r[0], r[1], r[2], None) for r in c.execute(text(
            "SELECT fuel_type_id, pump_number, pump_name FROM pumps WHERE is_active AND station_id = :sid"), {"sid": sid})]
    setup = {
        "station": c.execute(text("SELECT name FROM stations WHERE id = :sid"), {"sid": sid}).scalar() or "Station",
        "fuels": sorted(fuels.values(), key=lambda f: f["code"]),
        "shifts": _real_shifts(c, sid),
        "tanks": sorted(tanks.values(), key=lambda t: (t["fuel"], t["number"])),
        "pumps": sorted(({"fuel": fuels[fid]["code"], "number": number, "name": name,
                          "tank": tanks[tank_id]["number"] if tank_id in tanks else 1}
                         for fid, number, name, tank_id in pump_rows if fid in fuels),
                        key=lambda p: (p["fuel"], p["number"])),
        "prices": {},
    }
    for r in c.execute(text("SELECT fuel_type_id, price_per_litre FROM fuel_prices WHERE station_id = :sid "
                            "ORDER BY created_at, id"), {"sid": sid}):
        if r.fuel_type_id in fuels:
            setup["prices"][fuels[r.fuel_type_id]["code"]] = float(r.price_per_litre)
    return setup if setup["shifts"] and setup["pumps"] else None


def _real_shifts(c, sid):
    """Active shift types with their people per shift (1 if the real database is not upgraded yet)."""
    sql = ("SELECT code, name, start_time{} FROM shift_definitions "
           "WHERE is_active AND station_id = :sid ORDER BY start_time")
    try:
        rows = [(r.code, r.name, r.start_time, r.staff_needed)
                for r in c.execute(text(sql.format(", staff_needed")), {"sid": sid})]
    except Exception:
        c.rollback()
        rows = [(r.code, r.name, r.start_time, 1) for r in c.execute(text(sql.format("")), {"sid": sid})]
    return [{"code": code, "name": name, "start": str(start)[:5], "people": int(people or 1)}
            for code, name, start, people in rows]


def fingerprint(setup):
    """What the training portal must mirror: shift pattern, tanks and pumps (not prices or data)."""
    if not setup:
        return ""
    key = {k: setup[k] for k in ("shifts", "tanks", "pumps", "fuels")}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]


def needs_rebuild(station_id):
    """True when the real station's shifts/pumps/tanks changed since the practice data was built."""
    real = fingerprint(read_real_setup())
    return bool(real) and real != get_setting(station_id, "training_fingerprint")


def _apply_setup(sid, setup):
    """Make the training station look like the real one (fresh training database only)."""
    fuels = {}
    for f in setup["fuels"]:
        fuel = FuelType.query.filter_by(code=f["code"]).first() or FuelType(code=f["code"])
        fuel.name, fuel.color, fuel.is_active = f["name"], f["color"], True
        db.session.add(fuel)
        fuels[f["code"]] = fuel
    for fuel in FuelType.query.all():
        if fuel.code not in fuels:
            fuel.is_active = False
    db.session.flush()
    for fuel in fuels.values():
        if not StockThreshold.query.filter_by(station_id=sid, fuel_type_id=fuel.id).first():
            db.session.add(StockThreshold(station_id=sid, fuel_type_id=fuel.id, minimum_litres=2000))

    for d in ShiftDefinition.query.filter_by(station_id=sid):
        d.is_active = False
    for s in setup["shifts"]:
        d = ShiftDefinition.query.filter_by(station_id=sid, code=s["code"]).first() or \
            ShiftDefinition(station_id=sid, code=s["code"])
        d.name, d.start_time, d.is_active = s["name"], datetime.strptime(s["start"], "%H:%M").time(), True
        d.staff_needed = s.get("people", 1)
        db.session.add(d)
    db.session.flush()
    resequence(sid)

    # Fresh training database: replace the starter pumps/tanks with the real ones.
    Pump.query.filter_by(station_id=sid).delete()
    Tank.query.filter_by(station_id=sid).delete()
    db.session.flush()
    tanks = {}
    for t in setup["tanks"]:
        tank = Tank(station_id=sid, fuel_type_id=fuels[t["fuel"]].id, tank_number=t["number"], name=t["name"],
                    capacity_litres=t["capacity"])
        db.session.add(tank)
        tanks[(t["fuel"], t["number"])] = tank
    db.session.flush()
    for p in setup["pumps"]:
        tank = tanks.get((p["fuel"], p["tank"])) or next(v for (f, _), v in tanks.items() if f == p["fuel"])
        db.session.add(Pump(station_id=sid, fuel_type_id=fuels[p["fuel"]].id, pump_number=p["number"],
                            pump_name=p["name"], tank_id=tank.id))
    db.session.flush()


# ------------------------------------------------------------------- seed

def _utc(local_dt, station_id):
    """Station-local naive -> UTC naive (for *_at columns)."""
    return local_dt.replace(tzinfo=station_tz(station_id)).astimezone(timezone.utc).replace(tzinfo=None)


def trainees(station_id):
    """Practice accounts: one staff member per active shift, with that shift's hours."""
    defs = {d.id: d for d in ShiftDefinition.query.filter_by(station_id=station_id)}
    day = now_local(station_id).date()
    hours = {d.id: (s.strftime("%H:%M"), e.strftime("%H:%M")) for d, s, e in definition_windows(station_id, day)}
    users = (User.query.filter(User.station_id == station_id, User.role == "STAFF", User.is_active.is_(True),
                               User.username.like("demo.%")).all())
    users.sort(key=lambda u: defs[u.default_shift_definition_id].sequence if u.default_shift_definition_id in defs else 99)
    result = []
    for u in users:
        d = defs.get(u.default_shift_definition_id)
        start, end = hours.get(d.id, ("", "")) if d else ("", "")
        night = bool(start) and (start >= "18:00" or start < "05:00")
        result.append({"username": u.username, "full_name": u.full_name, "role": u.role,
                       "shift": d.name if d else None, "start": start, "end": end, "night": night,
                       "people": (d.staff_needed or 1) if d else 1,
                       "about": f"{d.name} shift attendant" if d else "Pump attendant"})
    return result


def accounts():
    station = Station.query.first()
    return trainees(station.id) if station else []


def scramble_passwords():
    """Give every practice account a fresh random password (older training data used a shared one)."""
    for user in User.query.filter(User.username.like("demo.%")):
        user.set_password(secrets.token_hex(24))


def practice_account(username):
    """The active practice attendant with this username, or None (never a manager or a real account)."""
    station = Station.query.first()
    if station is None:
        return None
    return next((u for u in User.query.filter(User.station_id == station.id, User.role == "STAFF",
                                              User.is_active.is_(True), User.username.like("demo.%"))
                 if u.username == username), None)


def seed(rng=None):
    rng = rng or random.Random(2026)
    station = Station.query.first()
    sid = station.id
    setup = read_real_setup()
    if setup:
        _apply_setup(sid, setup)
    station.name = f"{setup['station'] if setup else station.name} (Training)"
    set_setting(sid, "training_fingerprint", fingerprint(setup))

    # Pause the business clock during the first shift of the practice day.
    real_today = datetime.now(timezone.utc).astimezone(station_tz(sid)).date()
    first = active_definitions(sid)[0]
    base = practice_time_in_shift(sid, real_today, first.id) or datetime.combine(real_today, time(9, 0))
    set_setting(sid, "training_clock", base.isoformat(timespec="minutes"))
    db.session.flush()
    now = now_local(sid)
    first_day = today_local(sid) - timedelta(days=HISTORY_DAYS)
    history_start = datetime.combine(first_day, datetime.min.time())
    db.session.add(SystemSetting(station_id=sid, key="go_live_date", value=first_day.isoformat()))

    # ------------------------- people: as many per shift as the real one has
    for director in User.query.filter_by(role="DIRECTOR").all():   # the training portal is for staff only
        director.is_active = False
    system = User.query.filter_by(role="MANAGER").first()   # owns seeded approvals; cannot log in
    system.username, system.full_name = "training.system", "Training records"
    system.set_password(secrets.token_hex(24))
    system.is_active, system.must_change_password = False, False
    users = []
    pump_count = Pump.query.filter_by(station_id=sid, is_active=True).count()
    for definition in active_definitions(sid):
        # Each person needs a pump of their own, so never more trainees than pumps.
        people = max(1, min(definition.staff_needed or 1, pump_count, MAX_TRAINEES_PER_SHIFT))
        for n in range(1, people + 1):
            user = User(station_id=sid, role="STAFF", created_by=system.id, must_change_password=False,
                        username=f"demo.{definition.code.lower()}{n if n > 1 else ''}"[:60],
                        full_name=TRAINEE_NAMES[len(users) % len(TRAINEE_NAMES)],
                        default_shift_definition_id=definition.id)
            # Nobody knows this password: trainees are signed in by the server (POST /api/demo/login).
            user.set_password(secrets.token_hex(24))
            db.session.add(user)
            users.append(user)
    db.session.flush()

    # --------------------------------------------- prices, tanks, suppliers
    fuels = {f.code: f for f in FuelType.query.filter_by(is_active=True)}
    prices = {code: D((setup or {}).get("prices", {}).get(code) or FALLBACK_PRICES.get(code, 3000)) for code in fuels}
    suppliers = [Supplier(station_id=sid, name=n) for n in ("Puma Energy", "Oryx Energies", "GBP Tanzania")]
    db.session.add_all(suppliers)
    for code, fuel in fuels.items():
        db.session.add(FuelPrice(station_id=sid, fuel_type_id=fuel.id, price_per_litre=prices[code],
                                 confirmation_status="INITIAL", set_by=system.id, note="Copied from the real station",
                                 created_at=_utc(history_start - timedelta(hours=2), sid)))
    tanks = Tank.query.filter_by(station_id=sid, is_active=True).order_by(Tank.fuel_type_id, Tank.tank_number).all()
    for tank in tanks:
        cost = prices[tank.fuel_type.code] - COST_BELOW_PRICE
        db.session.add(StockAdjustment(station_id=sid, fuel_type_id=tank.fuel_type_id, tank_id=tank.id,
                                       adjustment_type="OPENING_BALANCE",
                                       quantity_litres=litres(D(tank.capacity_litres) * D("0.6")),
                                       cost_per_litre=cost, reason="Training opening dip",
                                       adjusted_at=history_start - timedelta(hours=1), created_by=system.id))
    db.session.flush()

    pumps = Pump.query.filter_by(station_id=sid, is_active=True).order_by(Pump.fuel_type_id, Pump.pump_number).all()
    meters = {p.id: D(rng.randint(12000, 48000)) for p in pumps}
    categories = {c.name: c for c in ExpenseCategory.query.filter_by(station_id=sid)}

    # ------------------------------------------------------- past shifts
    past = []
    day = first_day
    while day <= today_local(sid):
        for d, start, end in definition_windows(sid, day):
            if end <= now:
                past.append(get_or_create_shift(sid, day, d.code)[0])
        day += timedelta(days=1)

    short_deposit = None
    for shift in past:
        worker = shift.staff[0].user if shift.staff else users[0]
        # Several people on the shift: share the pumps out, one person per pump.
        owner = {}
        if len(shift.staff) > 1:
            for i, pump in enumerate(pumps):
                assignment = shift.staff[i % len(shift.staff)]
                db.session.add(ShiftStaffPump(shift_staff_id=assignment.id, pump_id=pump.id))
                owner[pump.id] = assignment.user_id
        shift.status, shift.opened_by = "OPEN", worker.id
        shift.started_at = _utc(shift.scheduled_start, sid)
        hours = (shift.scheduled_end - shift.scheduled_start).total_seconds() / 3600
        quiet = shift.scheduled_start.hour >= 18 or shift.scheduled_start.hour < 5

        for fuel in fuels.values():
            stock.ensure_stock_row(shift, fuel.id, worker.id)
        # Refill a tank when it drops below a quarter full.
        for tank in tanks:
            level, capacity = tank_level(tank), D(tank.capacity_litres)
            if level < capacity / 4:
                qty = litres(min(D(12000), capacity - level - 500))
                cost = prices[tank.fuel_type.code] - COST_BELOW_PRICE + rng.randint(-40, 40)
                at = shift.scheduled_start + timedelta(hours=min(2, hours / 3))
                refill = FuelRefill(station_id=sid, shift_id=shift.id, fuel_type_id=tank.fuel_type_id, tank_id=tank.id,
                                    supplier_id=rng.choice(suppliers).id, refill_date=at.date(), refill_time=at.time(),
                                    quantity_litres=qty, cost_per_litre=cost, total_cost=refill_total_cost(qty, cost),
                                    invoice_number=f"INV-{rng.randint(10000, 99999)}", recorded_by=worker.id,
                                    confirmed_by=worker.id, confirmed_at=_utc(at, sid), status="APPROVED",
                                    approved_by=system.id, approved_at=_utc(at + timedelta(hours=3), sid))
                db.session.add(refill)
                db.session.flush()
                db.session.add(RefillConfirmation(refill_id=refill.id, user_id=worker.id, entered_quantity=qty,
                                                  confirmed_quantity=qty, confirmation_result="CONFIRMED_CORRECT",
                                                  confirmed_at=_utc(at, sid)))

        per_hour = (25, 55) if quiet else (45, 95)
        for pump in pumps:
            sold = litres(rng.randint(*per_hour) * max(hours, 1))
            price = prices[pump.fuel_type.code]
            db.session.add(PumpReading(
                station_id=sid, shift_id=shift.id, pump_id=pump.id, fuel_type_id=pump.fuel_type_id, tank_id=pump.tank_id,
                opening_meter=meters[pump.id], expected_opening_meter=meters[pump.id], closing_meter=meters[pump.id] + sold,
                fuel_sold=sold, price_per_litre=price, revenue=revenue(sold, price),
                recorded_by=owner.get(pump.id, worker.id), updated_by=owner.get(pump.id, worker.id),
                opening_recorded_at=shift.started_at,
                closing_recorded_at=_utc(shift.scheduled_end - timedelta(minutes=10), sid)))
            meters[pump.id] += sold
        db.session.flush()

        sales = D(0)
        for row in FuelStock.query.filter_by(shift_id=shift.id).all():
            stock.recompute(row)
        for r in PumpReading.query.filter_by(shift_id=shift.id):
            sales += D(r.revenue)
        # Some customers pay online (mobile money / card): typed as "4000+60000+5000".
        payments = [rng.choice((5000, 10000, 20000, 35000, 50000, 60000)) for _ in range(rng.randint(2, 6))]
        db.session.add(ShiftCash(station_id=sid, shift_id=shift.id, online_expression="+".join(map(str, payments)),
                                 online_total=sum(payments), updated_by=worker.id))
        expected = money(sales - sum(payments))    # cash to hand in (no practice expenses are paid from the till)
        short = rng.random() < 0.12
        actual = money(expected - (rng.choice((5000, 10000, 20000)) if short else 0))
        db.session.add(ShiftDeposit(station_id=sid, shift_id=shift.id, expected_amount=money(expected),
                                    actual_amount=actual, difference=money(actual - expected),
                                    deposit_reference=f"DEP-{shift.id:04d}", deposit_method="Bank deposit",
                                    deposited_at=shift.scheduled_end + timedelta(hours=1), recorded_by=system.id))
        if short:
            short_deposit = (shift, expected, actual)
        shift.status, shift.submitted_by, shift.closed_by = "CLOSED", worker.id, system.id
        shift.submitted_at = shift.ended_at = _utc(shift.scheduled_end, sid)
        shift.approved_at = _utc(shift.scheduled_end + timedelta(hours=1), sid)

    if short_deposit:
        shift, expected, actual = short_deposit
        alerts.raise_alert(sid, "DEPOSIT_DIFFERENCE", f"SHIFT MONEY DIFFERENCE — {shift.label}",
                           f"Expected: {expected:,.2f}. Actual: {actual:,.2f}. Difference: {actual - expected:+,.2f}.",
                           severity="CRITICAL", dedupe_key=f"DEPOSIT:{shift.id}", shift_id=shift.id)

    for offset in range(HISTORY_DAYS, 0, -3):
        d = today_local(sid) - timedelta(days=offset)
        for cat, desc, amount in (("Electricity", "LUKU electricity tokens", 180000),
                                  ("Security", "Night guard allowance", 60000),
                                  ("Cleaning", "Forecourt cleaning supplies", 25000)):
            db.session.add(Expense(station_id=sid, expense_date=d, category=categories[cat], description=desc,
                                   amount=amount + rng.randint(-5, 5) * 1000, entered_by=system.id,
                                   approved_by=system.id, approved_at=utcnow(), status="APPROVED"))

    from .monitor import run_checks
    db.session.flush()
    run_checks(sid)
    for definition in active_definitions(sid):     # every trainee's practice shift on the practice day
        get_or_create_shift(sid, base.date(), definition.code)
    db.session.commit()


def put_trainee_on_shift(user, shift):
    """A trainee always has their practice shift (roster normally does this already)."""
    if shift.status == "SCHEDULED" and all(a.user_id != user.id for a in shift.staff):
        db.session.add(ShiftStaff(shift_id=shift.id, user_id=user.id, source="MANUAL"))
        db.session.flush()


def reset():
    """Wipe the training database and rebuild the practice data from the real set-up."""
    from .bootstrap import bootstrap
    db.session.remove()
    db.drop_all()
    bootstrap()
    seed()
