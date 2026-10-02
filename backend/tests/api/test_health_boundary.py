"""Regression tests for the health endpoint's database boundary (Phase 8).

`GET /api/health` used to probe the module-level engine in `app.db.session`,
which is built from whatever `DATABASE_URL` was set when that module was first
imported. Two consequences, both observed in practice:

- A test that passed its own `Settings` still probed the developer's real
  database, because the route never read them. The suite reported on the machine
  it ran on rather than on the app under test.
- If that real database was unreachable, health reported `degraded` for an app
  whose configured database was perfectly fine — a wrong answer, not a missing
  one.

The tests below pin the fix: the probe follows `app.state`, and the failure path
cannot leak a connection string.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.deps import get_database_dep
from app.core.config import Settings
from app.db.session import Database
from app.main import create_app
from app.services.capabilities import probe_database

#: A syntactically valid URL nothing will ever reach. Pointing the ambient
#: environment at it proves the health route reads the app's settings rather than
#: the import-time global.
UNREACHABLE_URL = "postgresql://nobody@127.0.0.1:1/none"


def sqlite_settings(path: Path) -> Settings:
    """Build keyless test settings pointed at an isolated SQLite file."""
    return Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite:///{path.as_posix()}",
        groq_api_key="",
        serpapi_key="",
        log_level="WARNING",
    )


@pytest.fixture
def client_with_foreign_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """An app configured for SQLite while the environment points at Postgres."""
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_URL)

    with TestClient(create_app(sqlite_settings(tmp_path / "boundary.db"))) as test_client:
        yield test_client


# -- the probe follows the app, not the environment ----------------------


def test_health_follows_the_apps_settings_not_the_environment(
    client_with_foreign_environment: TestClient,
) -> None:
    """The probe must describe the app under test, not the shell that started it."""
    body = client_with_foreign_environment.get("/api/health").json()

    assert body["database"]["dialect"] == "sqlite"
    assert body["database"]["connected"] is True


def test_health_is_ok_even_when_the_environment_database_is_unreachable(
    client_with_foreign_environment: TestClient,
) -> None:
    body = client_with_foreign_environment.get("/api/health").json()

    assert body["status"] == "ok"


def test_app_state_carries_a_database_built_from_its_settings(
    client_with_foreign_environment: TestClient,
) -> None:
    database = client_with_foreign_environment.app.state.database

    assert isinstance(database, Database)
    assert database.engine.dialect.name == "sqlite"


def test_the_database_dependency_returns_the_apps_own_instance(
    client_with_foreign_environment: TestClient,
) -> None:
    """The dependency hands back the app's database rather than building a second."""
    app = client_with_foreign_environment.app
    request = Request({"type": "http", "app": app, "headers": []})

    assert get_database_dep(request) is app.state.database


def test_the_database_is_disposed_on_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A leaked engine would hold a connection pool open for the process's life."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = create_app(sqlite_settings(tmp_path / "dispose.db"))

    with TestClient(app):
        pass

    assert app.state.database.engine.pool.checkedout() == 0


def test_a_failed_probe_still_reports_the_configured_dialect(
    client_with_foreign_environment: TestClient,
) -> None:
    """The dialect is a fact about settings, not a fact about connectivity.

    A client cannot tell an unconfigured database from a misconfigured one if the
    dialect vanishes alongside the connection.
    """
    app = client_with_foreign_environment.app
    database = app.state.database
    original_engine = database.engine
    original_factory = database.session_factory
    try:
        database.engine = _UnreachableEngine("postgresql")
        database.session_factory = _ExplodingSessionFactory()

        body = client_with_foreign_environment.get("/api/health").json()

        assert body["database"]["dialect"] == "postgresql"
        assert body["database"]["connected"] is False
        assert body["status"] == "degraded"
    finally:
        database.engine = original_engine
        database.session_factory = original_factory


# -- the failure path must not leak --------------------------------------


class _ExplodingSessionFactory:
    """A session factory whose error message contains a password.

    The message is the realistic threat: SQLAlchemy and most drivers embed the
    DSN in the exception text, and that DSN carries credentials.
    """

    def __call__(self) -> object:
        """Fail with a message shaped like a real driver's."""
        raise RuntimeError(
            "connection to postgresql://user:hunter2@db.internal:5432/app failed"
        )


def test_probe_database_never_returns_the_exception_message() -> None:
    connected, detail = probe_database(_ExplodingSessionFactory())

    assert connected is False
    assert detail == "RuntimeError"


def test_probe_database_never_returns_a_password() -> None:
    _, detail = probe_database(_ExplodingSessionFactory())

    assert "hunter2" not in detail
    assert "postgresql://" not in detail


def test_probe_database_reports_the_dialect_when_healthy(tmp_path: Path) -> None:
    database = Database(sqlite_settings(tmp_path / "probe.db"))

    try:
        assert probe_database(database.session_factory) == (True, "sqlite")
    finally:
        database.dispose()


class _UnreachableEngine:
    """An engine stand-in that names a dialect but cannot be connected to."""

    def __init__(self, dialect_name: str) -> None:
        self.dialect = type("_Dialect", (), {"name": dialect_name})()