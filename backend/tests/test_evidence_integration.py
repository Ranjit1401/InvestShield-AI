"""End-to-end evidence assembly over a real Phase 3 search and Phase 4 verdict.

Everything here runs against an in-memory `SearchProvider`, so the whole
`claim -> search -> verification -> evidence` chain is exercised with no key, no
socket and no database. This is the test that would fail if a later phase changed
the shape of `SearchResult` or `VerificationResult` without updating Phase 5.
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.schemas.claims import ClaimType
from app.schemas.search import SearchResponse, SearchResult, SearchStatus
from app.schemas.verification import VerificationResult, VerificationStatus
from app.services.evidence import EvidenceService, EvidenceResponse
from app.services.search import SearchProvider, SearchService
from app.services.verification import VerificationService, build_target
from tests.evidence_factories import make_claim, make_entity

REGISTERED = "Acme Capital Advisors is registered as an investment adviser."
CANCELLED = "The registration of Acme Capital Advisors was cancelled."
LOOKALIKE = "Acme Capital Advisors is registered with SEBI."
CAUTION = "SEBI cautions the public against guaranteed monthly returns."

ACME_PAGE = SearchResult(
    title="Acme Capital Advisors",
    url="https://www.sebi.gov.in/intermediaries/acme",
    snippet=REGISTERED,
    position=1,
)
ORDER_PAGE = SearchResult(
    title="Order against Acme Capital Advisors",
    url="https://www.sebi.gov.in/orders/acme",
    snippet=CANCELLED,
    position=2,
)
LOOKALIKE_PAGE = SearchResult(
    title="Acme Capital Advisors - SEBI Registered",
    url="https://fake-sebi-example.com/acme",
    snippet=LOOKALIKE,
    position=1,
)
CAUTION_PAGE = SearchResult(
    title="Caution against guaranteed returns",
    url="https://www.sebi.gov.in/circulars/returns",
    snippet=CAUTION,
    position=1,
)


class FixtureProvider(SearchProvider):
    """A provider that replays canned results for any query."""

    def __init__(self, results: tuple[SearchResult, ...]) -> None:
        self._results = results

    @property
    def name(self) -> str:
        return "fixture"

    def is_available(self) -> bool:
        return True

    def search(self, query: str, max_results: int) -> SearchResponse:
        return SearchResponse(
            query=query,
            results=self._results[:max_results],
            provider=self.name,
            status=SearchStatus.OK,
            provider_query_sent=True,
        )


class UnavailableProvider(SearchProvider):
    """A provider that reports it was never configured."""

    @property
    def name(self) -> str:
        return "null"

    def is_available(self) -> bool:
        return False

    def search(self, query: str, max_results: int) -> SearchResponse:
        return SearchResponse(
            query=query,
            results=(),
            provider=self.name,
            status=SearchStatus.UNAVAILABLE,
        )


@pytest.fixture
def entity():
    """The party under investigation."""
    return make_entity("Acme Capital Advisors")


@pytest.fixture
def evidence_service() -> EvidenceService:
    """A ready evidence service."""
    return EvidenceService()


def run_chain(
    pages: tuple[SearchResult, ...],
    entity: object,
    service: EvidenceService,
    *,
    claim_type: ClaimType = ClaimType.REGULATORY_STATUS,
    claim_text: str = (
        "Acme Capital Advisors is registered with SEBI as an investment adviser."
    ),
) -> tuple[EvidenceResponse, VerificationResult]:
    """Run `claim -> search -> verification -> evidence` over canned pages.

    Search is run through `SearchService` exactly as Phase 4 does, so the
    results handed to Phase 5 are the classified, de-duplicated ones an earlier
    phase produced — not a hand-built list.

    Args:
        pages: The results the provider will return for every query.
        entity: The `Entity` the claim is about.
        service: The evidence service to build the bundle with.
        claim_type: Claim family.
        claim_text: The claim as written.

    Returns:
        The evidence bundle and the Phase 4 result it explains.
    """
    claim = make_claim(claim_text, claim_type, entity_ids=("entity_001",))
    entities = (entity,)  # type: ignore[arg-type]
    search_service = SearchService(
        settings=get_settings(), provider=FixtureProvider(pages)
    )
    verification = VerificationService(
        settings=get_settings(), search_service=search_service
    ).verify_claim(claim, entities)

    target = build_target(claim, entities)
    responses = tuple(search_service.search(query, 5) for query in target.search_queries)
    results = tuple(result for response in responses for result in response.results)

    return service.build_claim(claim, verification, results, entities, responses=responses), (
        verification
    )


class TestVerifiedPipeline:
    """A regulator's record confirms the claim, and can be checked."""

    def test_the_whole_chain_produces_one_proof_item(
        self, entity, evidence_service: EvidenceService
    ) -> None:
        response, verification = run_chain((ACME_PAGE,), entity, evidence_service)

        assert verification.status is VerificationStatus.VERIFIED
        assert response.verification_status is VerificationStatus.VERIFIED
        assert response.proof_count == 1

    def test_the_excerpt_is_the_retrieved_snippet(
        self, entity, evidence_service: EvidenceService
    ) -> None:
        response, _ = run_chain((ACME_PAGE,), entity, evidence_service)

        assert response.evidence[0].excerpt == REGISTERED
        assert response.evidence[0].excerpt_origin == "snippet"

    def test_phase_three_classifies_the_publisher_and_phase_four_tiers_it(
        self, entity, evidence_service: EvidenceService
    ) -> None:
        """Phase 5 changes neither classification."""
        response, _ = run_chain((ACME_PAGE,), entity, evidence_service)
        source = response.evidence[0].source

        assert source.source_type.value == "REGULATOR"
        assert source.source_tier.value == "TIER_1_PRIMARY_REGULATOR"
        assert source.is_authoritative is True

    def test_the_supporting_cue_is_available_for_audit(
        self, entity, evidence_service: EvidenceService
    ) -> None:
        response, _ = run_chain((ACME_PAGE,), entity, evidence_service)

        assert response.evidence[0].matched_cue == "registered as"
        assert response.evidence[0].relationship.value == "SUPPORTS"

    def test_the_query_that_surfaced_the_record_is_recorded(
        self, entity, evidence_service: EvidenceService
    ) -> None:
        response, _ = run_chain((ACME_PAGE,), entity, evidence_service)

        assert response.evidence[0].provider_query


class TestContradictionPipeline:
    """Two official records that disagree are both shown."""

    def test_the_conflict_survives_into_the_bundle(
        self, entity, evidence_service: EvidenceService
    ) -> None:
        """Phase 4 declines to pick a side; evidence shows that it could not."""
        response, verification = run_chain(
            (ACME_PAGE, ORDER_PAGE), entity, evidence_service
        )

        assert verification.status is VerificationStatus.INSUFFICIENT_EVIDENCE
        assert verification.reason_code == "CONFLICTING_AUTHORITATIVE_SOURCES"
        assert response.verification_reason_code == "CONFLICTING_AUTHORITATIVE_SOURCES"
        assert response.supports_count == 1
        assert response.contradicts_count == 1

    def test_both_quoted_texts_are_the_retrieved_ones(
        self, entity, evidence_service: EvidenceService
    ) -> None:
        response, _ = run_chain((ACME_PAGE, ORDER_PAGE), entity, evidence_service)

        assert {item.excerpt for item in response.evidence} == {REGISTERED, CANCELLED}

    def test_support_is_shown_before_conflict(
        self, entity, evidence_service: EvidenceService
    ) -> None:
        response, _ = run_chain((ORDER_PAGE, ACME_PAGE), entity, evidence_service)

        assert response.evidence[0].relationship.value == "SUPPORTS"


class TestLookalikePipeline:
    """A convincing page on the wrong host is never proof."""

    def test_a_lookalike_domain_cannot_confirm_a_claim(
        self, entity, evidence_service: EvidenceService
    ) -> None:
        response, verification = run_chain((LOOKALIKE_PAGE,), entity, evidence_service)

        assert verification.status is not VerificationStatus.VERIFIED
        assert response.proof_count == 0

    def test_the_lookalike_is_still_shown_as_context(
        self, entity, evidence_service: EvidenceService
    ) -> None:
        """Hidden context would hide what the reader most needs to see."""
        response, _ = run_chain((LOOKALIKE_PAGE,), entity, evidence_service)

        assert response.evidence_count == 1
        assert response.evidence[0].excerpt == LOOKALIKE
        assert response.evidence[0].evidence_type.value == "SEARCH_RESULT"

    def test_a_similarly_named_company_is_context_not_proof(
        self, entity, evidence_service: EvidenceService
    ) -> None:
        """The case Phase 4's identity gate exists for."""
        lookalike = SearchResult(
            title="Acme Capital Holdings",
            url="https://www.sebi.gov.in/intermediaries/acme-holdings",
            snippet="Acme Capital Holdings is registered as an investment adviser.",
            position=1,
        )
        response, verification = run_chain((lookalike,), entity, evidence_service)

        assert verification.status is not VerificationStatus.VERIFIED
        assert response.proof_count == 0
        assert response.evidence[0].evidence_type.value == "REGULATORY_RECORD"


class TestReturnPromisePipeline:
    """A promise nobody can register has context but no record."""

    def test_a_regulatory_warning_is_only_a_mention(
        self, entity, evidence_service: EvidenceService
    ) -> None:
        response, verification = run_chain(
            (CAUTION_PAGE,),
            entity,
            evidence_service,
            claim_type=ClaimType.RETURN_PROMISE,
            claim_text="Acme Capital Advisors guarantees 30% monthly returns.",
        )

        assert verification.status is not VerificationStatus.VERIFIED
        assert response.proof_count == 0
        assert response.evidence[0].relationship.value in {"CONTEXT", "MENTIONS"}


class TestUnavailablePipeline:
    """No search, no evidence, and an honest reason."""

    def test_an_unconfigured_provider_yields_an_empty_bundle(
        self, entity, evidence_service: EvidenceService
    ) -> None:
        claim = make_claim(
            "Acme Capital Advisors is registered with SEBI as an investment adviser.",
            ClaimType.REGULATORY_STATUS,
            entity_ids=("entity_001",),
        )
        entities = (entity,)
        verification = VerificationService(
            settings=get_settings(),
            search_service=SearchService(
                settings=get_settings(), provider=UnavailableProvider()
            ),
        ).verify_claim(claim, entities)

        response = evidence_service.build_claim(claim, verification, (), entities)

        assert response.evidence == ()
        assert response.warnings
        assert "unavailable" in response.warnings[0]


class TestNoFabricationEndToEnd:
    """Every excerpt in a real bundle is text a provider actually returned."""

    def test_no_excerpt_is_synthesised(
        self, entity, evidence_service: EvidenceService
    ) -> None:
        pages = (ACME_PAGE, ORDER_PAGE, CAUTION_PAGE)
        response, _ = run_chain(pages, entity, evidence_service)

        assert response.evidence
        for item in response.evidence:
            retrieved = tuple(
                text
                for page in pages
                for text in (page.title, page.snippet or "")
            )
            assert any(item.excerpt == text or item.excerpt in text for text in retrieved), (
                item.excerpt
            )

    def test_every_excerpt_names_the_field_it_came_from(
        self, entity, evidence_service: EvidenceService
    ) -> None:
        response, _ = run_chain((ACME_PAGE, ORDER_PAGE), entity, evidence_service)

        assert {item.excerpt_origin for item in response.evidence} == {"snippet"}

    def test_every_item_traces_back_to_a_real_document(
        self, entity, evidence_service: EvidenceService
    ) -> None:
        pages = (ACME_PAGE, ORDER_PAGE)
        response, _ = run_chain(pages, entity, evidence_service)
        urls = {page.url for page in pages}

        for item in response.evidence:
            assert item.url in urls
            assert item.trace_path.isascii()

    def test_the_bundle_states_no_opinion_about_the_investment(
        self, entity, evidence_service: EvidenceService
    ) -> None:
        """Evidence reports records; it does not advise (D-020, D-021)."""
        response, _ = run_chain((ACME_PAGE, ORDER_PAGE), entity, evidence_service)
        text = " ".join(response.warnings).casefold()

        for term in ("scam", "fraud", "safe", "recommend", "avoid"):
            assert term not in text, term
