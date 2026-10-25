import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import create_app  # noqa: E402
from backend.config import TestConfig  # noqa: E402
from backend.extensions import db  # noqa: E402
from backend.services import guard, ratelimit  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    ratelimit.reset()
    guard.reset()
    yield
    ratelimit.reset()
    guard.reset()


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def browser_client(app):
    """A test client that behaves like this site's own pages (sends the anti-CSRF header)."""
    client = app.test_client()
    client.environ_base["HTTP_X_REQUESTED_WITH"] = "fetch"
    return client


def login(app, username, password, first_login=False):
    """Log in. A temporary password only allows changing it (see test_security); most tests are not
    about that, so the first-login password change is marked done for them unless first_login=True.
    client.first_login_user is the user as the server returned it at login."""
    client = browser_client(app)
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.json
    client.first_login_user = r.json["user"]
    if r.json["user"]["must_change_password"] and not first_login:
        from backend.models import User
        with app.app_context():
            user = db.session.get(User, r.json["user"]["id"])
            user.must_change_password = False
            db.session.commit()
    return client


@pytest.fixture()
def manager(app):
    return login(app, "manager", "ChangeMe123!")
