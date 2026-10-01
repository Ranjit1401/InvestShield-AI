"""Tests for database engine and session configuration."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text

from app.core.config import Settings
from app.db.base import Base
from app.db.session import Database, create_db_engine, resolve_database_url


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}")


def test_engine_uses_sqlite(tmp_path: Path) -> None:
    engine = create_db_engine(_settings(tmp_path))

    assert engine.dialect.name == "sqlite"
    engine.dispose()


def test_sqlite_connection_supports_cross_thread_use(tmp_path: Path) -> None:
    """FastAPI runs sync endpoints in a thread pool; SQLite must allow it."""
    import threading

    engine = create_db_engine(_settings(tmp_path))
    results: list[int] = []

    def worker() -> None:
        with engine.connect() as connection:
            results.append(connection.execute(text("SELECT 1")).scalar_one())

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=10)

    assert results == [1]
    engine.dispose()


def test_foreign_keys_pragma_is_enabled(tmp_path: Path) -> None:
    engine = create_db_engine(_settings(tmp_path))

    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1

    engine.dispose()


def test_create_all_builds_metadata(tmp_path: Path) -> None:
    """`create_all()` runs cleanly and materialises exactly the declared tables.

    The table set is empty until Phase 9 declares the ORM models; the assertion
    is written so it becomes meaningful automatically once they exist.
    """
    from app.db.base import Base

    database = Database(_settings(tmp_path))
    database.create_all()

    created = set(inspect(database.engine).get_table_names())

    assert created == set(Base.metadata.tables)
    database.dispose()


def test_create_all_is_idempotent(tmp_path: Path) -> None:
    database = Database(_settings(tmp_path))

    database.create_all()
    database.create_all()  # must not raise

    database.dispose()


def test_resolve_relative_sqlite_url_creates_parent_dir(tmp_path: Path) -> None:
    resolved = resolve_database_url("sqlite:///./nested/deep/db.sqlite", repo_root=tmp_path)

    assert (tmp_path / "nested" / "deep").is_dir()
    assert resolved.endswith("db.sqlite")


def test_session_factory_roundtrip(tmp_path: Path) -> None:
    database = Database(_settings(tmp_path))
    database.create_all()

    session = database.session_factory()
    try:
        assert session.get_bind().dialect.name == "sqlite"
        assert session.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        session.close()
        database.dispose()


def test_base_metadata_is_available() -> None:
    assert hasattr(Base, "metadata")
    assert Base.metadata is not None
