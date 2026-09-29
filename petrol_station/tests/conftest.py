import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import create_app  # noqa: E402
from backend.config import TestConfig  # noqa: E402
from backend.extensions import db  # noqa: E402
from backend.services import ratelimit  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    ratelimit.reset()
    yield
    ratelimit.reset()


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


def login(app, username, password):
    client = browser_client(app)
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.json
    return client


@pytest.fixture()
def manager(app):
    return login(app, "manager", "ChangeMe123!")
