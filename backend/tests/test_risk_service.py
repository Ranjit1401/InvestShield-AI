"""End-to-end tests for `RiskService`.

These are the tests that describe what the product actually does, rather than how
any one stage works. They feed a `RiskService` the four inputs a real investigation
produces and assert on the assessment a reader would be shown.

The behaviours worth stating as a contract, each tested below:

- **One signal scores once.** The SEBI-approval scenario, end to end: Phase 1 fires
  a red flag, Phase 2 extracts a claim over the same words, Phase 4 contradicts it
  and Phase 5 attaches a register page. The score reflects that *once*.
- **No finding is not a clean bill of health.** An empty investigation scores 0
  with no factors, and its warnings say the score is not a verdict.
- **The tool cannot accuse because it could not look.** With search unavailable,
  the score is 0, not elevated.
- **Every number is derived.** Counts, score, level and ratios always agree with
  the factor list beneath them.
- **Uncertainty is visible but not scored.** A claim nobody could check appears in
  the breakdown and lowers `analysis_completeness` without moving the score.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.schemas.claims import ClaimType
from app.schemas.red_flags import RedFlagCode
from app.schemas.risk import (
    RiskAssessment,
    RiskFactorOrigin,
    RiskLevel,
    VerificationFactorType,
)
from app.schemas.verification import VerificationStatus
from app.services.risk import (
    CLAIMS_CONTRADICTED,
    CLAIMS_NOT_CONFIRMED,
    SCORE_NOT_A_PROBABILITY,
    SEARCH_DATA_UNAVAILABLE,
    SIGNALS_DEDUPLICATED,
    RiskService,
    build_risk_service,
    weights_from_settings,
)
from tests.risk_factories import (
    contradicted,
    evidence_for,
    make_flag,
    make_placed_claim,
    not_applicable,
    unsearchable,
    unverified,
    verified,
)

WEIGHTS = weights_from_settings(Settings())


@pytest.fixture
def service() -> RiskService:
    """A service bound to the default configuration."""
    return RiskService()


def sebi_scenario() -> tuple:  # type: ignore[no-untyped-def]
    """One underlying signal, noticed by four stages.

    Content says "SEBI approved" at offset 0. Phase 1 fires
    `FAKE_REGULATORY_CLAIM` over those words, Phase 2 extracts a
    `REGULATORY_STATUS` claim over the same span, Phase 4 contradicts it, and
    Phase 5 attaches the register page that does the contradicting.
    """
    flag = make_flag(
        RedFlagCode.FAKE_REGULATORY_CLAIM,
        start=0,
        end=13,
        matched_text="SEBI approved",
    )
    claim = make_placed_claim(
        "SEBI approved", ClaimType.REGULATORY_STATUS, claim_id="claim_001", start=0
    )
    result = contradicted("claim_001", claim_type=ClaimType.REGULATORY_STATUS)
    evidence = evidence_for("claim_001")
    return (flag,), (claim,), (result,), (evidence,)


def scored_content(assessment: RiskAssessment) -> tuple:
    """Return an assessment with the wall-clock timestamp removed.

    `assessed_at` is the one field that legitimately differs between two runs over
    identical input, and it is deliberately excluded from every id. Comparing the
    rest is what determinism actually means here.
    """
    return assessment.model_dump(mode="json", exclude={"assessed_at"})


# -- The empty investigation --------------------------------------------


class TestEmptyAssessment:
    def test_no_inputs_scores_zero(self, service: RiskService) -> None:
        """The correct answer for "nothing was found" — not a clean bill of health."""
        assessment = service.assess()
        assert assessment.risk_score == 0
        assert assessment.risk_level is RiskLevel.LOW
        assert assessment.factors == ()

    def test_no_factors_means_no_coverage_claims(self, service: RiskService) -> None:
        assessment = service.assess()
        assert assessment.relevant_claims == 0
        assert assessment.assessed_claims == 0
        assert assessment.evidence_coverage == 0.0

    def test_completeness_is_zero_when_nothing_was_extracted(
        self, service: RiskService
    ) -> None:
        """An empty run did not do the work, and must not claim to have."""
        assert service.assess().analysis_completeness == 0.0

    def test_always_carries_the_standing_caveat(self, service: RiskService) -> None:
        """No report may present a score without its meaning attached."""
        assert service.assess().warnings[-1] == SCORE_NOT_A_PROBABILITY


# -- The headline scenario ----------------------------------------------


class TestOneSignalScoresOnce:
    def test_the_score_reflects_the_signal_exactly_once(
        self, service: RiskService
    ) -> None:
        """25 from the red flag, not 25 + 25 from the red flag and the claim."""
        assessment = service.assess(*sebi_scenario())
        primary = next(
            f for f in assessment.factors if f.origin is RiskFactorOrigin.RED_FLAG
        )
        assert assessment.risk_score == primary.weight
        assert primary.weight == WEIGHTS.weight_for(RedFlagCode.FAKE_REGULATORY_CLAIM)

    def test_the_contradicted_claim_is_retained_at_zero(
        self, service: RiskService
    ) -> None:
        """The breakdown still shows the claim was contradicted."""
        assessment = service.assess(*sebi_scenario())
        absorbed = next(f for f in assessment.factors if f.absorbed_into)
        assert absorbed.factor_type is VerificationFactorType.CONTRADICTED_CLAIM
        assert absorbed.contribution == 0
        assert absorbed.is_scoring is False

    def test_the_primary_records_the_contradiction_as_context(
        self, service: RiskService
    ) -> None:
        assessment = service.assess(*sebi_scenario())
        primary = next(
            f for f in assessment.factors if f.origin is RiskFactorOrigin.RED_FLAG
        )
        assert VerificationStatus.CONTRADICTED in primary.verification_statuses

    def test_the_primary_cites_the_retrieved_document(self, service: RiskService) -> None:
        assessment = service.assess(*sebi_scenario())
        primary = next(
            f for f in assessment.factors if f.origin is RiskFactorOrigin.RED_FLAG
        )
        assert primary.evidence_ids
        assert primary.source_ids
        assert primary.is_evidence_backed is True

    def test_no_factor_contributes_more_than_its_weight(
        self, service: RiskService
    ) -> None:
        """The arithmetic form of "one signal, one contribution"."""
        assessment = service.assess(*sebi_scenario())
        for factor in assessment.factors:
            assert factor.contribution <= factor.weight

    def test_the_contradiction_is_still_reported_as_a_warning(
        self, service: RiskService
    ) -> None:
        """De-duplication must not silence the most material finding."""
        assert CLAIMS_CONTRADICTED in service.assess(*sebi_scenario()).warnings

    def test_de_duplication_is_disclosed(self, service: RiskService) -> None:
        assert SIGNALS_DEDUPLICATED in service.assess(*sebi_scenario()).warnings

    def test_every_score_and_level_agree(self, service: RiskService) -> None:
        assessment = service.assess(*sebi_scenario())
        assert assessment.risk_score == sum(f.contribution for f in assessment.factors)
        assert assessment.raw_score == assessment.risk_score


# -- Scores add up ------------------------------------------------------


class TestScoringTotals:
    def test_distinct_signals_all_count(self, service: RiskService) -> None:
        """Two different signals are two factors, not one."""
        flags = (
            make_flag(RedFlagCode.GUARANTEED_RETURN, start=0, end=40),
            make_flag(RedFlagCode.URGENCY_PRESSURE, start=100, end=140),
        )
        assessment = service.assess(flags)
        assert assessment.risk_score == (
            WEIGHTS.weight_for(RedFlagCode.GUARANTEED_RETURN)
            + WEIGHTS.weight_for(RedFlagCode.URGENCY_PRESSURE)
        )

    def test_a_duplicate_red_flag_is_counted_once(self, service: RiskService) -> None:
        """The same rule firing twice is one signal, not two."""
        flag = make_flag()
        assessment = service.assess((flag, flag, flag))
        assert assessment.total_factors == 1
        assert assessment.risk_score == WEIGHTS.weight_for(RedFlagCode.GUARANTEED_RETURN)

    def test_a_duplicate_verification_result_is_counted_once(
        self, service: RiskService
    ) -> None:
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        results = (contradicted("claim_001"),) * 3
        assessment = service.assess((), (claim,), results)
        assert assessment.total_factors == 1

    def test_the_score_is_capped_rather_than_scaled(self, service: RiskService) -> None:
        """Two different documents far past the ceiling must be comparable."""
        flags = tuple(
            make_flag(code, start=index * 100, end=index * 100 + 40)
            for index, code in enumerate(RedFlagCode)
        )
        assessment = service.assess(flags)
        assert assessment.risk_score == assessment.thresholds.ceiling
        assert assessment.raw_score > assessment.risk_score

    def test_the_level_follows_the_capped_score(self, service: RiskService) -> None:
        flags = tuple(
            make_flag(code, start=index * 100, end=index * 100 + 40)
            for index, code in enumerate(RedFlagCode)
        )
        assert service.assess(flags).risk_level is RiskLevel.CRITICAL

    def test_a_verified_claim_does_not_lower_the_score(
        self, service: RiskService
    ) -> None:
        """Confirmation is not a risk reduction; nothing nets off (D-020)."""
        flag = make_flag(RedFlagCode.GUARANTEED_RETURN)
        claim = make_placed_claim(
            "guaranteed returns", ClaimType.REGULATORY_STATUS, claim_id="claim_001"
        )
        with_verified = service.assess((flag,), (claim,), (verified("claim_001"),))
        without = service.assess((flag,), (claim,))
        assert with_verified.risk_score == without.risk_score

    def test_nothing_can_push_the_score_below_zero(self, service: RiskService) -> None:
        assert service.assess().risk_score >= 0


# -- Absence of evidence is not risk ------------------------------------


class TestAbsenceOfEvidenceIsNotRisk:
    def test_an_unsearchable_claim_scores_nothing(self, service: RiskService) -> None:
        """The tool could not look. That is not a finding about the content."""
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        assessment = service.assess((), (claim,), (unsearchable("claim_001"),))
        assert assessment.risk_score == 0

    def test_the_gap_is_still_visible_in_the_breakdown(
        self, service: RiskService
    ) -> None:
        """Zero contribution, but present and labelled."""
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        assessment = service.assess((), (claim,), (unsearchable("claim_001"),))
        assert assessment.total_factors == 1
        assert assessment.uncertainty_factors == 1
        assert assessment.factors[0].is_uncertainty is True

    def test_a_failed_search_is_disclosed(self, service: RiskService) -> None:
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        assessment = service.assess((), (claim,), (unsearchable("claim_001"),))
        assert SEARCH_DATA_UNAVAILABLE in assessment.warnings

    def test_an_empty_register_is_disclosed_differently(
        self, service: RiskService
    ) -> None:
        """"We could not look" and "we looked and it was not there" differ in kind.

        The second is an observation about the public record, and a reader who
        conflated the two would be told less than the investigation actually
        established.
        """
        from app.services.risk import REGISTER_NO_MATCH
        from tests.evidence_factories import zero_results_verification

        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        assessment = service.assess(
            (), (claim,), (zero_results_verification("claim_001"),)
        )
        assert REGISTER_NO_MATCH in assessment.warnings
        assert SEARCH_DATA_UNAVAILABLE not in assessment.warnings

    def test_an_offline_run_does_not_accuse(self, service: RiskService) -> None:
        """The headline failure mode: highest risk exactly when least informed.

        Every material claim unverifiable, and the score must be 0 — not elevated
        by the very lack of evidence that is supposed to lower confidence.
        """
        claims = tuple(
            make_placed_claim(
                f"claim {index}",
                ClaimType.REGULATORY_STATUS,
                claim_id=f"claim_{index}",
            )
            for index in range(5)
        )
        results = tuple(unsearchable(f"claim_{index}") for index in range(5))
        assessment = service.assess((), claims, results)
        assert assessment.risk_score == 0
        assert assessment.risk_level is RiskLevel.LOW

    def test_a_completed_search_that_found_nothing_does_score(
        self, service: RiskService
    ) -> None:
        """The contrast: we looked, and the record does not establish the claim."""
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        assessment = service.assess((), (claim,), (unverified("claim_001"),))
        assert assessment.risk_score == WEIGHTS.unverified_claim

    def test_a_not_applicable_claim_produces_nothing(self, service: RiskService) -> None:
        """No record was ever looked up, so there is nothing to assess."""
        claim = make_placed_claim("Open Monday", ClaimType.COMPANY_CLAIM)
        assessment = service.assess((), (claim,), (not_applicable("claim_001"),))
        assert assessment.total_factors == 0
        assert assessment.risk_score == 0

    def test_a_non_material_claim_produces_nothing(self, service: RiskService) -> None:
        """A contradicted opening-hours statement is not a risk indicator."""
        claim = make_placed_claim("Open Monday", ClaimType.COMPANY_CLAIM)
        assessment = service.assess((), (claim,), (contradicted("claim_001"),))
        assert assessment.total_factors == 0

    def test_no_search_produced_means_lower_completeness(
        self, service: RiskService
    ) -> None:
        """Uncertainty is expressed here, not in the score (D-006)."""
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        offline = service.assess((), (claim,), (unsearchable("claim_001"),))
        online = service.assess(
            (),
            (claim,),
            (unverified("claim_001"),),
            (evidence_for("claim_001"),),
        )
        assert offline.analysis_completeness < online.analysis_completeness
        assert offline.risk_score == 0


# -- Coverage and completeness -----------------------------------------


class TestCoverageAndCompleteness:
    def test_evidence_coverage_is_one_when_every_claim_has_evidence(
        self, service: RiskService
    ) -> None:
        claims = (
            make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS, claim_id="c1"),
            make_placed_claim("SEBI approved", ClaimType.GUARANTEE_CLAIM, claim_id="c2"),
        )
        results = (contradicted("c1"), contradicted("c2"))
        evidence = (evidence_for("c1"), evidence_for("c2", url="https://x.gov/r"))
        assert service.assess((), claims, results, evidence).evidence_coverage == 1.0

    def test_evidence_coverage_is_zero_when_nothing_was_retrieved(
        self, service: RiskService
    ) -> None:
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        assessment = service.assess((), (claim,), (contradicted("claim_001"),))
        assert assessment.evidence_coverage == 0.0

    def test_evidence_coverage_never_divides_by_zero(
        self, service: RiskService
    ) -> None:
        assert service.assess((), (), ()).evidence_coverage == 0.0

    def test_evidence_coverage_stays_within_range(self, service: RiskService) -> None:
        for count in range(4):
            claims = tuple(
                make_placed_claim(
                    f"claim {index}",
                    ClaimType.REGULATORY_STATUS,
                    claim_id=f"c{index}",
                )
                for index in range(count)
            )
            results = tuple(contradicted(f"c{index}") for index in range(count))
            coverage = service.assess((), claims, results).evidence_coverage
            assert 0.0 <= coverage <= 1.0

    def test_relevant_claims_excludes_non_material_types(
        self, service: RiskService
    ) -> None:
        claims = (
            make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS, claim_id="c1"),
            make_placed_claim("Open Monday", ClaimType.COMPANY_CLAIM, claim_id="c2"),
        )
        assert service.assess(claims=claims).relevant_claims == 1

    def test_assessed_claims_counts_only_relevant_claims(
        self, service: RiskService
    ) -> None:
        claims = (
            make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS, claim_id="c1"),
            make_placed_claim("Open Monday", ClaimType.COMPANY_CLAIM, claim_id="c2"),
        )
        results = (contradicted("c1"), contradicted("c2"))
        assessment = service.assess((), claims, results)
        assert (assessment.relevant_claims, assessment.assessed_claims) == (1, 1)

    def test_an_unassessed_claim_lowers_completeness(
        self, service: RiskService
    ) -> None:
        claims = (
            make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS, claim_id="c1"),
            make_placed_claim("guaranteed", ClaimType.GUARANTEE_CLAIM, claim_id="c2"),
        )
        assessment = service.assess((), claims, (contradicted("c1"),))
        assert assessment.assessed_claims == 1
        assert assessment.analysis_completeness < 1.0

    def test_both_ratios_are_bounded(self, service: RiskService) -> None:
        assessment = service.assess(*sebi_scenario())
        assert 0.0 <= assessment.evidence_coverage <= 1.0
        assert 0.0 <= assessment.analysis_completeness <= 1.0


# -- Provenance and reporting ------------------------------------------


class TestProvenance:
    def test_every_factor_carries_a_source_phrase(self, service: RiskService) -> None:
        for factor in service.assess(*sebi_scenario()).factors:
            assert factor.source

    def test_every_factor_cites_something(self, service: RiskService) -> None:
        """A factor that cites no red flag, claim or result is untraceable."""
        for factor in service.assess(*sebi_scenario()).factors:
            assert factor.red_flag_ids or factor.claim_ids

    def test_the_configuration_used_is_snapshotted_onto_the_assessment(
        self, service: RiskService
    ) -> None:
        """A stored report must carry the weights and bands that produced it."""
        assessment = service.assess(*sebi_scenario())
        assert assessment.weights.weight_for(
            RedFlagCode.FAKE_REGULATORY_CLAIM
        ) == WEIGHTS.weight_for(RedFlagCode.FAKE_REGULATORY_CLAIM)
        assert assessment.thresholds.medium_max == 20

    def test_a_reconfigured_service_scores_differently(self) -> None:
        """The weights are genuinely configuration, not hard-coded."""
        flag = make_flag(RedFlagCode.GUARANTEED_RETURN)
        default = RiskService().assess((flag,)).risk_score
        reconfigured = RiskService(
            Settings(risk_weight_guaranteed_return=45)
        ).assess((flag,)).risk_score
        assert default != reconfigured

    def test_derived_counts_match_the_factor_list(self, service: RiskService) -> None:
        assessment = service.assess(*sebi_scenario())
        assert assessment.total_factors == len(assessment.factors)
        assert assessment.scoring_factors == sum(
            1 for f in assessment.factors if f.is_scoring
        )
        assert assessment.deduplicated_factors == sum(
            1 for f in assessment.factors if f.absorbed_into
        )

    def test_factor_ids_are_unique_within_an_assessment(self, service: RiskService) -> None:
        assessment = service.assess(*sebi_scenario())
        assert len(assessment.factor_by_id) == assessment.total_factors


# -- Warnings -----------------------------------------------------------


class TestWarnings:
    def test_the_standing_caveat_is_always_last(self, service: RiskService) -> None:
        for assessment in (
            service.assess(),
            service.assess(*sebi_scenario()),
        ):
            assert assessment.warnings[-1] == SCORE_NOT_A_PROBABILITY

    def test_warnings_are_de_duplicated(self, service: RiskService) -> None:
        flags = (
            make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13),
            make_flag(RedFlagCode.UNVERIFIED_ADVISER, start=100, end=140),
        )
        assessment = service.assess(flags)
        assert len(assessment.warnings) == len(set(assessment.warnings))

    def test_an_unconfirmed_material_claim_is_disclosed(
        self, service: RiskService
    ) -> None:
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        results = (unverified("claim_001"),)
        assert CLAIMS_NOT_CONFIRMED in service.assess((), (claim,), results).warnings

    def test_a_clean_investigation_reports_no_specific_findings(
        self, service: RiskService
    ) -> None:
        """Nothing found means only the standing caveat, not a reassurance."""
        assert service.assess().warnings == (SCORE_NOT_A_PROBABILITY,)

    def test_evidence_availability_never_raises_the_score(
        self, service: RiskService
    ) -> None:
        """More evidence changes the breakdown, not the weight of a finding."""
        claims = (make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS),)
        results = (contradicted("claim_001"),)
        without = service.assess((), claims, results)
        with_evidence = service.assess((), claims, results, (evidence_for("claim_001"),))
        assert with_evidence.risk_score == without.risk_score
        assert with_evidence.evidence_coverage > without.evidence_coverage


# -- Determinism and wiring --------------------------------------------


class TestDeterminismAndWiring:
    def test_identical_inputs_give_an_identical_assessment(
        self, service: RiskService
    ) -> None:
        """Same content, same score, same ids, same factors — bar the timestamp."""
        assert scored_content(service.assess(*sebi_scenario())) == scored_content(
            service.assess(*sebi_scenario())
        )

    def test_factor_ids_are_derived_not_sequential(self, service: RiskService) -> None:
        """A stable digest keeps a re-run comparable to a stored report."""
        ids = [f.id for f in service.assess(*sebi_scenario()).factors]
        assert all(len(factor_id) == 16 for factor_id in ids)

    def test_assess_batch_matches_assess(self) -> None:
        """Phase 7 will call the batch shape; it must not diverge."""
        flags, claims, results, evidence = sebi_scenario()
        assert scored_content(
            RiskService().assess_batch(claims, results, evidence, flags)
        ) == scored_content(RiskService().assess(flags, claims, results, evidence))

    def test_a_service_holds_no_state_between_assessments(
        self, service: RiskService
    ) -> None:
        """One instance, many investigations, no bleed."""
        service.assess(*sebi_scenario())
        assert service.assess().risk_score == 0

    def test_the_builder_produces_an_equivalent_service(self) -> None:
        assert scored_content(
            build_risk_service().assess(*sebi_scenario())
        ) == scored_content(RiskService().assess(*sebi_scenario()))

    def test_the_assessment_serialises_and_reparses(self, service: RiskService) -> None:
        """Phase 7 will persist this; the round trip must be lossless."""
        original = service.assess(*sebi_scenario())
        restored = RiskAssessment.model_validate_json(original.model_dump_json())
        assert restored.risk_score == original.risk_score
        assert len(restored.factors) == len(original.factors)
