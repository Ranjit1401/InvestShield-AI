"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import Database, get_db
from app.graph.context import GraphContext, build_default_context
from app.repositories.investigations import InvestigationRepository

__all__ = [
    "get_database_dep",
    "get_db",
    "get_graph_context_dep",
    "get_repository_dep",
    "get_settings_dep",
    "get_settings_cached",
]


@lru_cache(maxsize=1)
def get_settings_cached() -> Settings:
    """Return the process-wide cached settings."""
    return get_settings()


def get_settings_dep(request: Request) -> Settings:
    """FastAPI dependency exposing the settings the app was built with.

    Reads ``app.state.settings`` (set by the application factory) so tests and
    alternate deployments get the settings that actually belong to the running
    app, not the module-level global.

    Args:
        request: Incoming request, used to reach the app instance.

    Returns:
        The :class:`~app.core.config.Settings` for this application.
    """
    return getattr(request.app.state, "settings", None) or get_settings_cached()


def get_database_dep(request: Request) -> Database:
    """FastAPI dependency exposing the database bound to this app's settings.

    The application factory builds one :class:`~app.db.session.Database` from
    the settings it was given and stores it on ``app.state.database``. Reading it
    back here keeps route handlers off the import-time global engine in
    ``app.db.session``, which is resolved from whatever ``DATABASE_URL`` happened
    to be set when that module was first imported.

    Args:
        request: Incoming request, used to reach the app instance.

    Returns:
        The :class:`~app.db.session.Database` owned by this application.
    """
    database = getattr(request.app.state, "database", None)
    if database is None:  # pragma: no cover - defensive; factory always sets it
        database = Database(get_settings_dep(request))
        request.app.state.database = database
    return database


def get_graph_context_dep(request: Request) -> GraphContext:
    """FastAPI dependency exposing the graph context for this app.

    The application factory builds the production context once and parks it on
    ``app.state.graph_context``. Building it lazily here instead would mean every
    request constructed a fresh `ExtractionService`, `SearchService` and
    `RiskService` — six objects and a new connection pool per request.

    Tests override this dependency with one wired to fakes, which is what keeps
    the API suite offline: the route has no idea whether it is talking to the
    real pipeline.

    Args:
        request: Incoming request, used to reach the app instance.

    Returns:
        The :class:`~app.graph.context.GraphContext` for this application.
    """
    context = getattr(request.app.state, "graph_context", None)
    if context is None:  # pragma: no cover - defensive; factory always sets it
        context = build_default_context(get_settings_dep(request))
        request.app.state.graph_context = context
    return context


def get_repository_dep(
    database: Annotated[Database, Depends(get_database_dep)],
    session: Annotated[Session, Depends(get_db)],
) -> InvestigationRepository:
    """Return a repository bound to this request's session.

    The session comes from `get_db`, which is overridden in tests and in any
    deployment that needs to supply its own session. The `Database` is read from
    ``app.state`` purely so the dependency graph stays explicit about which
    application the request belongs to; the session it returns is what actually
    carries the connection.

    Taking both is deliberate rather than redundant. A route that took only the
    session would work, but a route that took only the `Database` would have to
    build a session itself and would then own committing — and a route that owns
    committing is how a half-written investigation becomes visible.

    Args:
        database: The `Database` this application was built with.
        session: The request-scoped session.

    Returns:
        A repository that writes and reads through `session`.
    """
    return InvestigationRepository(session)
