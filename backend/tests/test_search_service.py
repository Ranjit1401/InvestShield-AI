"""Tests for `SearchService` (Phase 3, §11–§16).

The service owns policy, so these tests are mostly about the rules a consumer
depends on: the query is normalized before it is sent, failures stay failures,
sources are classified, duplicates collapse by canonical URL, and result counts
are bounded.

No provider here performs any network I/O.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.schemas.search import (
    SEARCH_AUTH_ERROR,
    SEARCH_INVALID_QUERY,
    SEARCH_NOT_CONFIGURED,
    SEARCH_RATE_LIMITED,
    SEARCH_SERVICE_ERROR,
    SEARCH_TIMEOUT,
    SearchResponse,
    SearchResult,
    SearchStatus,
    SourceType,
)
from app.services.search import (
    HARD_MAX_RESULTS,
    NullSearchProvider,
    SearchProvider,
    SearchService,
    build_search_service,
)

SECRET = "serp-test-key-do-not-log"


def make_settings(**overrides: Any) -> Settings:
    """Build test settings with no search credentials by default."""
    defaults: dict[str, Any] = {
        "environment": "test",
        "database_url": "sqlite:///:memory:",
        "groq_api_key": "",
        "serpapi_key": "",
        "log_level": "WARNING",
    }
    defaults.update(overrides)
    return Settings(**defaults)


class FakeProvider(SearchProvider):
    """A configurable stand-in for a real search backend.

    Args:
        name: Provider name to report.
        available: Whether the provider claims to be runnable.
        results: Results to return when `response` is not given.
        response: A full response to return verbatim.
    """

    def __init__(
        self,
        name: str = "fake",
        available: bool = True,
        results: tuple[SearchResult, ...] = (),
        response: SearchResponse | None = None,
    ) -> None:
        self.name = name
        self._available = available
        self._results = results
        self._response = response
        self.calls: list[tuple[str, int]] = []

    def is_available(self) -> bool:
        return self._available

    def search(self, query: str, max_results: int) -> SearchResponse:
        self.calls.append((query, max_results))
        if self._response is not None:
            return self._response
        return SearchResponse(
            query=query,
            results=self._results[:max_results],
            provider=self.name,
            status=SearchStatus.OK,
            provider_query_sent=True,
        )


def result(url: str, title: str = "t", **overrides: Any) -> SearchResult:
    """Build a search result with sensible defaults."""
    defaults: dict[str, Any] = {"title": title, "url": url, "position": 1}
    defaults.update(overrides)
    return SearchResult(**defaults)


def service_with(provider: SearchProvider, **setting_overrides: Any) -> SearchService:
    return SearchService(
        settings=make_settings(**setting_overrides),
        provider=provider,
    )


class TestQueryHandling:
    """The query is normalized and validated before the provider sees it."""

    def test_query_is_normalized_before_search(self) -> None:
        provider = FakeProvider()
        service = service_with(provider)

        service.search("   SEBI   registration   XYZ   ")

        assert provider.calls == [("SEBI registration XYZ", 10)]

    def test_embedded_newlines_are_folded(self) -> None:
        provider = FakeProvider()
        service = service_with(provider)

        service.search("SEBI registration\nXYZ")

        assert provider.calls[0][0] == "SEBI registration XYZ"

    def test_identifiers_survive_intact(self) -> None:
        provider = FakeProvider()
        service = service_with(provider)

        service.search("acmefunds@okhdfcbank")

        assert provider.calls[0][0] == "acmefunds@okhdfcbank"

    @pytest.mark.parametrize("query", ["", "   ", None, "\n\n\t", "!!!"])
    def test_unsearchable_query_never_reaches_the_provider(self, query: str | None) -> None:
        provider = FakeProvider()
        service = service_with(provider)

        response = service.search(query)

        assert provider.calls == []
        assert response.status is SearchStatus.ERROR
        assert response.error_code == SEARCH_INVALID_QUERY
        assert response.results == ()
        assert response.provider_query_sent is False

    def test_response_echoes_the_normalized_query(self) -> None:
        response = service_with(FakeProvider()).search("  SEBI   xyz  ")

        assert response.query == "SEBI xyz"


class TestMissingApiKey:
    """No credentials must degrade explicitly, never crash or fabricate."""

    def test_missing_key_returns_unavailable(self) -> None:
        provider = FakeProvider(available=False, name="serpapi")
        service = service_with(provider)

        response = service.search("SEBI registration")

        assert response.status is SearchStatus.UNAVAILABLE
        assert response.success is False
        assert response.degraded is True
        assert response.error_code == SEARCH_NOT_CONFIGURED
        assert response.results == ()

    def test_missing_key_performs_no_provider_call(self) -> None:
        provider = FakeProvider(available=False)
        service = service_with(provider)

        service.search("SEBI registration")

        assert provider.calls == []

    def test_missing_key_states_the_limitation(self) -> None:
        provider = FakeProvider(available=False)
        service = service_with(provider)

        response = service.search("SEBI registration")

        assert response.warnings
        assert "not configured" in response.warnings[0]

    def test_missing_key_is_not_an_empty_success(self) -> None:
        provider = FakeProvider(available=False)
        service = service_with(provider)

        response = service.search("SEBI registration")

        assert response.status is not SearchStatus.OK
        assert response.error_code is not None

    def test_null_provider_names_the_configured_backend(self) -> None:
        service = build_search_service(make_settings(search_provider="bing"))

        response = service.search("SEBI registration")

        assert response.provider == "bing"
        assert response.status is SearchStatus.UNAVAILABLE

    def test_unknown_provider_name_degrades_rather_than_raising(self) -> None:
        service = build_search_service(make_settings(search_provider="not-a-provider"))

        response = service.search("SEBI registration")

        assert response.status is SearchStatus.UNAVAILABLE
        assert response.error_code == SEARCH_NOT_CONFIGURED

    def test_unavailable_response_cannot_claim_a_request_was_sent(self) -> None:
        provider = FakeProvider(available=False)
        service = service_with(provider)

        assert service.search("SEBI registration").provider_query_sent is False


class TestProviderFailurePassthrough:
    """A provider failure stays a failure."""

    @pytest.mark.parametrize(
        "code",
        [SEARCH_TIMEOUT, SEARCH_RATE_LIMITED, SEARCH_AUTH_ERROR, SEARCH_SERVICE_ERROR],
    )
    def test_error_codes_are_preserved(self, code: str) -> None:
        provider = FakeProvider(
            response=SearchResponse(
                query="q",
                provider="fake",
                status=SearchStatus.ERROR,
                error_code=code,
                provider_query_sent=True,
            )
        )
        service = service_with(provider)

        response = service.search("SEBI registration")

        assert response.status is SearchStatus.ERROR
        assert response.error_code == code
        assert response.success is False
        assert response.degraded is True

    def test_failure_is_never_dressed_up_as_zero_results(self) -> None:
        provider = FakeProvider(
            response=SearchResponse(
                query="q",
                provider="fake",
                status=SearchStatus.ERROR,
                error_code=SEARCH_TIMEOUT,
                provider_query_sent=True,
            )
        )

        response = service_with(provider).search("SEBI registration")

        assert response.results == ()
        assert response.status is not SearchStatus.OK

    def test_successful_empty_search_stays_a_success(self) -> None:
        response = service_with(FakeProvider(results=())).search("nothing matches this")

        assert response.status is SearchStatus.OK
        assert response.success is True
        assert response.results == ()
        assert response.error_code is None

    def test_successful_empty_search_is_not_degraded(self) -> None:
        response = service_with(FakeProvider(results=())).search("nothing matches this")

        assert response.degraded is False


class TestSourceClassification:
    """The service attaches publisher categories to results."""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.sebi.gov.in/a", SourceType.REGULATOR),
            ("https://mca.gov.in/a", SourceType.GOVERNMENT),
            ("https://nsearchives.nseindia.com/a", SourceType.EXCHANGE),
            ("https://fake-sebi-example.com/a", SourceType.GENERAL_WEB),
        ],
    )
    def test_results_are_classified(self, url: str, expected: SourceType) -> None:
        provider = FakeProvider(results=(result(url),))

        response = service_with(provider).search("query")

        assert response.results[0].source_type is expected

    def test_domain_is_attached(self) -> None:
        provider = FakeProvider(results=(result("https://www.sebi.gov.in/a"),))

        response = service_with(provider).search("query")

        assert response.results[0].source_domain == "sebi.gov.in"

    def test_canonical_url_is_attached(self) -> None:
        provider = FakeProvider(results=(result("https://www.sebi.gov.in/a/"),))

        response = service_with(provider).search("query")

        assert response.results[0].canonical_url == "https://sebi.gov.in/a"

    def test_results_carry_a_priority(self) -> None:
        provider = FakeProvider(results=(result("https://sebi.gov.in/a"),))

        response = service_with(provider).search("query")

        assert response.results[0].priority < 7


class TestDeduplication:
    """Identical canonical URLs collapse; different documents do not."""

    def test_trailing_slash_duplicate_collapses(self) -> None:
        provider = FakeProvider(
            results=(
                result("https://example.com/page", title="first", position=1),
                result("https://example.com/page/", title="duplicate", position=2),
            )
        )

        response = service_with(provider).search("query")

        assert len(response.results) == 1
        assert response.results[0].title == "first"
        assert response.results[0].position == 1

    def test_www_and_plain_host_collapse(self) -> None:
        provider = FakeProvider(
            results=(
                result("https://www.example.com/a", position=1),
                result("https://example.com/a", position=2),
            )
        )

        response = service_with(provider).search("query")

        assert len(response.results) == 1

    def test_fragment_duplicate_collapses(self) -> None:
        provider = FakeProvider(
            results=(
                result("https://example.com/a", position=1),
                result("https://example.com/a#section", position=2),
            )
        )

        response = service_with(provider).search("query")

        assert len(response.results) == 1

    def test_similar_titles_are_not_merged(self) -> None:
        # Merging on title similarity would silently discard sources.
        provider = FakeProvider(
            results=(
                result("https://example.com/a", title="SEBI Registration", position=1),
                result("https://other.com/b", title="SEBI Registration", position=2),
            )
        )

        response = service_with(provider).search("query")

        assert len(response.results) == 2

    def test_different_paths_are_not_merged(self) -> None:
        provider = FakeProvider(
            results=(
                result("https://example.com/a", position=1),
                result("https://example.com/b", position=2),
            )
        )

        response = service_with(provider).search("query")

        assert len(response.results) == 2

    def test_duplicate_removal_is_reported(self) -> None:
        provider = FakeProvider(
            results=(
                result("https://example.com/page", position=1),
                result("https://example.com/page/", position=2),
            )
        )

        response = service_with(provider).search("query")

        assert any("duplicate" in warning for warning in response.warnings)

    def test_no_duplicates_means_no_duplicate_warning(self) -> None:
        provider = FakeProvider(
            results=(
                result("https://example.com/a", position=1),
                result("https://example.com/b", position=2),
            )
        )

        response = service_with(provider).search("query")

        assert not any("duplicate" in warning for warning in response.warnings)


class TestResultLimits:
    """A caller cannot ask for an unbounded page."""

    def test_default_limit_is_applied(self) -> None:
        provider = FakeProvider()

        service_with(provider).search("query")

        assert provider.calls[0][1] == 10

    def test_requested_limit_is_respected(self) -> None:
        provider = FakeProvider()

        service_with(provider).search("query", max_results=3)

        assert provider.calls[0][1] == 3

    def test_limit_is_capped_by_the_hard_maximum(self) -> None:
        provider = FakeProvider()

        service_with(provider, search_max_results=10_000).search("query", max_results=9999)

        assert provider.calls[0][1] == HARD_MAX_RESULTS

    def test_configured_maximum_is_respected(self) -> None:
        provider = FakeProvider()

        service_with(provider, search_max_results=5).search("query", max_results=50)

        assert provider.calls[0][1] == 5

    def test_zero_and_negative_limits_become_one(self) -> None:
        provider = FakeProvider()

        service_with(provider).search("query", max_results=0)
        service_with(provider).search("query", max_results=-5)

        assert [limit for _, limit in provider.calls] == [1, 1]

    def test_unparseable_limit_falls_back_to_the_default(self) -> None:
        provider = FakeProvider()

        service_with(provider).search("query", max_results="not-a-number")  # type: ignore[arg-type]

        assert provider.calls[0][1] == 10

    def test_results_are_truncated_to_the_limit(self) -> None:
        provider = FakeProvider(
            results=tuple(result(f"https://example.com/{i}", position=i + 1) for i in range(20))
        )

        response = service_with(provider).search("query", max_results=4)

        assert len(response.results) == 4

    def test_truncation_is_reported(self) -> None:
        provider = FakeProvider(
            results=tuple(result(f"https://example.com/{i}", position=i + 1) for i in range(20))
        )

        response = service_with(provider).search("query", max_results=4)

        assert any("limited to" in warning for warning in response.warnings)

    def test_hard_maximum_is_a_sane_bound(self) -> None:
        assert 1 < HARD_MAX_RESULTS <= 100


class TestDeterminism:
    """Same input plus same provider output yields identical normalization."""

    def test_repeated_searches_are_identical(self) -> None:
        urls = (
            "https://www.sebi.gov.in/a",
            "https://example.com/b",
            "https://nsearchives.nseindia.com/c",
        )

        def run() -> list[dict[str, Any]]:
            provider = FakeProvider(
                results=tuple(result(url, title=f"t{i}", position=i + 1) for i, url in enumerate(urls))
            )
            response = service_with(provider).search("  SEBI   registration  ", max_results=5)
            return [
                {
                    key: value
                    for key, value in item.model_dump(mode="json").items()
                    if key != "retrieved_at"
                }
                for item in response.results
            ]

        assert run() == run()

    def test_classification_is_stable_across_calls(self) -> None:
        provider = FakeProvider(results=(result("https://sebi.gov.in/a"),))
        service = service_with(provider)

        types = {service.search("query").results[0].source_type for _ in range(20)}

        assert types == {SourceType.REGULATOR}


class TestNoVerificationConclusions:
    """Phase 3 retrieves information; it does not interpret it."""

    def test_response_makes_no_claim_about_any_verification_status(self) -> None:
        provider = FakeProvider(
            results=(
                result("https://sebi.gov.in/notice", title="Notice"),
                result("https://fake-sebi-example.com/claim", title="Claim"),
            )
        )

        response = service_with(provider).search("guaranteed 35% monthly returns")

        # A regulator result and a general-web result coexist with no verdict
        # about either. Ordering of authority is metadata, not a conclusion.
        assert len(response.results) == 2
        dumped = response.model_dump_json().upper()
        for verdict in ("VERIFIED", "CONTRADICTED", "SCAM", "FRAUD", "RISK_SCORE"):
            assert verdict not in dumped

    def test_no_risk_or_evidence_score_is_produced(self) -> None:
        provider = FakeProvider(results=(result("https://sebi.gov.in/a"),))

        dumped = service_with(provider).search("query").model_dump_json()

        for field in ("risk", "score", "evidence_strength", "supports", "contradicts"):
            assert field not in dumped


class TestFactoryAndIntrospection:
    """Construction and provider selection."""

    def test_build_service_uses_the_configured_provider(self) -> None:
        service = build_search_service(make_settings(serpapi_key=SECRET))

        assert service.provider_name == "serpapi"
        assert isinstance(service._provider, SearchProvider)  # noqa: SLF001

    def test_service_reports_availability(self) -> None:
        assert build_search_service(make_settings(serpapi_key=SECRET)).available is True
        assert build_search_service(make_settings(serpapi_key="")).available is False

    def test_null_provider_is_never_available(self) -> None:
        provider = NullSearchProvider()

        assert provider.is_available() is False
        assert provider.search("query", 5).status is SearchStatus.UNAVAILABLE

    def test_provider_override_is_respected(self) -> None:
        provider = FakeProvider(name="injected")
        service = SearchService(settings=make_settings(), provider=provider)

        assert service.provider_name == "injected"