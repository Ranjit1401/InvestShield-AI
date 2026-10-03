"""Database engine, session factory and FastAPI dependency.

The database URL comes from settings (D-001). A relative SQLite path is
anchored to the repository root so the same `DATABASE_URL` works whether the
process is started from `backend/` or the repository root.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

from fastapi import Request
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


#: Columns added after `create_all` could have first run, as
#: ``(table, column, portable DDL type)``.
#:
#: Appended to, never edited: an entry describes a column that a database
#: predating that phase does not have. Types are spelled the portable SQL way
#: rather than a dialect-specific one so the same statement runs on SQLite and
#: PostgreSQL.
_ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # Phase 12: provenance for a URL submission, `NULL` for every text run.
    ("investigations", "source_metadata", "JSON"),
    # Phase 13: provenance for a screenshot submission, `NULL` for every
    # other run. A column of its own rather than a share of
    # `source_metadata`, because the two provenance records are different
    # models with no common shape and a run carries at most one of them.
    ("investigations", "image_metadata", "JSON"),
    # Phase 14: provenance for a PDF submission, `NULL` for every
    # other run. A column of its own for the same reason as
    # `image_metadata`: the provenance records are different models
    # and a run carries at most one of them.
    ("investigations", "pdf_metadata", "JSON"),
)


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
        """Create every table declared on :class:`~app.db.base.Base`.

        Also applies the additive column DDL, because `create_all` creates
        missing *tables* and silently ignores missing *columns*. Without that
        second step a database created before Phase 12 would keep working for
        text investigations and fail only when a URL was stored — the kind of
        failure that looks like a code bug and is really a schema one.
        """
        from app.db.base import Base
        import app.models  # noqa: F401  (registers mappers on Base.metadata)

        Base.metadata.create_all(bind=self.engine)
        self.apply_additive_columns()

    def apply_additive_columns(self) -> None:
        """Add columns introduced after a database may already have existed.

        There is no migration framework in this project, so each later phase's
        new column is declared here and applied once. Three properties matter:

        - **Idempotent.** The column list is read from the live schema first, so
          running this on a fresh database, an up-to-date one, or twice in a row
          all do the same thing.
        - **Additive only.** Every statement is `ADD COLUMN`, which SQLite and
          PostgreSQL both accept without a default and without rewriting the
          table. Nothing here drops, renames or retypes a column, so there is no
          data loss available to get wrong.
        - **Nullable.** A column added this way must tolerate the rows already
          in the table, so each one is nullable and the application treats
          `NULL` as "this run predates the feature" rather than as a value.
        """
        from sqlalchemy import inspect, text

        inspector = inspect(self.engine)
        existing_tables = set(inspector.get_table_names())

        with self.engine.begin() as connection:
            for table, column, ddl_type in _ADDITIVE_COLUMNS:
                if table not in existing_tables:
                    # The table itself is missing; `create_all` will make it with
                    # the column already present.
                    continue
                present = {c["name"] for c in inspector.get_columns(table)}
                if column in present:
                    continue
                logger.info(
                    "Adding a column introduced by a later phase",
                    extra={"table": table, "column": column},
                )
                connection.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
                )

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


def get_db(request: Request) -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session.

    The session factory comes from ``app.state.database`` so the connection is
    the one this application was actually built with. Falling back to the
    module-level `SessionLocal` is a defensive path only: it is resolved from
    whatever `DATABASE_URL` was set when this module was first imported, which is
    exactly the global the `app.state` injection exists to avoid.

    Committing is the **route's** job, not this function's. A dependency that
    committed on the way out would make it impossible for a handler to abandon a
    failed write, and a half-written investigation that reports `COMPLETED` is
    worse than one that was never written.

    Args:
        request: Incoming request, used to reach the app instance.

    Yields:
        An open :class:`~sqlalchemy.orm.Session`, closed on teardown.
    """
    database = getattr(request.app.state, "database", None)
    if database is None:  # pragma: no cover - defensive; factory always sets it
        database = Database(get_settings())
    session = database.session_factory()
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
