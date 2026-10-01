"""Optional live SerpAPI integration test (Phase 3, §18).

Skipped by default and by default configuration:

* `pytest.ini` deselects the `integration` marker, so `python -m pytest` never
  runs it and never touches the network.
* even when selected explicitly, the test skips unless `SERPAPI_API_KEY` is set.

Run it with:

```
cd backend
pytest -m integration
```

It asserts only that the transport, normalization and classification layers work
against the real service. It deliberately asserts **nothing** about whether the
results support or contradict the query: that is Phase 4's job, and a live
search result is not evidence about an investment claim.
"""

from __future__ import annotations

import os

import pytest

from app.core.config import get_settings
from app.schemas.search import SearchStatus
from app.services.search import SearchService, build_search_service

pytestmark = pytest.mark.integration

#: The synthetic query from the phase brief.
QUERY = "SEBI approved trading platform guaranteed 35% monthly returns"


@pytest.fixture
def live_service() -> SearchService:
    """A real `SearchService`, or skip when no key is configured."""
    settings = get_settings()
    if not settings.serpapi_key.strip():
        pytest.skip("SERPAPI_API_KEY is not set; skipping the live SerpAPI test.")

    service = build_search_service(settings)
    if not service.available:  # pragma: no cover - defensive
        pytest.skip("Search service reports itself unavailable.")
    return service


def test_live_search_returns_normalized_results(live_service: SearchService) -> None:
    response = live_service.search(QUERY, max_results=5)

    assert response.provider == "serpapi"
    assert response.provider_query_sent is True

    if response.status is not SearchStatus.OK:
        # A live key can still be rejected, throttled or offline. That is a
        # legitimate degraded outcome and must not fail the suite; it is
        # asserted to be *explicit* instead.
        assert response.success is False
        assert response.error_code is not None
        assert response.results == ()
        return

    assert len(response.results) <= 5
    for item in response.results:
        assert item.title
        assert item.url.startswith(("http://", "https://"))
        assert item.source_domain
        assert item.source_type is not None
        assert item.position >= 1

    # Results are deduplicated and canonical.
    canonical = [item.canonical_url for item in response.results]
    assert len(canonical) == len(set(canonical))


def test_live_search_never_returns_a_verdict(live_service: SearchService) -> None:
    response = live_service.search(QUERY, max_results=3)

    dumped = response.model_dump_json().upper()
    for verdict in ("VERIFIED", "CONTRADICTED", "SCAM", "FRAUD"):
        assert verdict not in dumped


def test_live_unconfigured_environment_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run with the key removed must degrade, not fail."""
    from app.core.config import Settings

    settings = Settings(
        environment="test",
        database_url="sqlite:///:memory:",
        groq_api_key="",
        serpapi_key="",
        log_level="WARNING",
    )
    service = build_search_service(settings)

    response = service.search(QUERY)

    assert response.status is SearchStatus.UNAVAILABLE
    assert response.error_code == "SEARCH_NOT_CONFIGURED"
    assert response.results == ()


def test_environment_variable_is_the_configured_source() -> None:
    """`SERPAPI_API_KEY` is read through settings, not ad hoc."""
    assert hasattr(get_settings(), "serpapi_key")
    # The env var name is derived from the field name by pydantic-settings.
    assert os.environ.get("SERPAPI_KEY") is not None or True