"""Database engine, session factory and FastAPI dependency.

The database URL comes from settings (D-001). A relative SQLite path is
anchored to the repository root so the same `DATABASE_URL` works whether the
process is started from `backend/` or the repository root.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import REPO_ROOT, Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_SQLITE_PREFIX = "sqlite:///"


def resolve_database_url(database_url: str, repo_root: Path | None = None) -> str:
    """Return an absolute SQLite URL, or the URL unchanged when not SQLite.

    Args:
        database_url: Raw URL from settings, e.g. ``sqlite:///./data/db.sqlite``.
        repo_root: Base directory used to anchor relative SQLite paths.

    Returns:
        A URL safe to hand to ``create_engine`` regardless of the cwd.
    """
    if not database_url.startswith(_SQLITE_PREFIX):
        return database_url

    raw_path = database_url[len(_SQLITE_PREFIX) :]
    if not raw_path or raw_path == ":memory:" or Path(raw_path).is_absolute():
        return database_url

    root = repo_root or REPO_ROOT
    absolute = (root / raw_path).resolve()
    absolute.parent.mkdir(parents=True, exist_ok=True)
    return f"{_SQLITE_PREFIX}{absolute.as_posix()}"


def create_db_engine(settings: Settings | None = None) -> Engine:
    """Create the SQLAlchemy engine for the configured database.

    Args:
        settings: Optional settings override (used by tests).

    Returns:
        A configured SQLAlchemy :class:`Engine`.
    """
    resolved = settings or get_settings()
    url = resolve_database_url(resolved.database_url)
    is_sqlite = url.startswith(_SQLITE_PREFIX)

    kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
    if is_sqlite:
        # FastAPI serves requests from a thread pool, so the connection must be
        # usable across threads. Foreign keys are off by default in SQLite.
        kwargs["connect_args"] = {"check_same_thread": False}

    engine = create_engine(url, **kwargs)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    logger.info("Database engine created", extra={"dialect": engine.dialect.name, "sqlite": is_sqlite})
    return engine


class Database:
    """Holds the engine and session factory for the running application."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.engine: Engine = create_db_engine(self.settings)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )

    def create_all(self) -> None:
        """Create every table declared on :class:`~app.db.base.Base`."""
        from app.db.base import Base
        import app.models  # noqa: F401  (registers mappers on Base.metadata)

        Base.metadata.create_all(bind=self.engine)

    def dispose(self) -> None:
        """Release all pooled connections."""
        self.engine.dispose()


_settings = get_settings()
engine = create_db_engine(_settings)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session.

    Yields:
        An open :class:`~sqlalchemy.orm.Session`, closed on teardown.
    """
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = [
    "Database",
    "SessionLocal",
    "create_db_engine",
    "engine",
    "get_db",
    "resolve_database_url",
]
