"""Tests for Phase 6 de-duplication and cross-stage linking.

This is where "do not double count" is actually implemented, so these tests are
the ones that would catch the failure that matters most: **one behaviour scoring
more than once because more than one stage noticed it.**

The scenario that motivates the whole module, and the shape of several tests here:

> Content says "we are SEBI approved". Phase 1 fires `FAKE_REGULATORY_CLAIM` on
> those words. Phase 2 extracts a `REGULATORY_STATUS` claim over the same span.
> Phase 4 returns `CONTRADICTED` for it. Phase 5 attaches a SEBI register page.

That is one risk signal reported four ways. It must produce one scoring factor,
with the other three attached to it as provenance.

The rules under test:

1. Duplicate red flags and duplicate verification results collapse to one.
2. A claim links to a red flag by span overlap, or by claim-type mapping.
3. A verification factor describing an already-counted signal contributes ``0``
   and records ``absorbed_into``; the primary gains the status as context.
4. Absorbed factors are **kept**, not dropped, so the breakdown stays complete.
5. Evidence attaches ids and contributes nothing.
"""

from __future__ import annotations

from app.core.config import Settings
from app.schemas.claims import ClaimType
from app.schemas.red_flags import RedFlagCode
from app.schemas.risk import RiskFactorOrigin, VerificationFactorType
from app.services.risk import (
    CLAIM_TYPE_RED_FLAG_CODES,
    absorb_duplicate_signals,
    attach_absorption_context,
    attach_claims,
    attach_evidence,
    claim_links_to,
    collapse_duplicate_red_flags,
    collapse_duplicate_results,
    factors_from_red_flags,
    factors_from_verification,
    spans_overlap,
    weights_from_settings,
)
from tests.risk_factories import (
    contradicted,
    evidence_for,
    make_flag,
    make_placed_claim,
    unverified,
)

WEIGHTS = weights_from_settings(Settings())


# -- Span overlap -------------------------------------------------------


class TestSpansOverlap:
    def test_sharing_a_character_is_overlap(self) -> None:
        flag = make_flag(start=10, end=30)
        claim = make_placed_claim("SEBI approved", start=20)
        assert spans_overlap(claim.evidence_span, flag.evidence_span)

    def test_fully_containing_is_overlap(self) -> None:
        flag = make_flag(start=0, end=50)
        claim = make_placed_claim("SEBI approved", start=10)
        assert spans_overlap(claim.evidence_span, flag.evidence_span)

    def test_fully_contained_is_overlap(self) -> None:
        flag = make_flag(start=10, end=50)
        claim = make_placed_claim("SEBI approved", start=20)
        assert spans_overlap(claim.evidence_span, flag.evidence_span)

    def test_touching_at_a_boundary_is_not_overlap(self) -> None:
        """Half-open intervals, matching `EvidenceSpan` semantics."""
        flag = make_flag(start=0, end=13)
        claim = make_placed_claim("SEBI approved", start=13)
        assert not spans_overlap(claim.evidence_span, flag.evidence_span)

    def test_disjoint_spans_do_not_overlap(self) -> None:
        flag = make_flag(start=0, end=10)
        claim = make_placed_claim("SEBI approved", start=500)
        assert not spans_overlap(claim.evidence_span, flag.evidence_span)

    def test_overlap_is_symmetric(self) -> None:
        flag = make_flag(start=10, end=30)
        claim = make_placed_claim("SEBI approved", start=20)
        assert spans_overlap(claim.evidence_span, flag.evidence_span) == spans_overlap(
            flag.evidence_span, claim.evidence_span
        )


# -- Claim to red-flag linking -------------------------------------------


class TestClaimLinksTo:
    def test_an_overlapping_span_links(self) -> None:
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.OTHER, start=5)
        assert claim_links_to(claim, flag)

    def test_a_claim_type_maps_to_its_own_rule(self) -> None:
        """Phase 1 and Phase 2 segment text differently, so overlap is not enough."""
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=500, end=540)
        claim = make_placed_claim("Our firm is registered", ClaimType.REGULATORY_STATUS)
        assert claim_links_to(claim, flag)

    def test_an_unrelated_claim_type_does_not_link(self) -> None:
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=500, end=540)
        claim = make_placed_claim("Open Monday", ClaimType.COMPANY_CLAIM)
        assert not claim_links_to(claim, flag)

    def test_a_product_claim_maps_to_no_rule_at_all(self) -> None:
        """An unverifiable product description is not an investment risk indicator."""
        assert ClaimType.PRODUCT_CLAIM not in CLAIM_TYPE_RED_FLAG_CODES

    def test_a_company_claim_maps_to_no_rule_at_all(self) -> None:
        assert ClaimType.COMPANY_CLAIM not in CLAIM_TYPE_RED_FLAG_CODES

    def test_every_mapped_type_names_real_rules(self) -> None:
        for claim_type, codes in CLAIM_TYPE_RED_FLAG_CODES.items():
            assert codes, f"{claim_type} maps to nothing; remove it or map it"
            for code in codes:
                assert isinstance(code, RedFlagCode)


# -- Collapsing duplicates ----------------------------------------------


class TestCollapseDuplicateRedFlags:
    def test_the_same_rule_twice_collapses_to_one(self) -> None:
        flags = (make_flag(), make_flag())
        assert len(collapse_duplicate_red_flags(flags)) == 1

    def test_distinct_rules_are_preserved(self) -> None:
        flags = (
            make_flag(RedFlagCode.GUARANTEED_RETURN, start=0, end=40),
            make_flag(RedFlagCode.URGENCY_PRESSURE, start=100, end=140),
        )
        assert len(collapse_duplicate_red_flags(flags)) == 2

    def test_the_first_seen_flag_is_kept(self) -> None:
        first = make_flag(start=0, end=40)
        second = make_flag(start=500, end=540)
        assert collapse_duplicate_red_flags((first, second))[0] is first

    def test_order_is_preserved(self) -> None:
        first = make_flag(RedFlagCode.URGENCY_PRESSURE, start=0, end=40)
        second = make_flag(RedFlagCode.GUARANTEED_RETURN, start=100, end=140)
        assert collapse_duplicate_red_flags((first, second)) == (first, second)

    def test_empty_input_stays_empty(self) -> None:
        assert collapse_duplicate_red_flags(()) == ()


class TestCollapseDuplicateResults:
    def test_two_results_for_one_claim_collapse_to_one(self) -> None:
        assert len(collapse_duplicate_results((unverified(), unverified()))) == 1

    def test_distinct_claims_are_preserved(self) -> None:
        results = (unverified("claim_001"), unverified("claim_002"))
        assert len(collapse_duplicate_results(results)) == 2

    def test_the_first_seen_result_is_kept(self) -> None:
        first = unverified("claim_001")
        second = contradicted("claim_001")
        assert collapse_duplicate_results((first, second))[0] is first

    def test_empty_input_stays_empty(self) -> None:
        assert collapse_duplicate_results(()) == ()


# -- Attaching claims ---------------------------------------------------


class TestAttachClaims:
    def build(self, flags, claims):  # type: ignore[no-untyped-def]
        """Build red-flag factors, then attach claims to them."""
        factors = factors_from_red_flags(flags, WEIGHTS)
        return attach_claims(factors, claims, flags)

    def test_an_overlapping_claim_is_attached(self) -> None:
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        assert self.build((flag,), (claim,))[0].claim_ids == ("claim_001",)

    def test_a_type_mapped_claim_is_attached_without_overlap(self) -> None:
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=500, end=540)
        claim = make_placed_claim("Our firm is registered", ClaimType.REGULATORY_STATUS)
        assert self.build((flag,), (claim,))[0].claim_ids == ("claim_001",)

    def test_an_unrelated_claim_is_not_attached(self) -> None:
        flag = make_flag(RedFlagCode.GUARANTEED_RETURN, start=0, end=40)
        claim = make_placed_claim("Open Monday", ClaimType.COMPANY_CLAIM, start=500)
        assert self.build((flag,), (claim,))[0].claim_ids == ()

    def test_several_matching_claims_are_all_attached(self) -> None:
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claims = (
            make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS, claim_id="c1"),
            make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS, claim_id="c2"),
        )
        assert self.build((flag,), claims)[0].claim_ids == ("c1", "c2")

    def test_a_factor_whose_flag_was_not_supplied_is_left_alone(self) -> None:
        """An unlinked factor loses traceability rather than gaining a guess."""
        flag = make_flag()
        factors = factors_from_red_flags((flag,), WEIGHTS)
        attached = attach_claims(factors, (make_placed_claim("x"),), ())
        assert attached[0].claim_ids == ()

    def test_preserves_input_order(self) -> None:
        flags = (
            make_flag(RedFlagCode.GUARANTEED_RETURN, start=0, end=40),
            make_flag(RedFlagCode.URGENCY_PRESSURE, start=100, end=140),
        )
        assert [f.factor_type for f in self.build(flags, ())] == [
            RedFlagCode.GUARANTEED_RETURN,
            RedFlagCode.URGENCY_PRESSURE,
        ]


# -- Attaching evidence -------------------------------------------------


class TestAttachEvidence:
    def test_evidence_ids_are_attached_to_the_factor_for_its_claim(self) -> None:
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        factors = attach_claims(
            factors_from_red_flags((flag,), WEIGHTS), (claim,), (flag,)
        )
        evidence = evidence_for("claim_001")
        attached = attach_evidence(factors, (evidence,))
        assert attached[0].evidence_ids == tuple(
            item.id for item in evidence.evidence
        )
        assert attached[0].source_ids == tuple(
            item.source.source_id for item in evidence.evidence
        )
        assert attached[0].is_evidence_backed is True

    def test_evidence_never_changes_a_contribution(self) -> None:
        """Phase 5 evidence is never summed into the score (D-007)."""
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        factors = attach_claims(
            factors_from_red_flags((flag,), WEIGHTS), (claim,), (flag,)
        )
        before = factors[0].contribution
        after = attach_evidence(factors, (evidence_for("claim_001"),))
        assert after[0].contribution == before

    def test_a_factor_with_no_claims_gains_nothing(self) -> None:
        flag = make_flag()
        factors = factors_from_red_flags((flag,), WEIGHTS)
        attached = attach_evidence(factors, (evidence_for("claim_001"),))
        assert attached[0].evidence_ids == ()

    def test_evidence_for_another_claim_is_not_attached(self) -> None:
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        factors = attach_claims(
            factors_from_red_flags((flag,), WEIGHTS), (claim,), (flag,)
        )
        attached = attach_evidence(factors, (evidence_for("claim_999"),))
        assert attached[0].evidence_ids == ()

    def test_a_claim_with_no_evidence_attaches_nothing(self) -> None:
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        factors = attach_claims(
            factors_from_red_flags((flag,), WEIGHTS), (claim,), (flag,)
        )
        assert attach_evidence(factors, ())[0].evidence_ids == ()


# -- Absorption ---------------------------------------------------------


def build_linked(flags, claims, results, evidence=(), weights=WEIGHTS):  # type: ignore[no-untyped-def]
    """Run the full factor pipeline up to absorption, for one investigation."""
    by_id = {claim.id: claim for claim in claims}
    factors = list(factors_from_red_flags(flags, weights))
    factors.extend(factors_from_verification(results, by_id, weights))
    linked = attach_claims(tuple(factors), tuple(claims), flags)
    with_evidence = attach_evidence(linked, tuple(evidence))
    return absorb_duplicate_signals(with_evidence)


class TestAbsorbDuplicateSignals:
    def test_a_verification_factor_matching_a_red_flag_is_absorbed(self) -> None:
        """The core rule: one signal, one contribution."""
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        result = contradicted("claim_001")
        factors = build_linked((flag,), (claim,), (result,))
        verification = next(
            f for f in factors if f.origin is RiskFactorOrigin.VERIFICATION
        )
        assert verification.contribution == 0
        assert verification.absorbed_into is not None

    def test_the_red_flag_remains_the_primary_and_still_scores(self) -> None:
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        factors = build_linked((flag,), (claim,), (contradicted("claim_001"),))
        primary = next(f for f in factors if f.origin is RiskFactorOrigin.RED_FLAG)
        assert primary.absorbed_into is None
        assert primary.contribution == primary.weight

    def test_the_two_factors_together_contribute_only_one_signal(self) -> None:
        """The sum must equal the primary's weight, not both weights."""
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        factors = build_linked((flag,), (claim,), (contradicted("claim_001"),))
        primary = next(f for f in factors if f.origin is RiskFactorOrigin.RED_FLAG)
        assert sum(f.contribution for f in factors) == primary.weight

    def test_an_absorbed_factor_is_kept_not_dropped(self) -> None:
        """The breakdown must still show that the claim was contradicted."""
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        factors = build_linked((flag,), (claim,), (contradicted("claim_001"),))
        assert len(factors) == 2

    def test_a_verification_factor_for_an_unlinked_claim_still_scores(self) -> None:
        """A different signal is a different signal."""
        flag = make_flag(RedFlagCode.GUARANTEED_RETURN, start=0, end=40)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS, start=500)
        factors = build_linked((flag,), (claim,), (contradicted("claim_001"),))
        verification = next(
            f for f in factors if f.origin is RiskFactorOrigin.VERIFICATION
        )
        assert verification.absorbed_into is None
        assert verification.contribution == verification.weight

    def test_a_zero_weight_primary_does_not_absorb(self) -> None:
        """If the primary scored nothing, the claim's own factor is the only one.

        Otherwise absorption would erase the finding entirely: a red flag
        configured to weigh nothing must not silence the claim's contradiction.
        """
        flag = make_flag(RedFlagCode.GUARANTEED_RETURN, start=0, end=40)
        claim = make_placed_claim("guaranteed returns", ClaimType.GUARANTEE_CLAIM)
        weights = WEIGHTS.model_copy(
            update={
                "red_flag_weights": {
                    **WEIGHTS.red_flag_weights,
                    RedFlagCode.GUARANTEED_RETURN: 0,
                }
            }
        )
        factors = build_linked(
            (flag,), (claim,), (contradicted("claim_001"),), weights=weights
        )
        primary = next(f for f in factors if f.origin is RiskFactorOrigin.RED_FLAG)
        verification = next(
            f for f in factors if f.origin is RiskFactorOrigin.VERIFICATION
        )
        assert primary.contribution == 0
        assert verification.absorbed_into is None
        assert verification.is_scoring is True

    def test_an_uncertainty_factor_is_never_absorbed_into_scoring(self) -> None:
        """A verification gap is not a duplicate of a pattern that matched."""
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        from tests.risk_factories import unsearchable

        factors = build_linked((flag,), (claim,), (unsearchable("claim_001"),))
        uncertainty = next(
            f
            for f in factors
            if f.factor_type is VerificationFactorType.INSUFFICIENT_EVIDENCE
        )
        assert uncertainty.contribution == 0
        assert uncertainty.absorbed_into is None

    def test_an_uncertainty_factor_does_not_silence_the_primary(self) -> None:
        """The red flag still counts, and the gap is still visible."""
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        from tests.risk_factories import unsearchable

        factors = build_linked((flag,), (claim,), (unsearchable("claim_001"),))
        primary = next(f for f in factors if f.origin is RiskFactorOrigin.RED_FLAG)
        assert primary.is_scoring is True
        assert sum(f.contribution for f in factors) == primary.weight

    def test_nothing_scores_twice_for_one_claim(self) -> None:
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        factors = build_linked((flag,), (claim,), (unverified("claim_001"),))
        scoring = [f for f in factors if f.is_scoring]
        assert len(scoring) == 1

    def test_an_unverified_claim_is_absorbed_like_any_other_finding(self) -> None:
        """`UNVERIFIED_CLAIM` carries weight, so it is a second account of one signal.

        The trap this guards: `UNVERIFIED_CLAIM` sets `is_uncertainty`, and
        exempting uncertainty factors from absorption exempts this one too. It
        must be absorbed, or "SEBI approved" would score the red flag's 25 *and*
        the claim's 8 for the same words.
        """
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        factors = build_linked((flag,), (claim,), (unverified("claim_001"),))
        unverified_factor = next(
            f
            for f in factors
            if f.factor_type is VerificationFactorType.UNVERIFIED_CLAIM
        )
        assert unverified_factor.is_uncertainty is True
        assert unverified_factor.absorbed_into is not None
        assert unverified_factor.contribution == 0

    def test_absorbed_into_points_at_a_factor_that_exists(self) -> None:
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        factors = build_linked((flag,), (claim,), (contradicted("claim_001"),))
        ids = {f.id for f in factors}
        for factor in factors:
            if factor.absorbed_into:
                assert factor.absorbed_into in ids

    def test_no_factor_absorbs_into_itself(self) -> None:
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        for factor in build_linked((flag,), (claim,), (contradicted("claim_001"),)):
            assert factor.absorbed_into != factor.id

    def test_deterministic(self) -> None:
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        args = ((flag,), (claim,), (contradicted("claim_001"),))
        assert build_linked(*args) == build_linked(*args)


class TestAttachAbsorptionContext:
    def test_the_primary_gains_the_absorbed_status(self) -> None:
        """The claim was contradicted, and that fact must survive de-duplication."""
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        absorbed = build_linked((flag,), (claim,), (contradicted("claim_001"),))
        final = attach_absorption_context(absorbed)
        primary = next(f for f in final if f.origin is RiskFactorOrigin.RED_FLAG)
        assert primary.verification_statuses

    def test_a_primary_with_no_absorbed_factor_is_unchanged(self) -> None:
        flag = make_flag()
        factors = factors_from_red_flags((flag,), WEIGHTS)
        assert attach_absorption_context(factors) == factors

    def test_statuses_are_not_duplicated(self) -> None:
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        absorbed = build_linked((flag,), (claim,), (contradicted("claim_001"),))
        primary = next(
            f
            for f in attach_absorption_context(absorbed)
            if f.origin is RiskFactorOrigin.RED_FLAG
        )
        assert len(primary.verification_statuses) == len(
            set(primary.verification_statuses)
        )

    def test_deterministic(self) -> None:
        flag = make_flag(RedFlagCode.FAKE_REGULATORY_CLAIM, start=0, end=13)
        claim = make_placed_claim("SEBI approved", ClaimType.REGULATORY_STATUS)
        absorbed = build_linked((flag,), (claim,), (contradicted("claim_001"),))
        assert attach_absorption_context(absorbed) == attach_absorption_context(
            absorbed
        )
