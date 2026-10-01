"""Tests for the search schemas (Phase 3).

These pin the contract later phases depend on most: a search failure must never
look like an empty successful search, and no schema field may be misread as a
verdict about an investment claim.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.search import (
    SEARCH_ERROR_CODES,
    SEARCH_NOT_CONFIGURED,
    SEARCH_SERVICE_ERROR,
    SOURCE_PRIORITY,
    SearchResponse,
    SearchResult,
    SearchStatus,
    SourceType,
)


class TestSearchResult:
    """A normalized document: verbatim URL, derived identity, bounded position."""

    def test_minimal_result_is_valid(self) -> None:
        result = SearchResult(title="SEBI notice", url="https://www.sebi.gov.in/x")

        assert result.title == "SEBI notice"
        assert result.source_domain == "sebi.gov.in"
        assert result.source_type is SourceType.UNKNOWN
        assert result.canonical_url == "https://sebi.gov.in/x"

    def test_optional_fields_default_safely(self) -> None:
        result = SearchResult(title="t", url="https://example.com/a")

        assert result.snippet is None
        assert result.position == 1
        assert isinstance(result.retrieved_at, datetime)

    def test_source_type_must_be_a_known_member(self) -> None:
        with pytest.raises(ValidationError):
            SearchResult(
                title="t",
                url="https://example.com/a",
                source_type="DEFINITELY_AUTHORITATIVE",
            )

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "ftp://example.com/x",
            "data:text/html,<h1>x",
        ],
    )
    def test_non_http_schemes_are_rejected(self, url: str) -> None:
        # A hostile search result must not be able to smuggle an unusable
        # scheme into a later fetch stage.
        with pytest.raises(ValidationError):
            SearchResult(title="t", url=url)

    def test_url_without_host_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SearchResult(title="t", url="https:///path-only")

    def test_empty_title_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SearchResult(title="   ", url="https://example.com/a")

    def test_position_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            SearchResult(title="t", url="https://example.com/a", position=0)

    def test_result_is_frozen(self) -> None:
        result = SearchResult(title="t", url="https://example.com/a")

        with pytest.raises(ValidationError):
            result.title = "tampered"

    def test_priority_follows_source_type(self) -> None:
        general = SearchResult(
            title="t", url="https://example.com/a", source_type=SourceType.GENERAL_WEB
        )
        regulator = SearchResult(
            title="t", url="https://sebi.gov.in/a", source_type=SourceType.REGULATOR
        )

        assert regulator.priority < general.priority


class TestSourceType:
    """Every publisher category must be present and ranked."""

    def test_expected_members_exist(self) -> None:
        assert {member.value for member in SourceType} == {
            "REGULATOR",
            "GOVERNMENT",
            "EXCHANGE",
            "OFFICIAL_ENTITY",
            "TRUSTED_SECONDARY",
            "GENERAL_WEB",
            "UNKNOWN",
        }

    def test_priority_covers_every_type(self) -> None:
        assert set(SOURCE_PRIORITY) == set(SourceType)

    def test_priority_ordering_matches_the_documented_scale(self) -> None:
        order = [
            SourceType.REGULATOR,
            SourceType.GOVERNMENT,
            SourceType.EXCHANGE,
            SourceType.OFFICIAL_ENTITY,
            SourceType.TRUSTED_SECONDARY,
            SourceType.GENERAL_WEB,
            SourceType.UNKNOWN,
        ]
        ranks = [SOURCE_PRIORITY[member] for member in order]

        assert ranks == sorted(ranks)
        assert len(set(ranks)) == len(ranks)


class TestSearchResponse:
    """The three states must stay distinguishable, or Phase 4 lies."""

    def test_successful_empty_search_is_ok(self) -> None:
        response = SearchResponse(query="q", provider="serpapi", status=SearchStatus.OK)

        assert response.success is True
        assert response.degraded is False
        assert response.results == ()
        assert response.error_code is None

    def test_successful_search_carries_results(self) -> None:
        result = SearchResult(title="t", url="https://sebi.gov.in/a")
        response = SearchResponse(
            query="q",
            results=(result,),
            provider="serpapi",
            status=SearchStatus.OK,
        )

        assert len(response.results) == 1
        assert response.results[0].source_domain == "sebi.gov.in"

    def test_unavailable_is_not_success(self) -> None:
        response = SearchResponse(
            query="q",
            provider="serpapi",
            status=SearchStatus.UNAVAILABLE,
            error_code=SEARCH_NOT_CONFIGURED,
        )

        assert response.success is False
        assert response.degraded is True
        assert response.provider_query_sent is False

    def test_error_is_not_success(self) -> None:
        response = SearchResponse(
            query="q",
            provider="serpapi",
            status=SearchStatus.ERROR,
            error_code=SEARCH_SERVICE_ERROR,
        )

        assert response.success is False
        assert response.degraded is True

    def test_failure_must_carry_an_error_code(self) -> None:
        with pytest.raises(ValidationError):
            SearchResponse(query="q", provider="serpapi", status=SearchStatus.ERROR)

    def test_success_must_not_carry_an_error_code(self) -> None:
        with pytest.raises(ValidationError):
            SearchResponse(
                query="q",
                provider="serpapi",
                status=SearchStatus.OK,
                error_code=SEARCH_SERVICE_ERROR,
            )

    def test_unknown_error_code_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SearchResponse(
                query="q",
                provider="serpapi",
                status=SearchStatus.ERROR,
                error_code="SOMETHING_INVENTED",
            )

    def test_unavailable_cannot_claim_a_request_was_sent(self) -> None:
        with pytest.raises(ValidationError):
            SearchResponse(
                query="q",
                provider="serpapi",
                status=SearchStatus.UNAVAILABLE,
                error_code=SEARCH_NOT_CONFIGURED,
                provider_query_sent=True,
            )

    def test_response_is_frozen(self) -> None:
        response = SearchResponse(query="q", provider="serpapi")

        with pytest.raises(ValidationError):
            response.query = "tampered"


class TestNoVerificationLeakage:
    """Phase 3 reports where a document came from, not what it proves."""

    #: Words that would mean a schema had started judging truth.
    VERDICT_WORDS = (
        "verdict",
        "verdict_",
        "is_true",
        "truth",
        "supports",
        "contradicts",
        "confirms",
        "is_fraud",
        "fraud",
        "evidence_strength",
        "credibility_score",
        "reliability_score",
    )

    def test_documented_error_codes_are_the_only_ones(self) -> None:
        assert SEARCH_ERROR_CODES == {
            "SEARCH_NOT_CONFIGURED",
            "SEARCH_INVALID_QUERY",
            "SEARCH_SERVICE_ERROR",
            "SEARCH_TIMEOUT",
            "SEARCH_RATE_LIMITED",
            "SEARCH_AUTH_ERROR",
            "SEARCH_INVALID_RESPONSE",
        }

    def test_result_schema_has_no_verdict_field(self) -> None:
        fields = set(SearchResult.model_fields)

        for word in self.VERDICT_WORDS:
            assert word not in fields

    def test_response_schema_has_no_verdict_field(self) -> None:
        fields = set(SearchResponse.model_fields)

        for word in self.VERDICT_WORDS:
            assert word not in fields

    def test_source_type_does_not_encode_truth(self) -> None:
        # No category may be named after whether it supports a claim.
        values = {member.value for member in SourceType}

        for word in ("TRUE", "FALSE", "VERIFIED", "CONTRADICTED", "SCAM"):
            assert word not in values

    def test_utc_timestamps_are_settable(self) -> None:
        stamp = datetime(2026, 10, 1, tzinfo=timezone.utc)
        result = SearchResult(title="t", url="https://example.com/a", retrieved_at=stamp)

        assert result.retrieved_at == stamp