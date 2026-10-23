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
