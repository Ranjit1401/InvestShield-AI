"""Evidence service orchestration (Phase 5).

The service is where Phase 3 results and Phase 4 decisions become a user-facing
evidence bundle. Three of its rules are structural rather than documented, and
each is tested here directly:

- no inputs, no evidence — never a placeholder item;
- only the results Phase 4 actually saw may be cited;
- Phase 4 decides — the status is copied, never recomputed.
"""

from __future__ import annotations

import pytest

from app.schemas.claims import ClaimType
from app.schemas.evidence import EvidenceRelation
from app.schemas.verification import (
    SEARCH_FAILED,
    VerificationResponse,
    VerificationStatus,
)
from app.services.evidence import EvidenceService, build_evidence_service
from app.services.verification.comparator import result_id_for, source_id_for
from app.services.evidence.source_normalizer import canonical_url_for
from tests.evidence_factories import (
    contradictory_verification,
    error_response,
    identity_not_found_verification,
    make_claim,
    make_entity,
    make_result,
    make_verification,
    no_relevant_source_verification,
    not_applicable_verification,
    ok_response,
    unavailable_verification,
    zero_results_verification,
)

ACME = "https://www.sebi.gov.in/intermediaries/acme"
ORDERS = "https://www.sebi.gov.in/orders/acme"
REGISTERED = "Acme Capital Advisors is registered as an investment adviser."
CANCELLED = "The registration of Acme Capital Advisors was cancelled."


@pytest.fixture
def service() -> EvidenceService:
    """A ready evidence service."""
    return EvidenceService()


@pytest.fixture
def claim():
    """A regulatory-status claim about the entity used throughout."""
    return make_claim(
        "Acme Capital Advisors is registered with SEBI as an investment adviser.",
        ClaimType.REGULATORY_STATUS,
        entity_ids=("entity_001",),
    )


@pytest.fixture
def entities():
    """The entity the claim is about."""
    return (make_entity("Acme Capital Advisors"),)


def sebi_result(url: str = ACME, snippet: str = REGISTERED, position: int = 1):
    """A SEBI page, already enriched the way `SearchService` would enrich it."""
    return make_result("Acme Capital Advisors", url, snippet=snippet, position=position).model_copy(
        update={
            "source_domain": canonical_url_for(
                make_result("t", url).model_copy(update={"url": url})
            ).split("/")[2],
        }
    )


class TestNoEvidenceWithoutResults:
    """An empty evidence tuple is a real, meaningful answer."""

    def test_a_search_that_never_ran_yields_no_evidence(
        self, service: EvidenceService, claim
    ) -> None:
        response = service.build_claim(claim, unavailable_verification(), (), ())
        assert response.evidence == ()
        assert response.sources == ()
        assert response.evidence_count == 0

    def test_the_warning_names_what_did_not_happen(
        self, service: EvidenceService, claim
    ) -> None:
        response = service.build_claim(claim, unavailable_verification(), (), ())
        assert response.warnings
        assert "unavailable" in response.warnings[0]

    def test_a_failed_search_yields_no_evidence(
        self, service: EvidenceService, claim
    ) -> None:
        verification = make_verification(
            status=VerificationStatus.INSUFFICIENT_EVIDENCE,
            reason_code=SEARCH_FAILED,
            reason="The search provider could not be reached.",
            confidence=0.0,
            source_ids=(),
            matched_result_ids=(),
        )
        response = service.build_claim(claim, verification, (), ())
        assert response.evidence == ()
        assert response.warnings

    def test_no_results_supplied_yields_no_evidence(
        self, service: EvidenceService, claim
    ) -> None:
        response = service.build_claim(claim, make_verification(), (), ())
        assert response.evidence == ()
        assert response.warnings

    def test_an_empty_response_list_warns_rather_than_raising(
        self, service: EvidenceService, claim
    ) -> None:
        response = service.build_claim(
            claim, make_verification(), (), (), responses=(unavailable_response := error_response(),)
        )
        assert response.evidence == ()
        assert unavailable_response.results == ()

    def test_zero_results_warns_that_absence_is_not_disproof(
        self, service: EvidenceService, claim
    ) -> None:
        """An empty register is not a finding that the claim is false."""
        response = service.build_claim(claim, zero_results_verification(), (), ())
        assert "absence of confirmation" in " ".join(response.warnings).casefold()

    def test_identity_not_found_warns_that_nothing_identified_the_party(
        self, service: EvidenceService, claim
    ) -> None:
        response = service.build_claim(claim, identity_not_found_verification(), (), ())
        assert response.evidence == ()
        assert "identified" in " ".join(response.warnings)

    def test_the_status_is_still_reported_on_an_empty_bundle(
        self, service: EvidenceService, claim
    ) -> None:
        """An empty evidence set must never hide which decision it explains."""
        response = service.build_claim(claim, unavailable_verification(), (), ())
        assert response.verification_status is VerificationStatus.INSUFFICIENT_EVIDENCE
        assert response.verification_reason_code == "SEARCH_UNAVAILABLE"


class TestNotApplicable:
    """A claim that was never externally verifiable has no evidence, ever."""

    def test_it_yields_no_evidence(self, service: EvidenceService, claim) -> None:
        response = service.build_claim(claim, not_applicable_verification(), (), ())
        assert response.evidence == ()
        assert response.sources == ()

    def test_it_yields_no_evidence_even_when_results_are_supplied(
        self, service: EvidenceService, claim, entities
    ) -> None:
        """Anything attached here would have to have been invented."""
        response = service.build_claim(
            claim,
            not_applicable_verification(),
            (sebi_result(),),
            entities,
        )
        assert response.evidence == ()

    def test_the_warning_explains_why(self, service: EvidenceService, claim) -> None:
        response = service.build_claim(claim, not_applicable_verification(), (), ())
        assert "not externally verifiable" in " ".join(response.warnings)


class TestVerifiedClaim:
    """The ordinary path: retrieved records become traceable evidence."""

    def test_a_supporting_record_becomes_a_supports_item(
        self, service: EvidenceService, claim, entities
    ) -> None:
        result = sebi_result()
        verification = make_verification(
            source_ids=(source_id_for(canonical_url_for(result)),),
            matched_result_ids=(result_id_for(canonical_url_for(result)),),
        )
        response = service.build_claim(claim, verification, (result,), entities)
        assert response.evidence_count == 1
        assert response.evidence[0].relationship is EvidenceRelation.SUPPORTS
        assert response.evidence[0].is_proof is True

    def test_the_excerpt_is_the_retrieved_snippet(
        self, service: EvidenceService, claim, entities
    ) -> None:
        result = sebi_result()
        verification = make_verification(
            source_ids=(source_id_for(canonical_url_for(result)),),
            matched_result_ids=(result_id_for(canonical_url_for(result)),),
        )
        response = service.build_claim(claim, verification, (result,), entities)
        assert response.evidence[0].excerpt == REGISTERED

    def test_the_status_is_copied_not_recomputed(
        self, service: EvidenceService, claim, entities
    ) -> None:
        result = sebi_result()
        verification = make_verification(
            source_ids=(source_id_for(canonical_url_for(result)),),
            matched_result_ids=(result_id_for(canonical_url_for(result)),),
        )
        response = service.build_claim(claim, verification, (result,), entities)
        assert response.verification_status is VerificationStatus.VERIFIED

    def test_counts_add_up(self, service: EvidenceService, claim, entities) -> None:
        first = sebi_result(ACME, REGISTERED, 1)
        second = sebi_result(ORDERS, CANCELLED, 2)
        verification = make_verification(
            source_ids=(
                source_id_for(canonical_url_for(first)),
                source_id_for(canonical_url_for(second)),
            ),
            matched_result_ids=(
                result_id_for(canonical_url_for(first)),
                result_id_for(canonical_url_for(second)),
            ),
        )
        response = service.build_claim(claim, verification, (first, second), entities)
        assert response.evidence_count == response.supports_count + response.contradicts_count + (
            response.context_count
        )
        assert response.source_count == len(response.sources)

    def test_the_query_that_surfaced_a_document_is_recorded(
        self, service: EvidenceService, claim, entities
    ) -> None:
        result = sebi_result()
        verification = make_verification(
            source_ids=(source_id_for(canonical_url_for(result)),),
            matched_result_ids=(result_id_for(canonical_url_for(result)),),
        )
        responses = (ok_response("Acme Capital Advisors SEBI registration", (result,)),)
        response = service.build_claim(
            claim, verification, (result,), entities, responses=responses
        )
        assert response.evidence[0].provider_query == "Acme Capital Advisors SEBI registration"

    def test_the_same_inputs_always_produce_the_same_bundle(
        self, service: EvidenceService, claim, entities
    ) -> None:
        """Determinism is what lets a report be re-checked later."""
        result = sebi_result()
        verification = make_verification(
            source_ids=(source_id_for(canonical_url_for(result)),),
            matched_result_ids=(result_id_for(canonical_url_for(result)),),
        )
        first = service.build_claim(claim, verification, (result,), entities)
        second = service.build_claim(claim, verification, (result,), entities)
        assert [item.id for item in first.evidence] == [item.id for item in second.evidence]


class TestContradiction:
    """A conflict is reported as a conflict, not resolved."""

    def test_both_sides_are_kept(self, service: EvidenceService, claim, entities) -> None:
        registration = sebi_result(ACME, REGISTERED, 1)
        cancellation = sebi_result(ORDERS, CANCELLED, 2)
        verification = contradictory_verification(conflicting=True)
        verification = verification.model_copy(
            update={
                "source_ids": (
                    source_id_for(canonical_url_for(registration)),
                    source_id_for(canonical_url_for(cancellation)),
                ),
                "matched_result_ids": (
                    result_id_for(canonical_url_for(registration)),
                    result_id_for(canonical_url_for(cancellation)),
                ),
            }
        )
        response = service.build_claim(claim, verification, (registration, cancellation), entities)
        assert response.supports_count == 1
        assert response.contradicts_count == 1
        assert response.evidence_count == 2

    def test_support_is_ordered_before_conflict(
        self, service: EvidenceService, claim, entities
    ) -> None:
        cancellation = sebi_result(ORDERS, CANCELLED, 1)
        registration = sebi_result(ACME, REGISTERED, 2)
        verification = contradictory_verification(conflicting=True).model_copy(
            update={
                "source_ids": (
                    source_id_for(canonical_url_for(registration)),
                    source_id_for(canonical_url_for(cancellation)),
                ),
                "matched_result_ids": (
                    result_id_for(canonical_url_for(registration)),
                    result_id_for(canonical_url_for(cancellation)),
                ),
            }
        )
        response = service.build_claim(claim, verification, (cancellation, registration), entities)
        assert response.evidence[0].relationship is EvidenceRelation.SUPPORTS

    def test_a_contradiction_is_not_an_accusation(
        self, service: EvidenceService, claim, entities
    ) -> None:
        """The bundle reports the record; it never names the investment a fraud."""
        result = sebi_result(ORDERS, CANCELLED, 1)
        verification = contradictory_verification().model_copy(
            update={
                "source_ids": (source_id_for(canonical_url_for(result)),),
                "matched_result_ids": (result_id_for(canonical_url_for(result)),),
            }
        )
        response = service.build_claim(claim, verification, (result,), entities)
        text = " ".join(response.warnings).casefold()
        assert "fraud" not in text
        assert "scam" not in text
        assert "safe" not in text


class TestOnlySeenResults:
    """Evidence can only cite documents Phase 4 actually examined."""

    def test_an_unseen_result_is_dropped(self, service: EvidenceService, claim, entities) -> None:
        seen = sebi_result(ACME, REGISTERED, 1)
        unseen = sebi_result(ORDERS, "An unrelated order.", 2)
        verification = make_verification(
            source_ids=(source_id_for(canonical_url_for(seen)),),
            matched_result_ids=(result_id_for(canonical_url_for(seen)),),
        )
        response = service.build_claim(claim, verification, (seen, unseen), entities)
        assert response.evidence_count == 1
        assert response.evidence[0].url == ACME

    def test_dropping_a_result_is_reported(self, service: EvidenceService, claim, entities) -> None:
        seen = sebi_result(ACME, REGISTERED, 1)
        unseen = sebi_result(ORDERS, "An unrelated order.", 2)
        verification = make_verification(
            source_ids=(source_id_for(canonical_url_for(seen)),),
            matched_result_ids=(result_id_for(canonical_url_for(seen)),),
        )
        response = service.build_claim(claim, verification, (seen, unseen), entities)
        assert any("excluded" in warning for warning in response.warnings)

    def test_a_result_cited_only_as_a_source_id_is_still_kept(
        self, service: EvidenceService, claim, entities
    ) -> None:
        """Phase 4 records both ids; both are part of the evidence base."""
        result = sebi_result()
        verification = make_verification(
            source_ids=(source_id_for(canonical_url_for(result)),),
            matched_result_ids=(),
        )
        response = service.build_claim(claim, verification, (result,), entities)
        assert response.evidence_count == 1

    def test_no_recorded_ids_means_nothing_can_be_cross_checked(
        self, service: EvidenceService, claim, entities
    ) -> None:
        """A reason code that produced no results leaves nothing to compare."""
        verification = zero_results_verification()
        response = service.build_claim(
            claim, verification, (sebi_result(),), entities
        )
        assert response.evidence_count == 1
        assert not any("excluded" in warning for warning in response.warnings)


class TestCoverageWarnings:
    """A gap between the decision and what can be shown is stated, not hidden."""

    def test_a_verified_claim_with_no_showable_text_warns(
        self, service: EvidenceService, claim, entities
    ) -> None:
        """A lookalike page cannot be shown as confirmation."""
        result = sebi_result("https://random-blog.example/acme", "Registered with SEBI.", 1)
        verification = make_verification(
            source_ids=(source_id_for(canonical_url_for(result)),),
            matched_result_ids=(result_id_for(canonical_url_for(result)),),
        )
        response = service.build_claim(claim, verification, (result,), entities)
        assert response.proof_count == 0
        assert any("no retrieved text" in warning for warning in response.warnings)

    def test_a_contradiction_with_no_showable_text_warns(
        self, service: EvidenceService, claim, entities
    ) -> None:
        result = sebi_result("https://random-blog.example/acme", "Cancelled.", 1)
        verification = contradictory_verification().model_copy(
            update={
                "source_ids": (source_id_for(canonical_url_for(result)),),
                "matched_result_ids": (result_id_for(canonical_url_for(result)),),
            }
        )
        response = service.build_claim(claim, verification, (result,), entities)
        assert response.proof_count == 0
        assert any("no retrieved text" in warning for warning in response.warnings)

    def test_a_well_supported_claim_has_no_coverage_warning(
        self, service: EvidenceService, claim, entities
    ) -> None:
        result = sebi_result()
        verification = make_verification(
            source_ids=(source_id_for(canonical_url_for(result)),),
            matched_result_ids=(result_id_for(canonical_url_for(result)),),
        )
        response = service.build_claim(claim, verification, (result,), entities)
        assert response.proof_count == 1
        assert response.warnings == ()

    def test_an_unverified_claim_needs_no_coverage_warning(
        self, service: EvidenceService, claim, entities
    ) -> None:
        """Only a positive claim carries an obligation to show its proof."""
        result = sebi_result("https://random-blog.example/acme", "Registered.", 1)
        verification = no_relevant_source_verification()
        response = service.build_claim(claim, verification, (result,), entities)
        assert not any("no retrieved text" in warning for warning in response.warnings)


class TestClaimMismatch:
    """Every item is anchored to a claim, and a mismatch is fatal."""

    def test_a_mismatched_claim_id_is_rejected(self, service: EvidenceService, entities) -> None:
        claim = make_claim("Acme is registered.", claim_id="claim_999")
        with pytest.raises(ValueError, match="does not match"):
            service.build_claim(claim, make_verification(), (), entities)


class TestBuildAll:
    """Batch assembly keeps one response per claim, in order."""

    @pytest.fixture
    def batch(self):
        """Two claims and one verification response covering both."""
        first = make_claim("Acme is registered.", claim_id="claim_001")
        second = make_claim("Acme guarantees 30% a year.", claim_id="claim_002")
        verification = VerificationResponse(
            results=(
                make_verification("claim_001"),
                no_relevant_source_verification("claim_002"),
            ),
            warnings=("Provider returned fewer results than requested.",),
        )
        return (first, second), verification

    def test_one_response_per_claim(
        self, service: EvidenceService, batch, entities
    ) -> None:
        claims, verification = batch
        bundle = service.build_all(claims, verification, {}, entities)
        assert [response.claim_id for response in bundle.responses] == [
            "claim_001",
            "claim_002",
        ]

    def test_batch_warnings_are_carried(self, service: EvidenceService, batch, entities) -> None:
        claims, verification = batch
        bundle = service.build_all(claims, verification, {}, entities)
        assert bundle.warnings == verification.warnings

    def test_a_claim_with_no_results_gets_an_empty_bundle(
        self, service: EvidenceService, batch, entities
    ) -> None:
        claims, verification = batch
        bundle = service.build_all(claims, verification, {}, entities)
        assert bundle.claims_without_evidence == 2
        assert bundle.claims_with_evidence == 0

    def test_a_result_without_a_matching_claim_object_is_still_reported(
        self, service: EvidenceService, batch, entities
    ) -> None:
        """Skipping it silently would hide a claim from the report."""
        _claims, verification = batch
        bundle = service.build_all((), verification, {}, entities)
        assert bundle.evidence_count == 0
        assert len(bundle.responses) == 2
        assert all(response.evidence == () for response in bundle.responses)
        assert all(response.warnings for response in bundle.responses)

    def test_results_are_matched_to_their_claim(
        self, service: EvidenceService, entities
    ) -> None:
        claim = make_claim("Acme is registered.", claim_id="claim_001")
        result = sebi_result()
        verification = VerificationResponse(
            results=(
                make_verification(
                    "claim_001",
                    source_ids=(source_id_for(canonical_url_for(result)),),
                    matched_result_ids=(result_id_for(canonical_url_for(result)),),
                ),
            )
        )
        bundle = service.build_all(
            (claim,), verification, {"claim_001": (result,)}, entities
        )
        assert bundle.claims_with_evidence == 1
        assert bundle.responses[0].evidence_count == 1

    def test_an_empty_batch_is_valid(self, service: EvidenceService) -> None:
        bundle = service.build_all((), VerificationResponse(results=()), {}, ())
        assert bundle.responses == ()
        assert bundle.evidence_count == 0


class TestFactory:
    """The factory exists so callers never construct the class directly."""

    def test_it_returns_a_usable_service(self) -> None:
        assert isinstance(build_evidence_service(), EvidenceService)

    def test_two_factories_produce_independent_services(self) -> None:
        """The service holds no state, so reuse cannot leak between claims."""
        assert build_evidence_service() is not build_evidence_service()
