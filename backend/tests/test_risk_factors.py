"""Tests for Phase 6 risk factor construction.

The most important behaviour in this file is the mapping from a Phase 4
`VerificationStatus` to a `VerificationFactorType` — specifically which statuses
produce **no** factor at all, and which `UNVERIFIED` reasons are excluded.

The distinction under test is the difference between *we looked and found nothing*
and *we could not look*. Only the first is a finding. A tool that cannot reach
the internet must not report the absence of results as a risk signal, because the
alternative is a system whose score is highest exactly when it is least informed.
That is the failure mode D-006 exists to prevent, and the reason-code gate in
`factor_type_for` is where it is caught.

Also tested here: stable id derivation, that factor wording is templated rather
than generated, and that a red-flag factor carries Phase 1's own text unchanged.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.schemas.claims import ClaimType
from app.schemas.red_flags import RedFlagCode, Severity
from app.schemas.risk import (
    RED_FLAG_ID_PREFIX,
    RISK_FACTOR_ID_PREFIX,
    RiskFactorOrigin,
    VerificationFactorType,
)
from app.schemas.verification import (
    AUTHORITATIVE_SOURCE_CONTRADICTS,
    AUTHORITATIVE_SOURCE_CONFIRMS,
    IDENTITY_AMBIGUOUS,
    IDENTITY_NOT_FOUND,
    NO_CLAIM_RELEVANT_SOURCE,
    NO_CONFIRMATION_FOUND,
    NO_QUERY_BUILT,
    NOT_A_FACTUAL_CLAIM,
    SEARCH_FAILED,
    SEARCH_UNAVAILABLE,
    ZERO_RESULTS,
    VerificationStatus,
)
from app.services.risk import (
    claim_is_risk_relevant,
    factor_type_for,
    factors_from_red_flags,
    factors_from_verification,
    red_flag_id_for,
    risk_factor_id_for,
    severity_for_factor,
    weights_from_settings,
)
from tests.risk_factories import (
    contradicted,
    make_flag,
    make_placed_claim,
    not_applicable,
    unsearchable,
    unverified,
    unverified_because_search_never_ran,
    verified,
)
from tests.verification_factories import make_claim


@pytest.fixture
def weights():  # type: ignore[no-untyped-def]
    """The configured weight snapshot."""
    return weights_from_settings(Settings())


# -- Id derivation -------------------------------------------------------


class TestIdDerivation:
    def test_red_flag_ids_use_the_rf_prefix(self) -> None:
        assert red_flag_id_for(make_flag()).startswith(RED_FLAG_ID_PREFIX)

    def test_the_same_flag_always_yields_the_same_id(self) -> None:
        assert red_flag_id_for(make_flag()) == red_flag_id_for(make_flag())

    def test_flags_firing_on_different_spans_get_different_ids(self) -> None:
        first = make_flag(start=0, end=20)
        second = make_flag(start=200, end=220)
        assert red_flag_id_for(first) != red_flag_id_for(second)

    def test_different_rules_get_different_ids(self) -> None:
        """A guaranteed return and a fake regulatory claim are two signals."""
        first = make_flag(RedFlagCode.GUARANTEED_RETURN)
        second = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM)
        assert red_flag_id_for(first) != red_flag_id_for(second)

    def test_factor_ids_use_the_rsk_prefix(self) -> None:
        factor_id = risk_factor_id_for(
            RiskFactorOrigin.RED_FLAG, RedFlagCode.GUARANTEED_RETURN, ("rf_1",)
        )
        assert factor_id.startswith(RISK_FACTOR_ID_PREFIX)

    def test_the_same_signal_always_yields_the_same_factor_id(self) -> None:
        args = (RiskFactorOrigin.VERIFICATION, VerificationFactorType.UNVERIFIED_CLAIM)
        assert risk_factor_id_for(*args, ("claim_1",)) == risk_factor_id_for(
            *args, ("claim_1",)
        )

    def test_a_different_origin_is_a_different_factor(self) -> None:
        """The same signal seen by two stages must not collapse into one id."""
        red = risk_factor_id_for(
            RiskFactorOrigin.RED_FLAG, RedFlagCode.GUARANTEED_RETURN, ("x",)
        )
        verification = risk_factor_id_for(
            RiskFactorOrigin.VERIFICATION,
            VerificationFactorType.UNVERIFIED_CLAIM,
            ("x",),
        )
        assert red != verification

    def test_the_parts_are_separated_so_digests_cannot_collide(self) -> None:
        """`("ab", "c")` and `("a", "bc")` must not hash alike."""
        first = risk_factor_id_for(
            RiskFactorOrigin.VERIFICATION, VerificationFactorType.UNVERIFIED_CLAIM,
            ("ab", "c"),
        )
        second = risk_factor_id_for(
            RiskFactorOrigin.VERIFICATION, VerificationFactorType.UNVERIFIED_CLAIM,
            ("a", "bc"),
        )
        assert first != second


# -- Status to factor type mapping --------------------------------------


class TestFactorTypeFor:
    @pytest.mark.parametrize(
        "status",
        [
            VerificationStatus.VERIFIED,
            VerificationStatus.NOT_APPLICABLE,
        ],
    )
    def test_a_confirmation_or_a_non_lookup_produces_no_factor(
        self, status: VerificationStatus
    ) -> None:
        """Neither is a risk indicator, and neither may reduce the score (D-020)."""
        if status is VerificationStatus.VERIFIED:
            result = verified()
        else:
            result = not_applicable()
        assert factor_type_for(result) is None

    def test_a_contradiction_produces_a_contradicted_factor(self) -> None:
        assert factor_type_for(contradicted()) is (
            VerificationFactorType.CONTRADICTED_CLAIM
        )

    def test_conflicting_authorities_still_produce_one_contradicted_factor(self) -> None:
        """Two authorities disagreeing is one signal, not two."""
        from tests.evidence_factories import contradictory_verification

        single = contradicted("claim_001")
        conflicting = contradictory_verification("claim_001", conflicting=True)
        assert conflicting.reason_code == "CONFLICTING_AUTHORITATIVE_SOURCES"
        assert factor_type_for(single) is factor_type_for(conflicting)

    @pytest.mark.parametrize(
        "reason_code",
        [NO_CONFIRMATION_FOUND, IDENTITY_NOT_FOUND, IDENTITY_AMBIGUOUS],
    )
    def test_a_completed_search_that_found_nothing_produces_a_factor(
        self, reason_code: str
    ) -> None:
        """We looked, and the public record does not establish the claim."""
        result = unverified()
        from app.schemas.verification import VerificationResult

        rebuilt = VerificationResult(
            claim_id=result.claim_id,
            status=VerificationStatus.UNVERIFIED,
            reason="No confirming source was found.",
            reason_code=reason_code,
            confidence=0.1,
        )
        assert factor_type_for(rebuilt) is VerificationFactorType.UNVERIFIED_CLAIM

    @pytest.mark.parametrize(
        "reason_code",
        [
            SEARCH_UNAVAILABLE,
            SEARCH_FAILED,
            ZERO_RESULTS,
            NO_QUERY_BUILT,
            NO_CLAIM_RELEVANT_SOURCE,
        ],
    )
    def test_an_incomplete_search_produces_no_unverified_factor(
        self, reason_code: str
    ) -> None:
        """Not being able to look is not the same as looking and finding nothing.

        The single most important test in this file. `VerificationStatus` is
        permissive about which reason codes may pair with `UNVERIFIED`, so this
        combination is representable even though Phase 4's engine does not emit
        it — and it is exactly what would turn an offline run into an accusation.
        """
        from app.schemas.verification import VerificationResult

        result = VerificationResult(
            claim_id="claim_001",
            status=VerificationStatus.UNVERIFIED,
            reason="External web search is not configured.",
            reason_code=reason_code,
            confidence=0.0,
        )
        assert factor_type_for(result) is None

    def test_an_unverified_result_whose_search_never_ran_scores_nothing(self) -> None:
        assert factor_type_for(unverified_because_search_never_ran()) is None

    def test_insufficient_evidence_produces_the_uncertainty_factor(self) -> None:
        """It is kept, at weight 0, so the gap is visible rather than hidden."""
        assert factor_type_for(unsearchable()) is (
            VerificationFactorType.INSUFFICIENT_EVIDENCE
        )

    def test_insufficient_evidence_is_produced_whichever_reason_is_given(self) -> None:
        """Every route to that status means the same thing: no conclusion."""
        from app.schemas.verification import VerificationResult

        for reason_code in (SEARCH_UNAVAILABLE, ZERO_RESULTS, NO_CLAIM_RELEVANT_SOURCE):
            result = VerificationResult(
                claim_id="claim_001",
                status=VerificationStatus.INSUFFICIENT_EVIDENCE,
                reason="Verification could not be completed.",
                reason_code=reason_code,
                confidence=0.0,
            )
            assert factor_type_for(result) is VerificationFactorType.INSUFFICIENT_EVIDENCE

    def test_the_mapping_covers_every_status(self) -> None:
        """No status may be silently unhandled by a future edit."""
        from app.schemas.verification import VerificationResult

        for status in VerificationStatus:
            reasons = {
                VerificationStatus.VERIFIED: AUTHORITATIVE_SOURCE_CONFIRMS,
                VerificationStatus.CONTRADICTED: AUTHORITATIVE_SOURCE_CONTRADICTS,
                VerificationStatus.UNVERIFIED: NO_CONFIRMATION_FOUND,
                VerificationStatus.INSUFFICIENT_EVIDENCE: SEARCH_UNAVAILABLE,
                VerificationStatus.NOT_APPLICABLE: NOT_A_FACTUAL_CLAIM,
            }
            result = VerificationResult(
                claim_id="claim_001",
                status=status,
                reason="Test.",
                reason_code=reasons[status],
                confidence=0.5,
                source_ids=("src_1",) if status in (
                    VerificationStatus.VERIFIED,
                    VerificationStatus.CONTRADICTED,
                ) else (),
            )
            factor_type_for(result)


# -- Severity mapping ----------------------------------------------------


class TestSeverityForFactor:
    def test_a_contradiction_is_critical(self) -> None:
        """The only factor backed by a direct authoritative conflict."""
        assert severity_for_factor(
            VerificationFactorType.CONTRADICTED_CLAIM
        ) is Severity.CRITICAL

    @pytest.mark.parametrize(
        "factor_type",
        [
            VerificationFactorType.UNVERIFIED_CLAIM,
            VerificationFactorType.INSUFFICIENT_EVIDENCE,
        ],
    )
    def test_uncertainty_is_never_more_than_medium(
        self, factor_type: VerificationFactorType
    ) -> None:
        """Uncertainty is not wrongdoing, so it cannot outrank a real finding."""
        assert severity_for_factor(factor_type) in (Severity.LOW, Severity.MEDIUM)

    def test_uncertainty_ranks_below_contradiction(self) -> None:
        order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}
        assert order[severity_for_factor(VerificationFactorType.CONTRADICTED_CLAIM)] < order[
            severity_for_factor(VerificationFactorType.UNVERIFIED_CLAIM)
        ]


# -- Red-flag factors ----------------------------------------------------


class TestFactorsFromRedFlags:
    def test_builds_one_factor_per_flag(self, weights) -> None:  # type: ignore[no-untyped-def]
        flags = (
            make_flag(),
            make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=100, end=140),
        )
        assert len(factors_from_red_flags(flags, weights)) == 2

    def test_a_factor_reuses_phase_ones_own_text(self, weights) -> None:  # type: ignore[no-untyped-def]
        """Phase 1's wording is already audited; Phase 6 must not reword it."""
        flag = make_flag()
        factor = factors_from_red_flags((flag,), weights)[0]
        assert factor.label == flag.name
        assert factor.description == flag.description
        assert factor.reason == flag.rule_reason
        assert factor.severity is flag.severity

    def test_a_factor_contributes_its_configured_weight(self, weights) -> None:  # type: ignore[no-untyped-def]
        factor = factors_from_red_flags((make_flag(),), weights)[0]
        assert factor.contribution == factor.weight
        assert factor.weight == weights.weight_for(RedFlagCode.GUARANTEED_RETURN)

    def test_a_factor_cites_its_derived_red_flag_id(self, weights) -> None:  # type: ignore[no-untyped-def]
        flag = make_flag()
        factor = factors_from_red_flags((flag,), weights)[0]
        assert factor.red_flag_ids == (red_flag_id_for(flag),)

    def test_the_source_phrase_names_the_red_flag(self, weights) -> None:  # type: ignore[no-untyped-def]
        flag = make_flag()
        factor = factors_from_red_flags((flag,), weights)[0]
        assert factor.source == f"red flag {red_flag_id_for(flag)}"

    def test_a_red_flag_factor_is_never_uncertainty(self, weights) -> None:  # type: ignore[no-untyped-def]
        """Phase 1 found something. That is a finding, not a gap."""
        assert factors_from_red_flags((make_flag(),), weights)[0].is_uncertainty is False

    def test_no_flags_produces_no_factors(self, weights) -> None:  # type: ignore[no-untyped-def]
        assert factors_from_red_flags((), weights) == ()

    def test_output_is_deterministic(self, weights) -> None:  # type: ignore[no-untyped-def]
        flags = (
            make_flag(),
            make_flag(RedFlagCode.URGENCY_PRESSURE, start=100, end=140),
        )
        assert factors_from_red_flags(flags, weights) == factors_from_red_flags(
            flags, weights
        )


# -- Verification factors ------------------------------------------------


class TestFactorsFromVerification:
    def test_builds_a_factor_for_a_contradicted_material_claim(self, weights) -> None:  # type: ignore[no-untyped-def]
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        result = contradicted("claim_001", claim_type=ClaimType.REGULATORY_STATUS)
        factors = factors_from_verification(
            (result,), {"claim_001": claim}, weights
        )
        assert len(factors) == 1
        assert factors[0].factor_type is VerificationFactorType.CONTRADICTED_CLAIM

    def test_a_factor_contributes_its_configured_weight(self, weights) -> None:  # type: ignore[no-untyped-def]
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        result = contradicted("claim_001")
        factor = factors_from_verification((result,), {"claim_001": claim}, weights)[0]
        assert factor.contribution == weights.contradicted_claim

    def test_no_claim_means_no_factor(self, weights) -> None:  # type: ignore[no-untyped-def]
        """A result for a claim we do not have cannot be assessed against it."""
        assert factors_from_verification((contradicted("ghost"),), {}, weights) == ()

    @pytest.mark.parametrize(
        "claim_type", [ClaimType.COMPANY_CLAIM, ClaimType.PRODUCT_CLAIM, ClaimType.OTHER]
    )
    def test_a_non_material_claim_produces_no_factor(
        self, claim_type: ClaimType, weights  # type: ignore[no-untyped-def]
    ) -> None:
        """An unverifiable statement about office hours is not a risk indicator."""
        claim = make_claim("Open Monday to Friday", claim_type, claim_id="claim_001")
        result = contradicted("claim_001")
        assert factors_from_verification((result,), {"claim_001": claim}, weights) == ()

    def test_a_verified_claim_produces_no_factor(self, weights) -> None:  # type: ignore[no-untyped-def]
        """Confirmation is not a risk indicator, and cannot lower the score."""
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        result = verified("claim_001")
        assert factors_from_verification((result,), {"claim_001": claim}, weights) == ()

    def test_an_unsearchable_claim_produces_a_zero_weight_factor(
        self, weights  # type: ignore[no-untyped-def]
    ) -> None:
        """Kept for visibility, but it must not move the score (D-006)."""
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        result = unsearchable("claim_001")
        factor = factors_from_verification((result,), {"claim_001": claim}, weights)[0]
        assert factor.contribution == 0
        assert factor.is_uncertainty is True
        assert factor.is_scoring is False

    def test_an_unverified_claim_from_a_completed_search_does_score(
        self, weights  # type: ignore[no-untyped-def]
    ) -> None:
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        result = unverified("claim_001")
        factor = factors_from_verification((result,), {"claim_001": claim}, weights)[0]
        assert factor.contribution == weights.unverified_claim
        assert factor.contribution > 0

    def test_uncertainty_factors_are_marked_as_such(self, weights) -> None:  # type: ignore[no-untyped-def]
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        result = unverified("claim_001")
        factor = factors_from_verification((result,), {"claim_001": claim}, weights)[0]
        assert factor.is_uncertainty is True

    def test_a_contradiction_is_not_marked_as_uncertainty(
        self, weights  # type: ignore[no-untyped-def]
    ) -> None:
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        result = contradicted("claim_001")
        factor = factors_from_verification((result,), {"claim_001": claim}, weights)[0]
        assert factor.is_uncertainty is False

    def test_the_reason_names_the_claim_and_its_status(self, weights) -> None:  # type: ignore[no-untyped-def]
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        result = contradicted("claim_001")
        factor = factors_from_verification((result,), {"claim_001": claim}, weights)[0]
        assert "claim_001" in factor.reason
        assert VerificationStatus.CONTRADICTED.value in factor.reason

    def test_the_source_phrase_names_the_claim_and_status(self, weights) -> None:  # type: ignore[no-untyped-def]
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        result = contradicted("claim_001")
        factor = factors_from_verification((result,), {"claim_001": claim}, weights)[0]
        assert "claim_001" in factor.source
        assert VerificationStatus.CONTRADICTED.value in factor.source

    def test_output_is_deterministic(self, weights) -> None:  # type: ignore[no-untyped-def]
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        result = contradicted("claim_001")
        by_id = {"claim_001": claim}
        assert factors_from_verification((result,), by_id, weights) == (
            factors_from_verification((result,), by_id, weights)
        )


# -- Claim relevance -----------------------------------------------------


class TestClaimIsRiskRelevant:
    @pytest.mark.parametrize(
        "claim_type",
        [
            ClaimType.REGULATORY_STATUS,
            ClaimType.GUARANTEE_CLAIM,
            ClaimType.RETURN_PROMISE,
            ClaimType.PROFIT_PROMISE,
            ClaimType.PERFORMANCE_CLAIM,
            ClaimType.CREDENTIAL_CLAIM,
            ClaimType.OWNERSHIP_CLAIM,
            ClaimType.AFFILIATION_CLAIM,
            ClaimType.WITHDRAWAL_CLAIM,
            ClaimType.PAYMENT_INSTRUCTION,
        ],
    )
    def test_material_claim_families_are_relevant(self, claim_type: ClaimType) -> None:
        assert claim_is_risk_relevant(claim_type)

    @pytest.mark.parametrize(
        "claim_type",
        [ClaimType.COMPANY_CLAIM, ClaimType.PRODUCT_CLAIM, ClaimType.OTHER],
    )
    def test_incidental_claim_families_are_not(self, claim_type: ClaimType) -> None:
        assert not claim_is_risk_relevant(claim_type)
