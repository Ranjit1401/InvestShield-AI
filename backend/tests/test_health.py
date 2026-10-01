"""Tests for the health endpoint."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

#: Distinctive values planted in settings to prove they never reach a response.
SENTINEL_GROQ_KEY = "gsk-SENTINEL-must-never-be-echoed-000"
SENTINEL_SEARCH_KEY = "SERPAPI-SENTINEL-must-never-be-echoed-111"


@pytest.fixture
def credentialed_client(tmp_path: Path) -> TestClient:
    """App whose settings contain sentinel credentials."""
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite:///{(tmp_path / 'sentinel.db').as_posix()}",
        groq_api_key=SENTINEL_GROQ_KEY,
        serpapi_key=SENTINEL_SEARCH_KEY,
        log_level="WARNING",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "investshield-ai"
    assert body["version"]


def test_health_reports_database_state(client: TestClient) -> None:
    body = client.get("/api/health").json()

    assert body["database"]["connected"] is True
    assert body["database"]["dialect"] == "sqlite"


def test_health_reports_every_optional_service(client: TestClient) -> None:
    body = client.get("/api/health").json()

    assert set(body["services"]) == {"llm", "search", "ocr", "pdf", "embeddings"}
    for service in body["services"].values():
        assert isinstance(service["configured"], bool)
        assert isinstance(service["available"], bool)


def test_health_without_keys_is_still_healthy(client: TestClient) -> None:
    """Degradation is not failure (D-009)."""
    body = client.get("/api/health").json()

    assert body["status"] == "ok"
    assert body["services"]["llm"]["configured"] is False
    assert body["services"]["llm"]["detail"]


def test_health_marks_configured_providers(credentialed_client: TestClient) -> None:
    body = credentialed_client.get("/api/health").json()

    assert body["services"]["llm"]["configured"] is True
    assert body["services"]["search"]["configured"] is True


def test_health_never_echoes_credential_values(credentialed_client: TestClient) -> None:
    raw = credentialed_client.get("/api/health").text

    assert SENTINEL_GROQ_KEY not in raw
    assert SENTINEL_SEARCH_KEY not in raw


def test_health_response_has_no_secret_valued_field(credentialed_client: TestClient) -> None:
    body = credentialed_client.get("/api/health").json()

    assert "api_key" not in body
    assert "groq_api_key" not in body
    assert "serpapi_key" not in body


def test_root_points_at_health_and_docs(client: TestClient) -> None:
    body = client.get("/").json()

    assert body["health"] == "/api/health"
    assert body["docs"] == "/docs"


def test_openapi_schema_is_generated(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert "/api/health" in schema["paths"]
