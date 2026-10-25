"""Regression tests for the security audit of 25 Oct 2026 (SECURITY-AUDIT.md, findings F-01 ... F-15)."""
import time
from pathlib import Path

from backend.extensions import db

from .conftest import browser_client, login

ROOT = Path(__file__).resolve().parents[1]


def _device(app, ip):
    client = browser_client(app)
    client.environ_base["REMOTE_ADDR"] = ip
    return client


def _new_staff(manager, username="asha01", password="Temp1234"):
    return manager.post("/api/users", json={"full_name": "Asha", "username": username, "password": password}).json["user"]["id"]


# F-01 · only public/ is published as plain files on Vercel (never the server code) -----------------
def test_only_a_harmless_public_folder_is_published_as_files():
    files = [p.name for p in (ROOT / "public").iterdir()]
    assert files == ["README.txt"]                    # nothing private may ever be put there


# F-02 · a temporary password only allows changing it ---------------------------------------------
def test_temporary_password_only_allows_changing_it(app, manager):
    _new_staff(manager)
    asha = login(app, "asha01", "Temp1234", first_login=True)
    r = asha.get("/api/staff/my-shift")
    assert r.status_code == 403 and r.json["must_change_password"] is True
    assert asha.get("/api/staff/my-salary").status_code == 403
    assert asha.get("/api/auth/me").status_code == 200 and asha.get("/api/time").status_code == 200
    assert asha.post("/api/auth/change-password",
                     json={"current_password": "Temp1234", "new_password": "Kazi2026salama"}).status_code == 200
    assert asha.get("/api/staff/my-shift").status_code == 200


def test_a_reset_password_is_temporary_again(app, manager):
    uid = _new_staff(manager)
    asha = login(app, "asha01", "Temp1234")
    manager.post(f"/api/users/{uid}/reset-password", json={"password": "Reset2026a"})
    again = login(app, "asha01", "Reset2026a", first_login=True)
    assert again.get("/api/staff/my-shift").status_code == 403
    assert asha.get("/api/staff/my-shift").status_code == 401      # the old session ended with the reset


# F-03 · no resetting one's own password without the current one ---------------------------------
def test_manager_cannot_reset_own_password_but_can_reset_staff(app, manager):
    me = manager.get("/api/auth/me").json["user"]["id"]
    r = manager.post(f"/api/users/{me}/reset-password", json={"password": "Hijack2026x"})
    assert r.status_code == 403 and "Change password" in r.json["error"]
    assert browser_client(app).post("/api/auth/login",
                                    json={"username": "manager", "password": "Hijack2026x"}).status_code == 401
    uid = _new_staff(manager)
    assert manager.post(f"/api/users/{uid}/reset-password", json={"password": "Reset2026a"}).status_code == 200


# F-05 · logging out ends the session on the server (copies of the cookie stop working) -----------
def test_logout_ends_the_session_even_for_a_copied_cookie(app, manager):
    _new_staff(manager)
    phone = login(app, "asha01", "Temp1234")
    other_device = login(app, "asha01", "Temp1234")
    copied = browser_client(app)
    copied.set_cookie("session", phone.get_cookie("session").value)
    assert copied.get("/api/staff/my-salary").status_code == 200
    phone.post("/api/auth/logout")
    assert copied.get("/api/staff/my-salary").status_code == 401        # the copy no longer works
    assert phone.get("/api/staff/my-salary").status_code == 401
    assert other_device.get("/api/staff/my-salary").status_code == 200  # another device stays logged in


# F-09 · made-up usernames behave exactly like real ones --------------------------------------------
def test_unknown_usernames_take_as_long_and_get_the_same_waits(app, manager):
    _new_staff(manager)
    def avg(username, ip, n=3):
        client, start = _device(app, ip), time.perf_counter()
        for _ in range(n):
            client.post("/api/auth/login", json={"username": username, "password": "wrong-pass1"})
        return (time.perf_counter() - start) / n
    real, made_up = avg("asha01", "10.0.0.1", 2), avg("nobody99", "10.0.0.2", 2)
    assert made_up > real * 0.5                                         # was ~2 ms vs ~165 ms
    answers = {}
    for name, ip in (("asha01", "10.0.0.3"), ("nobody99", "10.0.0.4")):
        client = _device(app, ip)
        answers[name] = [client.post("/api/auth/login", json={"username": name, "password": "x1"}).json
                         for _ in range(3)]
        answers[name].append(client.post("/api/auth/login", json={"username": name, "password": "x1"}).json)
    for real_answer, made_up_answer in zip(answers["asha01"], answers["nobody99"]):
        assert real_answer["error"].split(" Wait")[0] == made_up_answer["error"].split(" Wait")[0]
        assert ("retry_after" in real_answer) == ("retry_after" in made_up_answer)


# F-10 · someone guessing cannot lock the real person out --------------------------------------------
def test_guessing_from_one_device_does_not_lock_the_owner_out(app, manager):
    _new_staff(manager)
    attacker = _device(app, "10.9.9.9")
    for _ in range(5):
        attacker.post("/api/auth/login", json={"username": "asha01", "password": "guess123"})
    assert attacker.post("/api/auth/login", json={"username": "asha01", "password": "Temp1234"}).status_code == 429
    owner = _device(app, "10.0.0.7")
    assert owner.post("/api/auth/login", json={"username": "asha01", "password": "Temp1234"}).status_code == 200


def test_guessing_spread_over_many_devices_locks_the_account_for_a_while(app, manager):
    _new_staff(manager)
    answers = []
    for n in range(7):                         # each device is made to wait after 3 guesses: 7 x 3 > 20
        device = _device(app, f"10.1.0.{n}")
        answers += [device.post("/api/auth/login", json={"username": "asha01", "password": "guess123"}).json
                    for _ in range(3)]
    assert sum(1 for a in answers if a.get("retry_after") == 15 * 60) == 1   # the 20th guess locks the account
    owner = _device(app, "10.0.0.8")
    r = owner.post("/api/auth/login", json={"username": "asha01", "password": "Temp1234"})
    assert r.status_code == 429                                        # locked everywhere for 15 minutes
    from backend.models import LoginFailure
    with app.app_context():
        LoginFailure.query.update({"locked_until": None})
        db.session.commit()
    assert owner.post("/api/auth/login", json={"username": "asha01", "password": "Temp1234"}).status_code == 200


def test_a_password_reset_clears_earlier_wrong_guesses(app, manager):
    uid = _new_staff(manager)
    device = _device(app, "10.2.0.1")
    for _ in range(5):
        device.post("/api/auth/login", json={"username": "asha01", "password": "guess123"})
    manager.post(f"/api/users/{uid}/reset-password", json={"password": "Reset2026a"})
    assert device.post("/api/auth/login", json={"username": "asha01", "password": "Reset2026a"}).status_code == 200


# F-12 · scripts only from this site and the one chart file -------------------------------------------
def test_scripts_are_allowed_only_from_this_site_and_the_chart_file(app):
    csp = app.test_client().get("/login.html").headers["Content-Security-Policy"]
    script_src = next(p for p in csp.split(";") if p.strip().startswith("script-src")).split()[1:]
    assert script_src == ["'self'", "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/"]


# F-13 · passwords people guess first are refused ------------------------------------------------------
def test_easy_passwords_are_refused(app, manager):
    for bad in ("Password1", "password123", "Asha01pass", "Kigala123"):
        r = manager.post("/api/users", json={"full_name": "Asha", "username": "asha01", "password": bad})
        assert r.status_code == 400, bad
    r = manager.post("/api/auth/change-password", json={"current_password": "ChangeMe123!", "new_password": "Short123x"})
    assert r.status_code == 400 and "10 characters" in r.json["error"]      # managers: 10 or more
    r = manager.post("/api/auth/change-password", json={"current_password": "ChangeMe123!", "new_password": "Manager2026x"})
    assert r.status_code == 400                                              # contains the username
    assert manager.post("/api/auth/change-password",
                        json={"current_password": "ChangeMe123!", "new_password": "Kituo4Mafuta9"}).status_code == 200


# F-14 · salary figures are never kept as offline copies on the phone ----------------------------------
def test_salaries_are_never_saved_on_the_device():
    sw = (ROOT / "frontend" / "sw.js").read_text(encoding="utf-8")
    never_kept = next(line for line in sw.splitlines() if "attachments|reports|demo|time" in line)
    assert "salaries" in never_kept and "staff\\/my-salary" in never_kept


# F-15 · the session cookie carries no part of the password hash --------------------------------------
def test_session_fingerprint_is_not_part_of_the_password_hash(app, manager):
    from backend.models import User
    with app.app_context():
        user = User.query.filter_by(username="manager").one()
        assert user.session_stamp not in user.password_hash


# F-07 · optional 2-step login for managers and the director ------------------------------------------
def _code(secret, offset=0):
    from backend.services import twostep
    return twostep.code_at(secret, twostep.current_step() + offset)


def _turn_on(manager, password="ChangeMe123!"):
    assert manager.post("/api/auth/two-step/setup", json={"password": "wrong"}).status_code == 400
    setup = manager.post("/api/auth/two-step/setup", json={"password": password}).json
    assert setup["link"].startswith("otpauth://totp/") and setup["qr_svg"].lstrip().startswith("<?xml")
    assert manager.post("/api/auth/two-step/confirm", json={"code": "000000" if _code(setup["secret"]) != "000000" else "111111"}).status_code == 400
    assert manager.post("/api/auth/two-step/confirm", json={"code": _code(setup["secret"])}).json["enabled"] is True
    return setup["secret"]


def test_two_step_login_asks_for_the_app_code_after_the_password(app, manager):
    secret = _turn_on(manager)
    device = _device(app, "10.3.0.1")
    step_one = device.post("/api/auth/login", json={"username": "manager", "password": "ChangeMe123!"}).json
    assert step_one["two_step_required"] is True and "user" not in step_one
    assert device.get("/api/auth/me").status_code == 401                      # not logged in by the password alone
    for _ in range(2):
        assert device.post("/api/auth/login/code", json={"ticket": step_one["ticket"], "code": "123456"}).status_code == 401
    third = device.post("/api/auth/login/code", json={"ticket": step_one["ticket"], "code": "123456"}).json
    assert third["retry_after"] == 30                                          # wrong codes count like wrong passwords
    from backend.models import LoginFailure
    with app.app_context():
        LoginFailure.query.update({"locked_until": None})
        db.session.commit()
    ok = device.post("/api/auth/login/code", json={"ticket": step_one["ticket"], "code": _code(secret, 1)})
    assert ok.status_code == 200 and ok.json["user"]["two_step"] is True
    assert device.get("/api/auth/me").status_code == 200
    again = _device(app, "10.3.0.2")                                           # the same code never works twice
    ticket = again.post("/api/auth/login", json={"username": "manager", "password": "ChangeMe123!"}).json["ticket"]
    assert again.post("/api/auth/login/code", json={"ticket": ticket, "code": _code(secret, 1)}).status_code == 401


def test_two_step_ticket_cannot_be_forged_or_outlive_a_password_change(app, manager):
    secret = _turn_on(manager)
    device = _device(app, "10.4.0.1")
    ticket = device.post("/api/auth/login", json={"username": "manager", "password": "ChangeMe123!"}).json["ticket"]
    assert device.post("/api/auth/login/code", json={"ticket": ticket + "x", "code": _code(secret)}).status_code == 401
    manager.post("/api/auth/change-password", json={"current_password": "ChangeMe123!", "new_password": "Kituo4Mafuta9"})
    r = device.post("/api/auth/login/code", json={"ticket": ticket, "code": _code(secret)})
    assert r.status_code == 401 and "again" in r.json["error"]


def test_staff_cannot_use_two_step_and_turning_it_off_needs_password_and_code(app, manager):
    _new_staff(manager)
    asha = login(app, "asha01", "Temp1234")
    assert asha.post("/api/auth/two-step/setup", json={"password": "Temp1234"}).status_code == 403
    secret = _turn_on(manager)
    assert manager.post("/api/auth/two-step/off", json={"password": "ChangeMe123!", "code": "000001"}).status_code == 400
    assert manager.post("/api/auth/two-step/off", json={"password": "wrong", "code": _code(secret, 1)}).status_code == 400
    assert manager.post("/api/auth/two-step/off", json={"password": "ChangeMe123!", "code": _code(secret, 1)}).json["enabled"] is False
    plain = _device(app, "10.5.0.1").post("/api/auth/login", json={"username": "manager", "password": "ChangeMe123!"}).json
    assert "user" in plain and "two_step_required" not in plain


def test_director_resets_a_managers_two_step_after_a_lost_phone(app, manager):
    _turn_on(manager)
    director = login(app, "director", "Director123!")
    mid = manager.get("/api/auth/me").json["user"]["id"]
    listed = {m["id"]: m for m in director.get("/api/director/managers").json["managers"]}
    assert listed[mid]["two_step"] is True
    assert director.post(f"/api/director/managers/{mid}/reset-two-step").json["manager"]["two_step"] is False
    assert manager.post(f"/api/director/managers/{mid}/reset-two-step").status_code == 403    # managers cannot
