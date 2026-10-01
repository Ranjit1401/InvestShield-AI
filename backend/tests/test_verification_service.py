"""Tests for `VerificationService` (Phase 4, §6).

The service is the only part of Phase 4 that talks to the outside world, so
these tests exercise it against a **fake** `SearchProvider`. No key, no
network, no quota: on this machine `SERPAPI_KEY` is absent, and the
no-credential path is a first-class outcome rather than a skipped test.

The paths that must all work offline: available provider, no credentials, zero
results, provider error, and a provider that breaks its own no-raise contract
(D-009).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.schemas.claims import ClaimType
from app.schemas.extraction import ClaimEntityLink, ExtractionMode, ExtractionResult, RelationshipType
from app.schemas.search import (
    SEARCH_SERVICE_ERROR,
    SEARCH_TIMEOUT,
    SearchResponse,
    SearchResult,
    SearchStatus,
)
from app.schemas.verification import (
    SEARCH_FAILED,
    SEARCH_UNAVAILABLE,
    ZERO_RESULTS,
    VerificationStatus,
)
from app.services.search import NullSearchProvider, SearchProvider, SearchService
from app.services.verification import (
    MAX_QUERIES_PER_CLAIM,
    VerificationService,
    build_verification_service,
    contains_prohibited_term,
)
from tests.verification_factories import make_claim, make_entity, make_result


def make_settings(**overrides: Any) -> Settings:
    """Build test settings with no external credentials."""
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
    """A scriptable stand-in for a real search backend.

    Args:
        name: Provider name to report.
        available: Whether the provider claims to be runnable.
        results: Results returned for every query when `responses` is unset.
        responses: Per-call responses, consumed in order.
        raise_on_search: Violate the no-raise contract, to prove the service
            still degrades instead of failing an investigation.
    """

    name = "fake"

    def __init__(
        self,
        *,
        name: str = "fake",
        available: bool = True,
        results: tuple[SearchResult, ...] = (),
        responses: tuple[SearchResponse, ...] | None = None,
        raise_on_search: bool = False,
    ) -> None:
        self.name = name
        self._available = available
        self._results = results
        self._responses = list(responses or ())
        self._raise_on_search = raise_on_search
        self.queries: list[str] = []
        self.max_results_requested: list[int] = []

    def is_available(self) -> bool:
        """Report the configured availability without any network call."""
        return self._available

    def search(self, query: str, max_results: int) -> SearchResponse:
        """Record the call and return the scripted response."""
        if self._raise_on_search:
            raise RuntimeError("provider contract violated")
        self.queries.append(query)
        self.max_results_requested.append(max_results)
        if self._responses:
            return self._responses.pop(0)
        return SearchResponse(
            query=query,
            results=self._results,
            provider=self.name,
            status=SearchStatus.OK,
            provider_query_sent=True,
        )


def sebi_result(path: str = "/a", title: str = "Acme Capital registration", snippet: str | None = None):
    """A SEBI-hosted result about `Acme Capital`."""
    return make_result(
        title,
        f"https://www.sebi.gov.in{path}",
        snippet=snippet or "Acme Capital is registered as an investment adviser.",
    )


ENTITY = make_entity("Acme Capital")
CLAIM = make_claim(
    "Acme Capital is SEBI registered.",
    ClaimType.REGULATORY_STATUS,
    entity_ids=(ENTITY.id,),
)


def build_service(provider: SearchProvider, **kwargs: Any) -> VerificationService:
    """Build a service backed by `provider` and isolated settings."""
    settings = make_settings()
    return VerificationService(
        settings=settings,
        search_service=SearchService(settings=settings, provider=provider),
        **kwargs,
    )


class TestVerifiedPath:
    """The happy path, offline."""

    def test_authoritative_confirmation_verifies_the_claim(self) -> None:
        provider = FakeProvider(results=(sebi_result(),))
        result = build_service(provider).verify_claim(CLAIM, (ENTITY,))
        assert result.status is VerificationStatus.VERIFIED
        assert result.source_ids
        assert result.reason_code == "AUTHORITATIVE_SOURCE_CONFIRMS"

    def test_every_query_result_is_deduplicated_by_canonical_url(self) -> None:
        provider = FakeProvider(results=(sebi_result(),))
        result = build_service(provider).verify_claim(CLAIM, (ENTITY,))
        assert len(result.source_ids) == 1

    def test_query_count_is_bounded(self) -> None:
        provider = FakeProvider()
        build_service(provider).verify_claim(CLAIM, (ENTITY,))
        assert 0 < len(provider.queries) <= MAX_QUERIES_PER_CLAIM

    def test_queries_are_recorded_on_the_result(self) -> None:
        provider = FakeProvider(results=(sebi_result(),))
        result = build_service(provider).verify_claim(CLAIM, (ENTITY,))
        assert result.queries
        assert set(result.queries) == set(provider.queries)

    def test_results_per_query_is_forwarded(self) -> None:
        provider = FakeProvider()
        build_service(provider, results_per_query=3).verify_claim(CLAIM, (ENTITY,))
        assert set(provider.max_results_requested) == {3}


class TestNoCredentials:
    """`SERPAPI_KEY` is absent here; that must be stated, not guessed."""

    def test_unavailable_search_is_insufficient_evidence(self) -> None:
        provider = FakeProvider(available=False)
        result = build_service(provider).verify_claim(CLAIM, (ENTITY,))
        assert result.status is VerificationStatus.INSUFFICIENT_EVIDENCE
        assert result.reason_code == SEARCH_UNAVAILABLE

    def test_unavailable_search_is_never_contradiction(self) -> None:
        provider = FakeProvider(available=False)
        result = build_service(provider).verify_claim(CLAIM, (ENTITY,))
        assert result.status is not VerificationStatus.CONTRADICTED
        assert result.source_ids == ()

    def test_unavailable_search_fabricates_no_sources(self) -> None:
        provider = FakeProvider(available=False)
        result = build_service(provider).verify_claim(CLAIM, (ENTITY,))
        assert result.source_ids == ()
        assert result.matched_result_ids == ()

    def test_service_reports_unavailability(self) -> None:
        provider = FakeProvider(available=False)
        assert build_service(provider).available is False

    def test_null_provider_is_the_same_outcome(self) -> None:
        settings = make_settings()
        service = VerificationService(
            settings=settings,
            search_service=SearchService(settings=settings, provider=NullSearchProvider()),
        )
        result = service.verify_claim(CLAIM, (ENTITY,))
        assert result.reason_code == SEARCH_UNAVAILABLE


class TestZeroResults:
    """A successful search that matched nothing."""

    def test_registration_claim_is_unverified(self) -> None:
        provider = FakeProvider(results=())
        result = build_service(provider).verify_claim(CLAIM, (ENTITY,))
        assert result.status is VerificationStatus.UNVERIFIED
        assert result.reason_code == ZERO_RESULTS

    def test_zero_results_is_not_contradiction(self) -> None:
        provider = FakeProvider(results=())
        result = build_service(provider).verify_claim(CLAIM, (ENTITY,))
        assert result.status is not VerificationStatus.CONTRADICTED

    def test_lookalike_website_does_not_confirm(self) -> None:
        provider = FakeProvider(
            results=(
                make_result(
                    "Acme Capital registration",
                    "https://fake-sebi-example.com/acme",
                    snippet="Acme Capital is registered with SEBI.",
                ),
            )
        )
        result = build_service(provider).verify_claim(CLAIM, (ENTITY,))
        assert result.status is not VerificationStatus.VERIFIED
        assert result.status is not VerificationStatus.CONTRADICTED


class TestProviderFailures:
    """Errors stay errors."""

    @staticmethod
    def error_script(count: int) -> tuple:
        """Return `count` scripted failed responses."""
        return tuple(
            SearchResponse(
                query=f"q{index}",
                status=SearchStatus.ERROR,
                error_code=SEARCH_TIMEOUT,
                provider="fake",
                provider_query_sent=True,
            )
            for index in range(count)
        )

    def test_provider_error_is_insufficient_evidence(self) -> None:
        provider = FakeProvider(responses=self.error_script(MAX_QUERIES_PER_CLAIM))
        result = build_service(provider).verify_claim(CLAIM, (ENTITY,))
        assert result.reason_code == SEARCH_FAILED
        assert result.status is VerificationStatus.INSUFFICIENT_EVIDENCE

    def test_error_code_appears_in_the_warnings(self) -> None:
        provider = FakeProvider(responses=self.error_script(MAX_QUERIES_PER_CLAIM))
        result = build_service(provider).verify_claim(CLAIM, (ENTITY,))
        assert any(SEARCH_TIMEOUT in warning for warning in result.warnings)

    def test_partial_failure_still_reports_the_failure(self) -> None:
        """One failed query among several is stated as partial coverage."""
        provider = FakeProvider(
            responses=(
                SearchResponse(
                    query="q1",
                    status=SearchStatus.ERROR,
                    error_code=SEARCH_TIMEOUT,
                    provider="fake",
                    provider_query_sent=True,
                ),
            )
        )
        result = build_service(provider).verify_claim(CLAIM, (ENTITY,))
        assert any("partial" in warning for warning in result.warnings)
        assert result.status is not VerificationStatus.CONTRADICTED

    def test_provider_that_raises_does_not_break_the_investigation(self) -> None:
        provider = FakeProvider(raise_on_search=True)
        result = build_service(provider).verify_claim(CLAIM, (ENTITY,))
        assert result.status is VerificationStatus.INSUFFICIENT_EVIDENCE
        assert result.reason_code == SEARCH_FAILED
        assert result.source_ids == ()

    def test_raised_error_reports_a_stable_code(self) -> None:
        provider = FakeProvider(raise_on_search=True)
        result = build_service(provider).verify_claim(CLAIM, (ENTITY,))
        assert any(SEARCH_SERVICE_ERROR in warning for warning in result.warnings)


class TestNotApplicable:
    """Claims that are not externally verifiable spend no quota."""

    @pytest.mark.parametrize(
        "claim_type",
        [
            ClaimType.INVESTMENT_OPPORTUNITY,
            ClaimType.PAYMENT_INSTRUCTION,
            ClaimType.OTHER,
        ],
    )
    def test_no_search_is_issued(self, claim_type: ClaimType) -> None:
        provider = FakeProvider(results=(sebi_result(),))
        claim = make_claim("Act now.", claim_type)
        result = build_service(provider).verify_claim(claim, ())
        assert provider.queries == []
        assert result.status is VerificationStatus.NOT_APPLICABLE
        assert result.reason_code == "NOT_A_FACTUAL_CLAIM"


class TestBatchVerification:
    """Batch behaviour, ordering and honest coverage reporting."""

    def test_results_follow_input_order(self) -> None:
        provider = FakeProvider(results=())
        claims = (
            make_claim("Acme Capital is registered.", ClaimType.REGULATORY_STATUS, claim_id="c1"),
            make_claim("Invest now.", ClaimType.INVESTMENT_OPPORTUNITY, claim_id="c2"),
        )
        response = build_service(provider).verify_claims(claims, ())
        assert response.claim_ids == ("c1", "c2")

    def test_counts_match_results(self) -> None:
        provider = FakeProvider(results=())
        claims = (
            make_claim("Acme is registered.", ClaimType.REGULATORY_STATUS, claim_id="c1"),
            make_claim("Invest now.", ClaimType.INVESTMENT_OPPORTUNITY, claim_id="c2"),
        )
        response = build_service(provider).verify_claims(claims, ())
        assert response.unverified_count == 1
        assert response.not_applicable_count == 1
        assert response.results and len(response.results) == 2

    def test_unavailable_search_is_reported_at_batch_level(self) -> None:
        provider = FakeProvider(available=False)
        response = build_service(provider).verify_claims((CLAIM,), (ENTITY,))
        assert response.warnings
        assert "unavailable" in response.warnings[0]

    def test_healthy_batch_has_no_batch_warnings(self) -> None:
        provider = FakeProvider(results=(sebi_result(),))
        response = build_service(provider).verify_claims((CLAIM,), (ENTITY,))
        assert response.warnings == ()

    def test_empty_batch(self) -> None:
        response = build_service(FakeProvider()).verify_claims((), ())
        assert response.results == ()
        assert response.warnings == ()


class TestExtractionEntryPoint:
    """The extraction-level entry point used by later phases."""

    def test_verifies_every_claim_of_an_extraction(self) -> None:
        provider = FakeProvider(results=(sebi_result(),))
        extraction = ExtractionResult(
            claims=(
                make_claim(
                    "Acme Capital is registered.",
                    ClaimType.REGULATORY_STATUS,
                    claim_id="claim_001",
                    entity_ids=(ENTITY.id,),
                ),
                make_claim("Invest now.", ClaimType.INVESTMENT_OPPORTUNITY, claim_id="claim_002"),
            ),
            entities=(ENTITY,),
            relationships=(
                ClaimEntityLink(
                    claim_id="claim_001",
                    entity_id=ENTITY.id,
                    relationship=RelationshipType.SUBJECT,
                ),
            ),
            extraction_mode=ExtractionMode.FALLBACK,
        )
        response = build_service(provider).verify_extraction(extraction)
        assert response.claim_ids == ("claim_001", "claim_002")
        assert response.verified_count == 1
        assert response.not_applicable_count == 1


class TestServiceInvariants:
    """Properties that must hold across every path."""

    def _every_claim_type(self) -> list:
        """Build one claim per claim family."""
        return [
            make_claim(f"A statement about claim type {index}.", claim_type, claim_id=f"c{index}")
            for index, claim_type in enumerate(ClaimType)
        ]

    def test_no_claim_type_produces_a_forbidden_status(self) -> None:
        allowed = {status.value for status in VerificationStatus}
        provider = FakeProvider(results=(sebi_result(),))
        service = build_service(provider)
        for claim in self._every_claim_type():
            result = service.verify_claim(claim, (ENTITY,))
            assert result.status.value in allowed

    def test_no_output_contains_verdict_language(self) -> None:
        provider = FakeProvider(results=(sebi_result(),))
        service = build_service(provider)
        for claim in self._every_claim_type():
            result = service.verify_claim(claim, (ENTITY,))
            text = " ".join((result.reason, *result.warnings))
            assert contains_prohibited_term(text) is None, claim.claim_type

    def test_service_never_constructs_a_provider_itself(self) -> None:
        """Consumers depend on `SearchService`; nothing else builds a backend."""
        provider = FakeProvider(results=(sebi_result(),))
        service = build_service(provider)
        assert service.search_service is not None
        assert service.search_service.provider_name == "fake"

    def test_build_helper_uses_configuration(self) -> None:
        service = build_verification_service(make_settings())
        assert isinstance(service, VerificationService)
        assert service.available is False