"""Shared pytest fixtures for the InvestShield AI backend."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

# Allow `pytest` to run from the repository root as well as from `backend/`.
for candidate in (BACKEND_DIR, REPO_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


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
