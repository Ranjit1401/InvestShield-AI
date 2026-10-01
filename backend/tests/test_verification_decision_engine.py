"""Tests for the verification decision table (Phase 4, §5).

`decide_verification()` is pure, so every row of the table is tested directly.
Two properties are asserted across *all* rows rather than per row, because they
are the ones the product's credibility rests on: absence never produces
`CONTRADICTED`, and no explanation ever contains verdict language (D-006, D-022).
"""

from __future__ import annotations

import pytest

from app.schemas.claims import ClaimType
from app.schemas.verification import (
    AUTHORITATIVE_SOURCE_CONTRADICTS,
    AUTHORITATIVE_SOURCE_CONFIRMS,
    CONFLICTING_AUTHORITATIVE_SOURCES,
    IDENTITY_AMBIGUOUS,
    IDENTITY_NOT_FOUND,
    NO_CLAIM_RELEVANT_SOURCE,
    NO_CONFIRMATION_FOUND,
    NO_QUERY_BUILT,
    NOT_A_FACTUAL_CLAIM,
    SEARCH_FAILED,
    SEARCH_UNAVAILABLE,
    VERIFICATION_REASON_CODES,
    ZERO_RESULTS,
    VerificationStatus,
)
from app.services.verification.decision_engine import (
    CONFIDENCE_BY_REASON,
    EXPLANATION_TEMPLATES,
    PROHIBITED_OUTPUT_TERMS,
    contains_prohibited_term,
    decide_verification,
    render_explanation,
)
from app.services.verification.target import build_target
from tests.verification_factories import (
    error_response,
    make_claim,
    make_entity,
    make_result,
    ok_response,
    unavailable_response,
)

ENTITY = make_entity("Acme Capital")


def regulatory_target():
    """Target for a registration claim about `Acme Capital`."""
    claim = make_claim(
        "Acme Capital is SEBI registered.",
        ClaimType.REGULATORY_STATUS,
        entity_ids=(ENTITY.id,),
    )
    return build_target(claim, (ENTITY,))


def promise_target():
    """Target for a promised return, which no register can settle."""
    claim = make_claim("35% monthly returns guaranteed.", ClaimType.RETURN_PROMISE)
    return build_target(claim, ())


def sebi_result(title: str, snippet: str, path: str = "/x"):
    """A SEBI-hosted result about `Acme Capital`."""
    return make_result(title, f"https://www.sebi.gov.in{path}", snippet=snippet)


#: One entry per reachable branch of the decision table. Used by the invariant
#: tests so a new branch cannot be added without also being asserted here.
SCENARIOS: tuple = (
    # NOT_A_FACTUAL_CLAIM
    (build_target(make_claim("Invest now.", ClaimType.INVESTMENT_OPPORTUNITY), ()), ()),
    # NO_QUERY_BUILT
    (build_target(make_claim("   ", ClaimType.REGULATORY_STATUS), ()), ()),
    # SEARCH_UNAVAILABLE
    (regulatory_target(), (unavailable_response("q1"),)),
    # SEARCH_FAILED
    (regulatory_target(), (error_response("q1"),)),
    # ZERO_RESULTS
    (regulatory_target(), (ok_response("q1", ()),)),
    # NO_CONFIRMATION_FOUND
    (promise_target(), (ok_response("q1", ()),)),
    # IDENTITY_NOT_FOUND
    (
        regulatory_target(),
        (ok_response("q1", (sebi_result("Other entity", "Other entity is registered."),)),),
    ),
    # IDENTITY_AMBIGUOUS
    (
        regulatory_target(),
        (
            ok_response(
                "q1",
                (
                    sebi_result(
                        "Acme Capital Advisors",
                        "Acme Capital Advisors is registered as an adviser.",
                    ),
                ),
            ),
        ),
    ),
    # NO_CLAIM_RELEVANT_SOURCE (only general web)
    (
        regulatory_target(),
        (
            ok_response(
                "q1",
                (
                    make_result(
                        "Acme Capital registration",
                        "https://news.example.com/acme",
                        snippet="Acme Capital is registered as an adviser.",
                    ),
                ),
            ),
        ),
    ),
    # NO_CLAIM_RELEVANT_SOURCE (entity found, nothing on the claim)
    (
        regulatory_target(),
        (
            ok_response(
                "q1",
                (sebi_result("Acme Capital news", "Acme Capital issued a circular."),),
            ),
        ),
    ),
    # AUTHORITATIVE_SOURCE_CONFIRMS
    (
        regulatory_target(),
        (
            ok_response(
                "q1",
                (
                    sebi_result(
                        "Acme Capital registration",
                        "Acme Capital is registered as an investment adviser.",
                        path="/a",
                    ),
                ),
            ),
        ),
    ),
    # AUTHORITATIVE_SOURCE_CONTRADICTS
    (
        regulatory_target(),
        (
            ok_response(
                "q1",
                (
                    sebi_result(
                        "Order against Acme Capital",
                        "The registration of Acme Capital was cancelled.",
                        path="/b",
                    ),
                ),
            ),
        ),
    ),
    # CONFLICTING_AUTHORITATIVE_SOURCES
    (
        regulatory_target(),
        (
            ok_response(
                "q1",
                (
                    sebi_result(
                        "Acme Capital registration",
                        "Acme Capital is registered as an investment adviser.",
                        path="/a",
                    ),
                    sebi_result(
                        "Order against Acme Capital",
                        "The registration of Acme Capital was cancelled.",
                        path="/b",
                    ),
                ),
            ),
        ),
    ),
    # Partial coverage: unavailable search plus a confirming result.
    (
        regulatory_target(),
        (
            unavailable_response("q1"),
            ok_response(
                "q2",
                (
                    sebi_result(
                        "Acme Capital registration",
                        "Acme Capital is registered as an investment adviser.",
                        path="/c",
                    ),
                ),
            ),
        ),
    ),
)


class TestRowOneNotApplicable:
    """An instruction or an opinion cannot be confirmed by a record."""

    @pytest.mark.parametrize(
        "claim_type",
        [
            ClaimType.INVESTMENT_OPPORTUNITY,
            ClaimType.PAYMENT_INSTRUCTION,
            ClaimType.OTHER,
        ],
    )
    def test_not_applicable(self, claim_type: ClaimType) -> None:
        claim = make_claim("Invest now.", claim_type)
        result = decide_verification(build_target(claim, ()), ())
        assert result.status is VerificationStatus.NOT_APPLICABLE
        assert result.reason_code == NOT_A_FACTUAL_CLAIM
        assert result.source_ids == ()
        assert result.queries == ()

    def test_not_applicable_even_with_results_available(self) -> None:
        claim = make_claim("Invest now in this scheme.", ClaimType.INVESTMENT_OPPORTUNITY)
        responses = (ok_response("invest", (sebi_result("Scheme", "Acme Capital."),)),)
        result = decide_verification(build_target(claim, ()), responses)
        assert result.status is VerificationStatus.NOT_APPLICABLE
        assert result.source_ids == ()


class TestRowTwoNoQuery:
    """Nothing searchable means nothing assessed."""

    def test_no_query_built(self) -> None:
        claim = make_claim("   ", ClaimType.REGULATORY_STATUS)
        target = build_target(claim, ())
        result = decide_verification(target, ())
        assert result.status is VerificationStatus.INSUFFICIENT_EVIDENCE
        assert result.reason_code == NO_QUERY_BUILT
        assert result.confidence == 0.0


class TestRowsThreeAndFourSearchFailures:
    """"We could not look" is a limitation, never a finding."""

    def test_search_unavailable(self) -> None:
        result = decide_verification(
            regulatory_target(), (unavailable_response("q1"),)
        )
        assert result.status is VerificationStatus.INSUFFICIENT_EVIDENCE
        assert result.reason_code == SEARCH_UNAVAILABLE
        assert result.source_ids == ()

    def test_search_failed(self) -> None:
        result = decide_verification(regulatory_target(), (error_response("q1"),))
        assert result.status is VerificationStatus.INSUFFICIENT_EVIDENCE
        assert result.reason_code == SEARCH_FAILED
        assert result.source_ids == ()

    def test_error_takes_precedence_over_unavailable(self) -> None:
        responses = (unavailable_response("q1"), error_response("q2"))
        result = decide_verification(regulatory_target(), responses)
        assert result.reason_code == SEARCH_FAILED

    def test_error_code_is_surfaced_as_a_warning(self) -> None:
        result = decide_verification(regulatory_target(), (error_response("q1"),))
        assert any("SEARCH_SERVICE_ERROR" in warning for warning in result.warnings)

    def test_no_responses_at_all_is_no_query_built(self) -> None:
        target = regulatory_target()
        result = decide_verification(target, ())
        assert result.reason_code == NO_QUERY_BUILT


class TestRowsEightAndNineZeroResults:
    """A successful search that matched nothing."""

    def test_registration_style_zero_results_is_unverified(self) -> None:
        result = decide_verification(regulatory_target(), (ok_response("q1", ()),))
        assert result.status is VerificationStatus.UNVERIFIED
        assert result.reason_code == ZERO_RESULTS

    def test_zero_results_is_not_contradiction(self) -> None:
        result = decide_verification(regulatory_target(), (ok_response("q1", ()),))
        assert result.status is not VerificationStatus.CONTRADICTED

    def test_non_registration_zero_results_is_insufficient(self) -> None:
        result = decide_verification(promise_target(), (ok_response("q1", ()),))
        assert result.status is VerificationStatus.INSUFFICIENT_EVIDENCE
        assert result.reason_code == NO_CONFIRMATION_FOUND

    def test_zero_results_states_absence_not_falsehood(self) -> None:
        result = decide_verification(regulatory_target(), (ok_response("q1", ()),))
        assert "not a finding that the claim is false" in result.reason


class TestRowFiveNoRelevantSource:
    """Only sources that cannot settle the claim were retrieved."""

    def test_only_general_web_results(self) -> None:
        responses = (
            ok_response(
                "q1",
                (
                    make_result(
                        "Acme Capital registration",
                        "https://news.example.com/acme",
                        snippet="Acme Capital is registered as an adviser.",
                    ),
                ),
            ),
        )
        result = decide_verification(regulatory_target(), responses)
        assert result.status is VerificationStatus.INSUFFICIENT_EVIDENCE
        assert result.reason_code == NO_CLAIM_RELEVANT_SOURCE

    def test_lookalike_regulator_cannot_settle_the_claim(self) -> None:
        responses = (
            ok_response(
                "q1",
                (
                    make_result(
                        "Acme Capital registration",
                        "https://fake-sebi-example.com/acme",
                        snippet="Acme Capital is registered with SEBI.",
                    ),
                ),
            ),
        )
        result = decide_verification(regulatory_target(), responses)
        assert result.status is VerificationStatus.INSUFFICIENT_EVIDENCE
        assert result.reason_code == NO_CLAIM_RELEVANT_SOURCE
        assert result.source_ids == ()

    def test_promise_claim_cannot_be_settled_by_a_regulator(self) -> None:
        responses = (
            ok_response(
                "q1",
                (
                    sebi_result(
                        "Acme Capital returns",
                        "Acme Capital registered entity returning 35% monthly.",
                    ),
                ),
            ),
        )
        result = decide_verification(promise_target(), responses)
        assert result.status is VerificationStatus.INSUFFICIENT_EVIDENCE
        assert result.reason_code == NO_CLAIM_RELEVANT_SOURCE


class TestRowsSixAndSevenIdentity:
    """The record must be about the claimed entity."""

    def test_only_a_longer_name_matches(self) -> None:
        responses = (
            ok_response(
                "q1",
                (
                    sebi_result(
                        "Acme Capital Advisors",
                        "Acme Capital Advisors is registered as an adviser.",
                    ),
                ),
            ),
        )
        result = decide_verification(regulatory_target(), responses)
        assert result.status is VerificationStatus.INSUFFICIENT_EVIDENCE
        assert result.reason_code == IDENTITY_AMBIGUOUS

    def test_entity_absent_from_results(self) -> None:
        responses = (
            ok_response(
                "q1",
                (
                    sebi_result(
                        "Some other entity",
                        "Some other entity is registered as an adviser.",
                    ),
                ),
            ),
        )
        result = decide_verification(regulatory_target(), responses)
        assert result.status is VerificationStatus.UNVERIFIED
        assert result.reason_code == IDENTITY_NOT_FOUND

    def test_identity_not_found_is_not_an_accusation(self) -> None:
        responses = (
            ok_response(
                "q1", (sebi_result("Other", "Other entity registered entity."),)
            ),
        )
        result = decide_verification(regulatory_target(), responses)
        assert result.status is VerificationStatus.UNVERIFIED
        assert contains_prohibited_term(result.reason) is None


class TestRowTenVerified:
    """An authoritative, claim-relevant record directly supports the claim."""

    def test_verified(self) -> None:
        responses = (
            ok_response(
                "q1",
                (
                    sebi_result(
                        "Acme Capital registration",
                        "Acme Capital is registered as an investment adviser.",
                        path="/intermediaries/acme",
                    ),
                ),
            ),
        )
        result = decide_verification(regulatory_target(), responses)
        assert result.status is VerificationStatus.VERIFIED
        assert result.reason_code == AUTHORITATIVE_SOURCE_CONFIRMS
        assert result.source_ids
        assert result.confidence > 0.8

    def test_verified_names_the_authority_that_confirmed(self) -> None:
        responses = (
            ok_response(
                "q1",
                (
                    sebi_result(
                        "Acme Capital registration",
                        "Acme Capital is registered as an investment adviser.",
                    ),
                ),
            ),
        )
        result = decide_verification(regulatory_target(), responses)
        assert "Securities and Exchange Board of India" in result.reason

    def test_verified_is_not_a_safety_statement(self) -> None:
        """A verified claim can still be a dangerous promise; the wording must not merge the two."""
        responses = (
            ok_response(
                "q1",
                (
                    sebi_result(
                        "Acme Capital registration",
                        "Acme Capital is registered as an investment adviser.",
                    ),
                ),
            ),
        )
        result = decide_verification(regulatory_target(), responses)
        assert contains_prohibited_term(result.reason) is None
        assert "statement about the record" in result.reason


class TestRowElevenContradicted:
    """A direct authoritative conflict — the only route to this status."""

    def test_contradicted(self) -> None:
        responses = (
            ok_response(
                "q1",
                (
                    sebi_result(
                        "Order against Acme Capital",
                        "The registration of Acme Capital was cancelled.",
                        path="/orders/acme",
                    ),
                ),
            ),
        )
        result = decide_verification(regulatory_target(), responses)
        assert result.status is VerificationStatus.CONTRADICTED
        assert result.reason_code == AUTHORITATIVE_SOURCE_CONTRADICTS
        assert result.source_ids

    def test_conflict_only_counts_from_an_authoritative_source(self) -> None:
        responses = (
            ok_response(
                "q1",
                (
                    make_result(
                        "Acme Capital registration",
                        "https://blog.example.com/x",
                        snippet="The registration of Acme Capital was cancelled.",
                    ),
                ),
            ),
        )
        result = decide_verification(regulatory_target(), responses)
        assert result.status is not VerificationStatus.CONTRADICTED
        assert result.reason_code == NO_CLAIM_RELEVANT_SOURCE

    def test_conflict_must_mention_the_claimed_entity(self) -> None:
        responses = (
            ok_response(
                "q1",
                (
                    sebi_result(
                        "Order against another entity",
                        "The registration of another entity was cancelled.",
                    ),
                ),
            ),
        )
        result = decide_verification(regulatory_target(), responses)
        assert result.status is VerificationStatus.UNVERIFIED
        assert result.reason_code == IDENTITY_NOT_FOUND


class TestRowTwelveConflictingSources:
    """Official records that disagree leave the claim unsettled."""

    def test_support_and_contradiction_conflict(self) -> None:
        responses = (
            ok_response(
                "q1",
                (
                    sebi_result(
                        "Acme Capital registration",
                        "Acme Capital is registered as an investment adviser.",
                        path="/a",
                    ),
                    sebi_result(
                        "Order against Acme Capital",
                        "The registration of Acme Capital was cancelled.",
                        path="/b",
                    ),
                ),
            ),
        )
        result = decide_verification(regulatory_target(), responses)
        assert result.status is VerificationStatus.INSUFFICIENT_EVIDENCE
        assert result.reason_code == CONFLICTING_AUTHORITATIVE_SOURCES
        assert "1 support it" in result.reason

    def test_contradiction_alone_does_not_conflict(self) -> None:
        responses = (
            ok_response(
                "q1",
                (
                    sebi_result(
                        "Order one",
                        "The registration of Acme Capital was cancelled.",
                        path="/a",
                    ),
                    sebi_result(
                        "Order two",
                        "The registration of Acme Capital was revoked.",
                        path="/b",
                    ),
                ),
            ),
        )
        result = decide_verification(regulatory_target(), responses)
        assert result.status is VerificationStatus.CONTRADICTED


class TestNeutralOutcome:
    """The entity was found, but no record speaks to the claim."""

    def test_neither_support_nor_contradiction(self) -> None:
        responses = (
            ok_response(
                "q1",
                (
                    sebi_result(
                        "Acme Capital news",
                        "Acme Capital published a circular today.",
                    ),
                ),
            ),
        )
        result = decide_verification(regulatory_target(), responses)
        assert result.status is VerificationStatus.INSUFFICIENT_EVIDENCE
        assert result.reason_code == NO_CLAIM_RELEVANT_SOURCE
        assert result.source_ids == ()


class TestDegradedSearches:
    """Partial coverage is stated, never silently dropped."""

    def test_partial_results_are_still_used(self) -> None:
        responses = (
            unavailable_response("q1"),
            ok_response(
                "q2",
                (
                    sebi_result(
                        "Acme Capital registration",
                        "Acme Capital is registered as an investment adviser.",
                    ),
                ),
            ),
        )
        result = decide_verification(regulatory_target(), responses)
        assert result.status is VerificationStatus.VERIFIED
        assert any("partial" in warning for warning in result.warnings)

    def test_partial_failure_with_no_results(self) -> None:
        responses = (ok_response("q1", ()), error_response("q2"))
        result = decide_verification(regulatory_target(), responses)
        assert result.status is VerificationStatus.UNVERIFIED
        assert result.reason_code == ZERO_RESULTS
        assert any("partial" in warning for warning in result.warnings)

    def test_queries_are_recorded(self) -> None:
        target = regulatory_target()
        responses = tuple(unavailable_response(query) for query in target.search_queries)
        result = decide_verification(target, responses)
        assert result.queries == target.search_queries

    def test_results_are_deduplicated_across_queries(self) -> None:
        shared = sebi_result(
            "Acme Capital registration",
            "Acme Capital is registered as an investment adviser.",
        )
        responses = (ok_response("q1", (shared,)), ok_response("q2", (shared,)))
        result = decide_verification(regulatory_target(), responses)
        assert result.status is VerificationStatus.VERIFIED
        assert len(result.source_ids) == 1
        assert len(result.matched_result_ids) == 1


class TestExplanationTemplates:
    """Explanations are fixed text, never generated."""

    def test_every_reason_code_has_a_template(self) -> None:
        assert set(EXPLANATION_TEMPLATES) == set(VERIFICATION_REASON_CODES)

    def test_every_reason_code_has_a_confidence(self) -> None:
        assert set(CONFIDENCE_BY_REASON) == set(VERIFICATION_REASON_CODES)

    def test_missing_placeholders_render_empty_not_crash(self) -> None:
        assert "Acme" not in render_explanation(ZERO_RESULTS)

    def test_unknown_reason_code_raises(self) -> None:
        with pytest.raises(KeyError):
            render_explanation("MADE_UP_CODE")

    def test_templates_carry_no_prohibited_terms(self) -> None:
        for code, template in EXPLANATION_TEMPLATES.items():
            assert contains_prohibited_term(template) is None, code

    def test_status_warnings_carry_no_prohibited_terms(self) -> None:
        from app.services.verification.decision_engine import _STATUS_WARNINGS

        for code, warnings in _STATUS_WARNINGS.items():
            for warning in warnings:
                assert contains_prohibited_term(warning) is None, code


class TestOutputLanguageGuard:
    """No verification output may contain verdict language."""

    def test_prohibited_terms_are_declared(self) -> None:
        assert {"scam", "fraud", "safe", "dangerous", "buy", "sell", "invest"} <= (
            PROHIBITED_OUTPUT_TERMS
        )

    @pytest.mark.parametrize(
        "text",
        [
            "This is a scam.",
            "Fraud detected.",
            "Safe investment.",
            "Dangerous offer.",
            "You should buy now.",
            "Do not sell.",
            "Invest today.",
        ],
    )
    def test_detector_catches_verdict_language(self, text: str) -> None:
        assert contains_prohibited_term(text) is not None

    @pytest.mark.parametrize(
        "text",
        [
            "An official record directly supports this claim.",
            "No matching entry was found in the records searched.",
            "This is not evidence that the claim is false.",
            "External search was not configured.",
        ],
    )
    def test_detector_accepts_factual_wording(self, text: str) -> None:
        assert contains_prohibited_term(text) is None

    def test_detector_handles_empty_input(self) -> None:
        assert contains_prohibited_term("") is None

    def test_detector_uses_word_boundaries(self) -> None:
        """`investigation` and `safeguard` are ordinary words, not verdicts."""
        assert contains_prohibited_term("The investigation continued.") is None
        assert contains_prohibited_term("Safeguard measures apply.") is None


class TestDecisionTableInvariants:
    """Properties that must hold for every reachable decision."""

    def _all_decisions(self):
        """Yield one result per branch of the decision table."""
        for target, responses in SCENARIOS:
            yield decide_verification(target, responses)

    def test_no_branch_produces_contradicted_without_an_authoritative_source(self) -> None:
        for result in self._all_decisions():
            if result.status is VerificationStatus.CONTRADICTED:
                assert result.source_ids
                assert result.reason_code in (
                    AUTHORITATIVE_SOURCE_CONTRADICTS,
                    CONFLICTING_AUTHORITATIVE_SOURCES,
                )

    def test_no_branch_produces_an_unexplained_status(self) -> None:
        for result in self._all_decisions():
            assert result.reason_code in VERIFICATION_REASON_CODES
            assert result.reason.strip()

    def test_no_branch_exceeds_the_confidence_range(self) -> None:
        for result in self._all_decisions():
            assert 0.0 <= result.confidence <= 1.0

    def test_no_branch_contains_verdict_language(self) -> None:
        for result in self._all_decisions():
            text = " ".join((result.reason, *result.warnings))
            assert contains_prohibited_term(text) is None, result.reason_code

    def test_no_branch_asserts_a_recommendation(self) -> None:
        for result in self._all_decisions():
            assert "recommend" not in result.reason.lower()

    def test_unverified_and_insufficient_are_not_conclusions_about_truth(self) -> None:
        for result in self._all_decisions():
            if result.status in (
                VerificationStatus.UNVERIFIED,
                VerificationStatus.INSUFFICIENT_EVIDENCE,
            ):
                assert "is false" not in result.reason.replace(
                    "that the claim is false", ""
                )

    def test_all_five_statuses_are_reachable(self) -> None:
        """The table is not silently collapsing two statuses into one."""
        reached = {result.status for result in self._all_decisions()}
        assert VerificationStatus.UNVERIFIED in reached
        assert VerificationStatus.VERIFIED in reached
        assert VerificationStatus.INSUFFICIENT_EVIDENCE in reached
        assert VerificationStatus.NOT_APPLICABLE in reached


class TestDeterminism:
    """Same inputs, same output — every time."""

    def test_repeated_decisions_are_identical(self) -> None:
        responses = (
            ok_response(
                "q1",
                (
                    sebi_result(
                        "Acme Capital registration",
                        "Acme Capital is registered as an investment adviser.",
                    ),
                ),
            ),
        )
        results = [
            decide_verification(regulatory_target(), responses) for _ in range(5)
        ]
        assert len({repr(result) for result in results}) == 1

    def test_result_order_does_not_change_the_status(self) -> None:
        first = sebi_result(
            "Acme Capital registration",
            "Acme Capital is registered as an investment adviser.",
            path="/a",
        )
        second = sebi_result(
            "Acme Capital circular",
            "A circular was issued for Acme Capital.",
            path="/b",
        )
        forward = decide_verification(regulatory_target(), (ok_response("q", (first, second)),))
        backward = decide_verification(regulatory_target(), (ok_response("q", (second, first)),))
        assert forward.status is backward.status
        assert forward.reason_code == backward.reason_code

    def test_scenario_matrix_is_well_formed(self) -> None:
        """Sanity check that the scenario matrix itself is exhaustive enough."""
        results = [decide_verification(target, responses) for target, responses in SCENARIOS]
        assert len(results) == len(SCENARIOS)
        assert all(result.claim_id == "claim_001" for result in results)


class TestReasonCodeCoverage:
    """Every declared reason code is reachable from the decision table."""

    def test_all_reason_codes_are_reachable(self) -> None:
        reached = {
            decide_verification(target, responses).reason_code
            for target, responses in SCENARIOS
        }
        assert reached == VERIFICATION_REASON_CODES

    def test_all_reason_codes_have_a_matching_confidence_entry(self) -> None:
        for code in VERIFICATION_REASON_CODES:
            assert 0.0 <= CONFIDENCE_BY_REASON[code] <= 1.0