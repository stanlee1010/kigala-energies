import pytest

from backend.config import _database_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgres://db.example/station", "postgresql+psycopg2://db.example/station"),
        ("postgresql://db.example/station", "postgresql+psycopg2://db.example/station"),
        ("postgresql+psycopg2://db.example/station", "postgresql+psycopg2://db.example/station"),
        ("mysql+pymysql://db.example/station", "mysql+pymysql://db.example/station"),
    ],
)
def test_database_url_uses_installed_postgres_driver(monkeypatch, url, expected):
    monkeypatch.setenv("DATABASE_URL", url)

    assert _database_url() == expected


def test_checks_run_during_requests_when_there_is_no_background_thread():
    from backend.app import create_app
    from backend.config import TestConfig
    from backend.models import Shift

    class Serverless(TestConfig):
        RUN_CHECKS_ON_REQUESTS = True
        SCHEDULER_INTERVAL_SECONDS = 300

    app = create_app(Serverless)
    with app.app_context():
        assert Shift.query.count() == 0
    app.test_client().get("/api/auth/me")                      # any API request (even logged out)
    with app.app_context():
        first = Shift.query.count()
    assert first > 0                                            # upcoming shifts were generated
    app.test_client().get("/api/auth/me")
    with app.app_context():
        assert Shift.query.count() == first                     # not again within the interval


def test_training_link_is_hidden_on_the_internet_host():
    from backend.app import create_app
    from backend.config import TestConfig
    app = create_app(TestConfig)
    app.config["VERCEL"] = True
    assert app.test_client().get("/api/demo/info").json == {"demo": False, "training_available": False}
