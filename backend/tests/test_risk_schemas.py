"""Tests for the Phase 6 risk schemas.

The risk schemas carry most of Phase 6's guarantees as validation rules rather
than as conventions, so these tests are largely about proving those rules reject
what they are supposed to reject. If a test here fails, the invariant is gone even
if the service still produces plausible-looking numbers.

The invariants under test:

- A factor can never contribute more than its declared weight (the arithmetic
  form of "do not double count"), and never a negative amount.
- A factor absorbed into another must contribute exactly ``0``, and cannot absorb
  into itself.
- An assessment's ``raw_score`` must equal the sum of its factor contributions,
  and its ``risk_score`` must be that sum capped at the ceiling.
- An assessment with no factors must score ``0``, and a score above ``0`` requires
  at least one factor that actually contributed.
- Derived counts can never disagree with the factor list they describe.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.claims import ClaimType
from app.schemas.red_flags import RedFlagCode, Severity
from app.schemas.risk import (
    RED_FLAG_ID_PREFIX,
    RISK_FACTOR_ID_PREFIX,
    RISK_ID_DIGEST_LENGTH,
    RISK_RELEVANT_CLAIM_TYPES,
    RiskAssessment,
    RiskFactor,
    RiskFactorOrigin,
    RiskLevel,
    RiskThresholds,
    RiskWeights,
    VerificationFactorType,
)
from app.schemas.verification import VerificationStatus


def make_weights(**overrides: int) -> RiskWeights:
    """Build a `RiskWeights` with one weight per red-flag code.

    Args:
        **overrides: Replace individual red-flag weights by code name.

    Returns:
        A `RiskWeights` where every code resolves, so a test about scoring is not
        accidentally a test about a missing key.
    """
    weights = {code: 10 for code in RedFlagCode}
    for name, value in overrides.items():
        weights[RedFlagCode[name]] = value
    return RiskWeights(
        red_flag_weights=weights,
        contradicted_claim=overrides.pop("contradicted_claim", 25),
        unverified_claim=8,
        insufficient_evidence=0,
    )


THRESHOLDS = RiskThresholds(medium_max=20, high_max=50, critical_max=90, ceiling=100)


def make_factor(
    *,
    factor_id: str = "rsk_000000000001",
    origin: RiskFactorOrigin = RiskFactorOrigin.RED_FLAG,
    factor_type: RedFlagCode | VerificationFactorType = RedFlagCode.GUARANTEED_RETURN,
    weight: int = 10,
    contribution: int = 10,
    absorbed_into: str | None = None,
    **kwargs: object,
) -> RiskFactor:
    """Build a valid `RiskFactor`, overriding only what a test is about."""
    return RiskFactor(
        id=factor_id,
        origin=origin,
        factor_type=factor_type,
        label="Test factor",
        description="A factor built for a schema test.",
        reason="Built for a schema test.",
        source="red flag rf_000000000001",
        severity=Severity.HIGH,
        weight=weight,
        contribution=contribution,
        absorbed_into=absorbed_into,
        **kwargs,  # type: ignore[arg-type]
    )


def make_assessment(
    factors: tuple[RiskFactor, ...] = (),
    *,
    raw_score: int | None = None,
    risk_score: int | None = None,
    risk_level: RiskLevel = RiskLevel.LOW,
    relevant_claims: int = 0,
    assessed_claims: int = 0,
    evidence_coverage: float = 0.0,
    analysis_completeness: float = 0.0,
) -> RiskAssessment:
    """Build a `RiskAssessment` whose derived fields are consistent by default.

    Args:
        factors: The factors to embed.
        raw_score: Override the raw score. Defaults to the contribution sum.
        risk_score: Override the capped score. Defaults to the raw score.
        risk_level: The band to report.
        relevant_claims: Risk-relevant claim count.
        assessed_claims: Assessed relevant claim count.
        evidence_coverage: Evidence coverage ratio.
        analysis_completeness: Analysis completeness ratio.

    Returns:
        A `RiskAssessment` that passes every cross-field check.
    """
    summed = sum(factor.contribution for factor in factors)
    return RiskAssessment(
        risk_score=summed if risk_score is None else risk_score,
        raw_score=summed if raw_score is None else raw_score,
        risk_level=risk_level,
        factors=factors,
        relevant_claims=relevant_claims,
        assessed_claims=assessed_claims,
        evidence_coverage=evidence_coverage,
        analysis_completeness=analysis_completeness,
        weights=make_weights(),
        thresholds=THRESHOLDS,
    )


# -- RiskWeights ---------------------------------------------------------


class TestRiskWeights:
    def test_every_red_flag_code_resolves_a_weight(self) -> None:
        """A code with no resolvable weight would silently contribute 0."""
        weights = make_weights()
        assert all(weights.weight_for(code) > 0 for code in RedFlagCode)

    def test_weight_for_uses_the_enum_key_not_a_string_key(self) -> None:
        """Regression: a lowercase key once made every red flag weight 0."""
        weights = make_weights(GUARANTEED_RETURN=20)
        assert weights.weight_for(RedFlagCode.GUARANTEED_RETURN) == 20

    def test_unconfigured_code_resolves_zero_rather_than_raising(self) -> None:
        empty = RiskWeights(
            red_flag_weights={},
            contradicted_claim=25,
            unverified_claim=8,
            insufficient_evidence=0,
        )
        assert empty.weight_for(RedFlagCode.SUSPICIOUS_URL) == 0

    @pytest.mark.parametrize(
        ("factor_type", "attribute"),
        [
            (VerificationFactorType.CONTRADICTED_CLAIM, "contradicted_claim"),
            (VerificationFactorType.UNVERIFIED_CLAIM, "unverified_claim"),
            (VerificationFactorType.INSUFFICIENT_EVIDENCE, "insufficient_evidence"),
        ],
    )
    def test_verification_types_resolve_their_own_columns(
        self, factor_type: VerificationFactorType, attribute: str
    ) -> None:
        weights = RiskWeights(
            red_flag_weights={},
            contradicted_claim=25,
            unverified_claim=8,
            insufficient_evidence=0,
        )
        assert weights.weight_for(factor_type) == getattr(weights, attribute)

    def test_insufficient_evidence_weighs_nothing_by_default(self) -> None:
        """Not being able to check something is not a finding (D-006)."""
        assert make_weights().weight_for(
            VerificationFactorType.INSUFFICIENT_EVIDENCE
        ) == 0

    def test_rejects_negative_weights(self) -> None:
        with pytest.raises(ValidationError):
            RiskWeights(
                red_flag_weights={},
                contradicted_claim=-1,
                unverified_claim=0,
                insufficient_evidence=0,
            )

    def test_snapshots_serialise_with_readable_code_keys(self) -> None:
        """A stored report must show which weight applied to which rule."""
        dumped = make_weights().model_dump(mode="json")["red_flag_weights"]
        assert dumped[RedFlagCode.GUARANTEED_RETURN.value] == 10

    def test_survives_a_json_round_trip(self) -> None:
        original = make_weights()
        restored = RiskWeights.model_validate_json(original.model_dump_json())
        assert restored.weight_for(RedFlagCode.GUARANTEED_RETURN) == 10


# -- RiskThresholds ------------------------------------------------------


class TestRiskThresholds:
    def test_accepts_the_default_band_ordering(self) -> None:
        thresholds = RiskThresholds(
            medium_max=20, high_max=50, critical_max=90, ceiling=100
        )
        assert thresholds.ceiling == 100

    @pytest.mark.parametrize(
        ("medium_max", "high_max", "critical_max"),
        [
            (50, 20, 90),
            (20, 20, 90),
            (20, 90, 50),
            (20, 50, 50),
        ],
    )
    def test_rejects_bands_that_are_not_strictly_increasing(
        self, medium_max: int, high_max: int, critical_max: int
    ) -> None:
        """A score must never belong to two levels at once."""
        with pytest.raises(ValidationError):
            RiskThresholds(
                medium_max=medium_max,
                high_max=high_max,
                critical_max=critical_max,
                ceiling=100,
            )

    def test_rejects_a_critical_band_reaching_the_ceiling(self) -> None:
        """Otherwise CRITICAL would be the only score the ceiling could produce."""
        with pytest.raises(ValidationError):
            RiskThresholds(
                medium_max=20, high_max=50, critical_max=100, ceiling=100
            )

    def test_rejects_a_zero_ceiling(self) -> None:
        with pytest.raises(ValidationError):
            RiskThresholds(
                medium_max=0, high_max=50, critical_max=90, ceiling=0
            )


# -- RiskFactor ----------------------------------------------------------


class TestRiskFactor:
    def test_a_valid_factor_is_accepted(self) -> None:
        factor = make_factor()
        assert factor.is_scoring is True
        assert factor.is_evidence_backed is False

    def test_contribution_may_not_exceed_weight(self) -> None:
        """This is 'do not double count' as an arithmetic rule."""
        with pytest.raises(ValidationError, match="must not exceed weight"):
            make_factor(weight=10, contribution=11)

    def test_contribution_may_equal_weight(self) -> None:
        assert make_factor(weight=10, contribution=10).contribution == 10

    def test_contribution_may_be_zero(self) -> None:
        assert make_factor(weight=10, contribution=0).is_scoring is False

    def test_rejects_a_negative_contribution(self) -> None:
        """No factor may reduce risk by contributing a negative amount (D-020)."""
        with pytest.raises(ValidationError):
            make_factor(weight=10, contribution=-5)

    def test_rejects_a_negative_weight(self) -> None:
        with pytest.raises(ValidationError):
            make_factor(weight=-1, contribution=0)

    def test_an_absorbed_factor_must_contribute_zero(self) -> None:
        with pytest.raises(ValidationError, match="must contribute 0"):
            make_factor(
                weight=10, contribution=10, absorbed_into="rsk_0000000000ff"
            )

    def test_rejects_self_absorption(self) -> None:
        with pytest.raises(ValidationError, match="absorbed into itself"):
            make_factor(weight=10, contribution=0, absorbed_into="rsk_000000000001")

    def test_requires_the_rsk_id_prefix(self) -> None:
        """A stable, namespaced id is what makes `absorbed_into` resolvable."""
        with pytest.raises(ValidationError, match="must start with"):
            make_factor(factor_id="factor_1")

    def test_a_verification_factor_reports_its_phase_four_status(self) -> None:
        factor = make_factor(
            origin=RiskFactorOrigin.VERIFICATION,
            factor_type=VerificationFactorType.CONTRADICTED_CLAIM,
        )
        assert factor.verification_status is VerificationStatus.CONTRADICTED

    def test_a_red_flag_factor_has_no_verification_status(self) -> None:
        """Phase 1 did not verify anything, so there is no status to report."""
        assert make_factor().verification_status is None

    def test_every_verification_factor_type_maps_to_a_status(self) -> None:
        for factor_type in VerificationFactorType:
            factor = make_factor(
                origin=RiskFactorOrigin.VERIFICATION, factor_type=factor_type
            )
            assert factor.verification_status is not None

    def test_is_evidence_backed_needs_an_evidence_id(self) -> None:
        assert make_factor(evidence_ids=("ev_000000000001",)).is_evidence_backed
        assert not make_factor(source_ids=("src_1",)).is_evidence_backed

    def test_is_frozen(self) -> None:
        factor = make_factor()
        with pytest.raises(ValidationError):
            factor.contribution = 99  # type: ignore[misc]


# -- RiskAssessment ------------------------------------------------------


class TestRiskAssessment:
    def test_an_empty_assessment_scores_zero(self) -> None:
        assessment = make_assessment()
        assert assessment.risk_score == 0
        assert assessment.risk_level is RiskLevel.LOW
        assert assessment.total_factors == 0

    def test_raw_score_must_equal_the_sum_of_contributions(self) -> None:
        with pytest.raises(ValidationError, match="raw_score must equal"):
            make_assessment((make_factor(),), raw_score=99)

    def test_risk_score_must_be_the_raw_score_capped(self) -> None:
        with pytest.raises(ValidationError, match="capped at the ceiling"):
            make_assessment(
                (make_factor(weight=200, contribution=200),),
                raw_score=200,
                risk_score=200,
            )

    def test_capping_is_recorded_by_keeping_both_numbers(self) -> None:
        """A reader must be able to see that the score was clamped."""
        assessment = make_assessment(
            (make_factor(weight=200, contribution=200),),
            raw_score=200,
            risk_score=100,
            risk_level=RiskLevel.CRITICAL,
        )
        assert (assessment.raw_score, assessment.risk_score) == (200, 100)

    def test_rejects_a_nonzero_score_with_no_factors(self) -> None:
        with pytest.raises(ValidationError, match="zero-factor assessment"):
            make_assessment((), raw_score=0, risk_score=5)

    def test_every_factor_de_duplicated_means_a_zero_score(self) -> None:
        """The degenerate case: nothing was counted, so nothing was found."""
        scoring = make_factor(factor_id="rsk_000000000001", weight=10, contribution=0)
        de_duped = make_factor(
            factor_id="rsk_000000000002",
            weight=10,
            contribution=0,
            absorbed_into=scoring.id,
        )
        assessment = make_assessment((scoring, de_duped))
        assert (assessment.risk_score, assessment.raw_score) == (0, 0)
        assert assessment.scoring_factors == 0

    def test_rejects_assessed_claims_exceeding_relevant_claims(self) -> None:
        with pytest.raises(ValidationError, match="cannot exceed"):
            make_assessment(relevant_claims=1, assessed_claims=2)

    @pytest.mark.parametrize("value", [-0.1, 1.1])
    def test_rejects_out_of_range_ratios(self, value: float) -> None:
        with pytest.raises(ValidationError):
            make_assessment(evidence_coverage=value)
        with pytest.raises(ValidationError):
            make_assessment(analysis_completeness=value)

    def test_derived_counts_match_the_factor_list(self) -> None:
        scoring = make_factor(factor_id="rsk_000000000001", weight=10, contribution=10)
        de_duped = make_factor(
            factor_id="rsk_000000000002",
            weight=10,
            contribution=0,
            absorbed_into=scoring.id,
        )
        backed = make_factor(
            factor_id="rsk_000000000003",
            weight=10,
            contribution=10,
            evidence_ids=("ev_000000000001",),
        )
        uncertain = make_factor(
            factor_id="rsk_000000000004",
            origin=RiskFactorOrigin.VERIFICATION,
            factor_type=VerificationFactorType.INSUFFICIENT_EVIDENCE,
            weight=0,
            contribution=0,
            is_uncertainty=True,
        )
        assessment = make_assessment((scoring, de_duped, backed, uncertain))
        assert assessment.total_factors == 4
        assert assessment.scoring_factors == 2
        assert assessment.deduplicated_factors == 1
        assert assessment.evidence_backed_factors == 1
        assert assessment.uncertainty_factors == 1
        assert assessment.red_flag_factors == 3

    def test_contributing_factors_excludes_de_duplicated_ones(self) -> None:
        scoring = make_factor(factor_id="rsk_000000000001")
        de_duped = make_factor(
            factor_id="rsk_000000000002",
            weight=10,
            contribution=0,
            absorbed_into=scoring.id,
        )
        assessment = make_assessment((scoring, de_duped))
        assert assessment.contributing_factors == (scoring,)

    def test_factor_by_id_resolves_every_emitted_factor(self) -> None:
        scoring = make_factor(factor_id="rsk_000000000001")
        de_duped = make_factor(
            factor_id="rsk_000000000002",
            weight=10,
            contribution=0,
            absorbed_into=scoring.id,
        )
        assessment = make_assessment((scoring, de_duped))
        assert set(assessment.factor_by_id) == {scoring.id, de_duped.id}

    def test_snapshots_the_weights_and_bands_actually_used(self) -> None:
        """A stored report must carry the configuration that produced it."""
        assessment = make_assessment()
        assert assessment.weights.weight_for(RedFlagCode.GUARANTEED_RETURN) == 10
        assert assessment.thresholds.medium_max == 20

    def test_is_frozen(self) -> None:
        assessment = make_assessment()
        with pytest.raises(ValidationError):
            assessment.risk_score = 99  # type: ignore[misc]


# -- Module-level constants ---------------------------------------------


class TestRiskConstants:
    def test_id_prefixes_are_namespaced(self) -> None:
        assert RED_FLAG_ID_PREFIX == "rf_"
        assert RISK_FACTOR_ID_PREFIX == "rsk_"

    def test_digest_length_matches_the_other_phases(self) -> None:
        assert RISK_ID_DIGEST_LENGTH == 12

    def test_risk_relevant_types_exclude_the_non_material_families(self) -> None:
        """An unverifiable opening-hours statement is not a risk indicator."""
        assert ClaimType.COMPANY_CLAIM not in RISK_RELEVANT_CLAIM_TYPES
        assert ClaimType.PRODUCT_CLAIM not in RISK_RELEVANT_CLAIM_TYPES
        assert ClaimType.OTHER not in RISK_RELEVANT_CLAIM_TYPES

    def test_risk_relevant_types_include_the_material_families(self) -> None:
        for claim_type in (
            ClaimType.REGULATORY_STATUS,
            ClaimType.GUARANTEE_CLAIM,
            ClaimType.RETURN_PROMISE,
            ClaimType.PAYMENT_INSTRUCTION,
            ClaimType.WITHDRAWAL_CLAIM,
        ):
            assert claim_type in RISK_RELEVANT_CLAIM_TYPES

    def test_where_the_money_goes_is_material(self) -> None:
        """`PAYMENT_INSTRUCTION` is relevant even though Phase 2 may not verify it."""
        assert ClaimType.PAYMENT_INSTRUCTION in RISK_RELEVANT_CLAIM_TYPES
