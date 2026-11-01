"""The manager chooses which warnings are urgent (Settings)."""
from backend.extensions import db
from backend.services import alerts

from .conftest import login


def _raise(app, kind, key="test", severity="WARNING"):
    with app.app_context():
        alerts.raise_alert(1, kind, f"{kind} test", "message", severity=severity, dedupe_key=f"{kind}:{key}")
        db.session.commit()


def _open(manager):
    return {a["alert_type"]: a["severity"] for a in manager.get("/api/alerts?resolved=0").json["alerts"]}


def test_usual_urgency_until_the_manager_chooses(app, manager):
    kinds = {k["type"]: k for k in manager.get("/api/alerts/urgency").json["kinds"]}
    assert kinds["LOW_STOCK"]["urgent"] is True and kinds["METER_ERROR"]["urgent"] is False
    assert kinds["SECURITY"]["always"] is True and kinds["LOW_STOCK"]["always"] is False
    _raise(app, "LOW_STOCK"); _raise(app, "METER_ERROR")
    assert _open(manager) == {"LOW_STOCK": "CRITICAL", "METER_ERROR": "WARNING"}


def test_choices_apply_to_new_and_already_open_warnings(app, manager):
    _raise(app, "LOW_STOCK", "old")
    r = manager.put("/api/alerts/urgency", json={"urgent": {"LOW_STOCK": False, "METER_ERROR": True}})
    assert r.status_code == 200
    kinds = {k["type"]: k["urgent"] for k in r.json["kinds"]}
    assert kinds["LOW_STOCK"] is False and kinds["METER_ERROR"] is True
    assert _open(manager)["LOW_STOCK"] == "WARNING"                     # the open one changed too
    _raise(app, "METER_ERROR", "new", severity="WARNING")
    assert _open(manager)["METER_ERROR"] == "CRITICAL"                  # new ones follow the choice
    first = manager.get("/api/alerts?resolved=0").json["alerts"][0]
    assert first["severity"] == "CRITICAL"                               # urgent ones are listed first
    log = [e["action"] for e in manager.get("/api/audit").json["logs"]]
    assert "WARNING URGENCY CHANGED" in log


def test_security_and_price_review_are_always_urgent(app, manager):
    manager.put("/api/alerts/urgency", json={"urgent": {"SECURITY": False, "FUEL_PRICE_UPDATE_REQUIRED": False}})
    kinds = {k["type"]: k["urgent"] for k in manager.get("/api/alerts/urgency").json["kinds"]}
    assert kinds["SECURITY"] is True and kinds["FUEL_PRICE_UPDATE_REQUIRED"] is True
    _raise(app, "SECURITY")
    assert _open(manager)["SECURITY"] == "CRITICAL"


def test_bad_choices_and_staff_are_refused(app, manager):
    assert manager.put("/api/alerts/urgency", json={"urgent": {"MADE_UP": True}}).status_code == 400
    assert manager.put("/api/alerts/urgency", json={"urgent": ["LOW_STOCK"]}).status_code == 400
    assert manager.put("/api/settings", json={"settings": {"urgent_warnings": "{}"}}).status_code in (200, 400)
    manager.post("/api/users", json={"full_name": "Asha", "username": "asha01", "password": "Temp@1234"})
    asha = login(app, "asha01", "Temp@1234")
    assert asha.get("/api/alerts/urgency").status_code == 403
    assert asha.put("/api/alerts/urgency", json={"urgent": {"LOW_STOCK": False}}).status_code == 403
