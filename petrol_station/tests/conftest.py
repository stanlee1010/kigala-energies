import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import create_app  # noqa: E402
from backend.config import TestConfig  # noqa: E402
from backend.extensions import db  # noqa: E402


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def login(app, username, password):
    client = app.test_client()
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.json
    return client


@pytest.fixture()
def manager(app):
    return login(app, "manager", "ChangeMe123!")
