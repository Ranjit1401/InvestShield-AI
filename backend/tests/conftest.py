"""Shared pytest fixtures for the InvestShield AI backend."""

from __future__ import annotations

import dataclasses
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

# Allow `pytest` to run from the repository root as well as from `backend/`.
for candidate in (BACKEND_DIR, REPO_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from tests.network_guard import (  # noqa: E402
    NetworkAccessBlocked,
    is_integration,
    no_network,
)

from app.core.config import Settings  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.graph.context import GraphContext  # noqa: E402
from app.main import create_app  # noqa: E402
from app.repositories.investigations import InvestigationRepository  # noqa: E402
from tests.graph.graph_factories import (  # noqa: E402
    RecordingExtractionService,
    RecordingRedFlagEngine,
    build_claim,
    build_entity,
    build_flag,
    real_dependencies,
)
from tests.persistence_factories import (  # noqa: E402
    REGULATORY_CONTENT,
)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the repository root."""
    return REPO_ROOT


@pytest.fixture
def test_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings pointed at an isolated SQLite file inside pytest's tmp dir.

    API keys are explicitly cleared so tests exercise the degraded (no-key)
    path and can never make a real network call.
    """
    for key in ("GROQ_API_KEY", "SERPAPI_KEY"):
        monkeypatch.delenv(key, raising=False)

    db_path = (tmp_path / "investshield_test.db").as_posix()

    return Settings(
        environment="test",
        database_url=f"sqlite:///{db_path}",
        groq_api_key="",
        serpapi_key="",
        log_level="WARNING",
    )


@pytest.fixture
def client(test_settings: Settings) -> TestClient:
    """A ``TestClient`` bound to an app built with isolated test settings."""
    app = create_app(test_settings)
    with TestClient(app) as test_client:
        yield test_client


# -- persistence fixtures (Phase 9) ---------------------------------------
#
# Every database here is a **temporary SQLite file**, not ``:memory:``. An
# in-memory database lives inside one connection, so a second session — which is
# exactly what a request-scoped dependency hands out — would find an empty schema,
# and a round-trip test would pass for the wrong reason. A file also matches what
# the application does, where the engine pools connections.


@pytest.fixture
def db_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings pointing at a temporary SQLite file, with no credentials.

    Args:
        tmp_path: Pytest-provided temporary directory.
        monkeypatch: Pytest monkeypatch, used to clear ambient configuration.

    Returns:
        Settings that cannot reach the network and cannot touch a real database.
    """
    for key in ("GROQ_API_KEY", "SERPAPI_KEY", "DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    return Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite:///{(tmp_path / 'persistence.db').as_posix()}",
        groq_api_key="",
        serpapi_key="",
        log_level="WARNING",
    )


@pytest.fixture
def database(db_settings: Settings) -> Iterator[Database]:
    """A ``Database`` with the schema created, disposed after the test.

    Args:
        db_settings: Isolated settings for this test.

    Yields:
        A ``Database`` whose schema matches ``Base.metadata``.
    """
    database = Database(db_settings)
    database.create_all()
    try:
        yield database
    finally:
        database.dispose()


@pytest.fixture
def session(database: Database) -> Iterator[Session]:
    """A session against the temporary database.

    Args:
        database: The temporary database.

    Yields:
        An open session, closed after the test.
    """
    session = database.session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def repo(session: Session) -> InvestigationRepository:
    """A repository bound to the temporary database.

    Args:
        session: The session under test.

    Returns:
        The repository.
    """
    return InvestigationRepository(session)


@pytest.fixture
def real_context(db_settings: Settings) -> GraphContext:
    """A context running the genuine Phase 1-6 services, fully offline.

    Extraction falls back to its deterministic patterns (no LLM key), search is
    served by a fixture provider, and verification and risk run for real. This is
    the context the round-trip tests use, so what is asserted to survive storage
    is what the real pipeline produces.

    Args:
        db_settings: Isolated settings for this test.

    Returns:
        A ``GraphContext`` whose runs cannot reach the network.
    """
    dependencies, _ = real_dependencies(db_settings)
    return GraphContext(dependencies=dependencies)


@pytest.fixture
def rich_context(db_settings: Settings) -> GraphContext:
    """An offline context producing entities, links, evidence and a real score.

    Two gaps in the default context make it inadequate on its own. Its
    deterministic extraction emits **no entities**, so ``entities`` and
    ``claim_entity_links`` would be tested only against empty tables. And its
    claims are guarantees, which Phase 4 correctly reports as
    ``NOT_A_FACTUAL_CLAIM``, leaving the risk engine nothing to score.

    This context replaces extraction and red-flag detection only. **Verification
    and evidence stay real**, which is what populates the search recorder and so
    produces actual evidence items; faking verification would leave the recorder
    empty and the two largest tables in the schema untested.
    """
    from app.schemas.extraction import ClaimEntityLink, ExtractionMode, ExtractionResult

    dependencies, _ = real_dependencies(db_settings)
    claim = build_claim()
    entity = build_entity()

    return GraphContext(
        dependencies=dataclasses.replace(
            dependencies,
            extraction_service=RecordingExtractionService(
                result=ExtractionResult(
                    claims=(claim,),
                    entities=(entity,),
                    relationships=(ClaimEntityLink(claim_id=claim.id, entity_id=entity.id),),
                    extraction_mode=ExtractionMode.LLM,
                    prompt_version="extraction-v1",
                    model="test-model",
                    source_text=REGULATORY_CONTENT,
                    normalized_text=REGULATORY_CONTENT,
                )
            ),
            red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
        )
    )


__all__ = [
    "NetworkAccessBlocked",
    "no_network",
]


@pytest.fixture(autouse=True)
def _offline_by_default(request: pytest.FixtureRequest) -> Iterator[None]:
    """Block outbound network access around every test that did not opt out.

    **This fixture has to live in `conftest.py`, and that is not a style choice.**
    An `autouse` fixture is only collected from a file pytest collects as a plugin
    or a conftest. `tests/network_guard.py` holds the implementation, but importing
    it — which is all that was being done — does not register its fixtures. The
    guard therefore existed, was importable, and was never installed once. The
    suite was green either way, which is exactly why nobody noticed:
    `tests/test_network_guard.py` asserts the guard is present from inside a normal
    test, and that assertion is what caught it.

    An integration-marked test is deliberately allowed to reach the network, so the
    guard is skipped for it rather than left to fail. Otherwise the marker would
    mean "run and immediately fail", which is not opt-in at all.

    Args:
        request: The pytest request, used to read the test's markers.

    Yields:
        `None` for the duration of the test.
    """
    if is_integration(request.node):
        yield
        return

    with no_network():
        yield
