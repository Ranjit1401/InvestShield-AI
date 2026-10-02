"""Shared fixtures for the investigation API tests (Phase 8).

Every client built here runs the **real** investigation graph against services
that cannot reach the network. Two things make that true:

- `offline_settings()` clears both API keys and sets `_env_file=None`, so a
  developer's local `.env` cannot quietly supply a real `SERPAPI_KEY` and turn a
  unit test into a live network call that happens to pass.
- `get_graph_context_dep` is overridden with a context built from
  `real_dependencies`, which wires the genuine Phase 1-6 services with a fixture
  search provider. The route has no idea it is being handed a test context,
  which is the point: the assertions are about what the API does with a run, not
  about a hand-written stub.

A second fixture family — `api_client_with` — lets a test install a context
holding deliberately broken services, so the error-mapping tests exercise the
real exception path rather than asserting against a mocked response.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent

for _candidate in (BACKEND_DIR, REPO_ROOT):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from app.api.deps import get_graph_context_dep  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.graph.context import GraphContext  # noqa: E402
from app.main import create_app  # noqa: E402
from tests.graph.graph_factories import (  # noqa: E402
    offline_settings,
    real_dependencies,
)


@pytest.fixture
def api_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings with no credentials and an isolated SQLite file.

    Args:
        tmp_path: Pytest-provided temporary directory.
        monkeypatch: Pytest monkeypatch fixture, used to clear ambient keys.

    Returns:
        Settings that cannot authenticate to any external provider.
    """
    for key in ("GROQ_API_KEY", "SERPAPI_KEY", "DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)

    return Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite:///{(tmp_path / 'api_test.db').as_posix()}",
        groq_api_key="",
        serpapi_key="",
        log_level="WARNING",
    )


@pytest.fixture
def offline_graph_context() -> GraphContext:
    """A context wired to the real Phase 1-6 services with a fake search provider."""
    dependencies, _ = real_dependencies(offline_settings())
    return GraphContext(dependencies=dependencies)


@pytest.fixture
def api_client(
    api_settings: Settings, offline_graph_context: GraphContext
) -> Iterator[TestClient]:
    """A client whose graph context is offline but otherwise fully real."""
    app = create_app(api_settings)
    app.dependency_overrides[get_graph_context_dep] = lambda: offline_graph_context
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def api_client_factory(api_settings: Settings) -> Callable[[GraphContext], TestClient]:
    """Return a builder for a client running a specific graph context.

    Args:
        api_settings: Isolated settings to build the app with.

    Returns:
        A callable taking a `GraphContext` and yielding a `TestClient`.
    """

    def build(context: GraphContext) -> TestClient:
        app = create_app(api_settings)
        app.dependency_overrides[get_graph_context_dep] = lambda: context
        return TestClient(app)

    return build


__all__ = ["api_client", "api_client_factory", "api_settings", "offline_graph_context"]