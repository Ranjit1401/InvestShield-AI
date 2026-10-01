"""Tests for the verification schemas (Phase 4, §1).

These tests defend the boundaries the rest of the pipeline relies on: exactly
five statuses, no verdict vocabulary anywhere in the schema, and a schema that
refuses internally contradictory results such as a `VERIFIED` claim citing no
source.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.verification import (
    AUTHORITATIVE_TIERS,
    TIER_PRIORITY,
    VERIFICATION_REASON_CODES,
    IdentityMatch,
    SourceTier,
    VerificationResponse,
    VerificationResult,
    VerificationStatus,
)

EXPECTED_STATUSES = {
    "VERIFIED",
    "UNVERIFIED",
    "CONTRADICTED",
    "INSUFFICIENT_EVIDENCE",
    "NOT_APPLICABLE",
}


def build_result(**overrides: object) -> VerificationResult:
    """Build a valid `VerificationResult` with the given field overrides."""
    defaults: dict[str, object] = {
        "claim_id": "claim_001",
        "claim_type": "REGULATORY_STATUS",
        "status": VerificationStatus.INSUFFICIENT_EVIDENCE,
        "reason": "A reason that says what happened.",
        "reason_code": "SEARCH_UNAVAILABLE",
        "confidence": 0.3,
    }
    defaults.update(overrides)
    return VerificationResult(**defaults)


class TestVerificationStatusVocabulary:
    """Exactly five statuses, and none of them a verdict."""

    def test_exactly_five_statuses_exist(self) -> None:
        assert {status.value for status in VerificationStatus} == EXPECTED_STATUSES

    @pytest.mark.parametrize(
        "forbidden",
        ["SCAM", "NOT_SCAM", "FRAUD", "SAFE", "DANGEROUS", "BUY", "SELL", "INVEST"],
    )
    def test_no_verdict_status_exists(self, forbidden: str) -> None:
        assert forbidden not in {status.value for status in VerificationStatus}

    def test_status_is_stable_string_enum(self) -> None:
        assert VerificationStatus.VERIFIED == "VERIFIED"

    def test_identity_match_vocabulary(self) -> None:
        assert {member.value for member in IdentityMatch} == {
            "MATCHED",
            "ABSENT",
            "AMBIGUOUS",
        }

    def test_no_field_name_expresses_a_verdict(self) -> None:
        forbidden = ("scam", "fraud", "safe", "danger", "risk_score", "verdict")
        field_names = tuple(VerificationResult.model_fields)
        for name in field_names:
            assert not any(term in name.lower() for term in forbidden)


class TestSourceTier:
    """Tier ordering and the authoritative cut-off."""

    def test_every_tier_has_a_priority(self) -> None:
        assert set(TIER_PRIORITY) == set(SourceTier)

    def test_priority_order_is_regulator_first(self) -> None:
        assert (
            TIER_PRIORITY[SourceTier.TIER_1_PRIMARY_REGULATOR]
            < TIER_PRIORITY[SourceTier.TIER_2_GOVERNMENT]
            < TIER_PRIORITY[SourceTier.TIER_3_EXCHANGE]
            < TIER_PRIORITY[SourceTier.TIER_4_OFFICIAL_ENTITY]
            < TIER_PRIORITY[SourceTier.TIER_5_TRUSTED_SECONDARY]
            < TIER_PRIORITY[SourceTier.TIER_6_GENERAL_WEB]
            < TIER_PRIORITY[SourceTier.UNKNOWN]
        )

    def test_only_primary_government_and_exchange_are_authoritative(self) -> None:
        assert AUTHORITATIVE_TIERS == frozenset(
            {
                SourceTier.TIER_1_PRIMARY_REGULATOR,
                SourceTier.TIER_2_GOVERNMENT,
                SourceTier.TIER_3_EXCHANGE,
            }
        )
        assert SourceTier.TIER_4_OFFICIAL_ENTITY not in AUTHORITATIVE_TIERS
        assert SourceTier.TIER_6_GENERAL_WEB not in AUTHORITATIVE_TIERS
        assert SourceTier.UNKNOWN not in AUTHORITATIVE_TIERS


class TestReasonCodes:
    """The reason-code vocabulary is closed and documented."""

    def test_every_reason_code_is_declared(self) -> None:
        assert VERIFICATION_REASON_CODES == frozenset(
            {
                "AUTHORITATIVE_SOURCE_CONFIRMS",
                "NO_CONFIRMATION_FOUND",
                "AUTHORITATIVE_SOURCE_CONTRADICTS",
                "CONFLICTING_AUTHORITATIVE_SOURCES",
                "SEARCH_UNAVAILABLE",
                "SEARCH_FAILED",
                "ZERO_RESULTS",
                "IDENTITY_AMBIGUOUS",
                "IDENTITY_NOT_FOUND",
                "NO_CLAIM_RELEVANT_SOURCE",
                "NO_QUERY_BUILT",
                "NOT_A_FACTUAL_CLAIM",
            }
        )

    def test_unknown_reason_code_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unknown verification reason code"):
            build_result(reason_code="TOTALLY_MADE_UP")


class TestVerificationResultValidation:
    """A status can never contradict the evidence it cites."""

    def test_verified_requires_sources(self) -> None:
        with pytest.raises(ValidationError, match="must cite the sources"):
            build_result(
                status=VerificationStatus.VERIFIED,
                reason_code="AUTHORITATIVE_SOURCE_CONFIRMS",
            )

    def test_verified_requires_confirmation_reason(self) -> None:
        with pytest.raises(ValidationError, match="AUTHORITATIVE_SOURCE_CONFIRMS"):
            build_result(
                status=VerificationStatus.VERIFIED,
                reason_code="ZERO_RESULTS",
                source_ids=("src_abc123abc123",),
            )

    def test_verified_with_sources_is_accepted(self) -> None:
        result = build_result(
            status=VerificationStatus.VERIFIED,
            reason_code="AUTHORITATIVE_SOURCE_CONFIRMS",
            source_ids=("src_abc123abc123",),
        )
        assert result.is_positive is True
        assert result.is_inconclusive is False

    def test_contradicted_requires_sources(self) -> None:
        with pytest.raises(ValidationError, match="must cite the conflicting source"):
            build_result(
                status=VerificationStatus.CONTRADICTED,
                reason_code="AUTHORITATIVE_SOURCE_CONTRADICTS",
            )

    def test_contradicted_requires_a_conflict_reason(self) -> None:
        with pytest.raises(ValidationError, match="authoritative-conflict reason"):
            build_result(
                status=VerificationStatus.CONTRADICTED,
                reason_code="ZERO_RESULTS",
                source_ids=("src_abc123abc123",),
            )

    def test_contradicted_with_conflict_reason_is_accepted(self) -> None:
        result = build_result(
            status=VerificationStatus.CONTRADICTED,
            reason_code="CONFLICTING_AUTHORITATIVE_SOURCES",
            source_ids=("src_abc123abc123",),
        )
        assert result.status is VerificationStatus.CONTRADICTED

    def test_not_applicable_cannot_cite_sources(self) -> None:
        with pytest.raises(ValidationError, match="cannot cite sources"):
            build_result(
                status=VerificationStatus.NOT_APPLICABLE,
                reason_code="NOT_A_FACTUAL_CLAIM",
                source_ids=("src_abc123abc123",),
            )

    @pytest.mark.parametrize(
        "status",
        [
            VerificationStatus.UNVERIFIED,
            VerificationStatus.INSUFFICIENT_EVIDENCE,
            VerificationStatus.NOT_APPLICABLE,
        ],
    )
    def test_inconclusive_statuses(self, status: VerificationStatus) -> None:
        result = build_result(
            status=status,
            reason_code={
                VerificationStatus.UNVERIFIED: "ZERO_RESULTS",
                VerificationStatus.INSUFFICIENT_EVIDENCE: "SEARCH_UNAVAILABLE",
                VerificationStatus.NOT_APPLICABLE: "NOT_A_FACTUAL_CLAIM",
            }[status],
        )
        assert result.is_inconclusive is True
        assert result.is_positive is False

    @pytest.mark.parametrize("confidence", [-0.1, 1.1])
    def test_confidence_must_be_a_probability(self, confidence: float) -> None:
        with pytest.raises(ValidationError):
            build_result(confidence=confidence)

    def test_confidence_accepts_the_full_range(self) -> None:
        assert build_result(confidence=0.0).confidence == 0.0
        assert build_result(confidence=1.0).confidence == 1.0

    def test_result_is_frozen(self) -> None:
        result = build_result()
        with pytest.raises(ValidationError):
            result.status = VerificationStatus.CONTRADICTED

    def test_empty_claim_id_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            build_result(claim_id="")


class TestVerificationResponse:
    """Counts are derived, so they cannot disagree with the results."""

    def test_counts_are_recomputed(self) -> None:
        results = (
            build_result(
                claim_id="c1",
                status=VerificationStatus.VERIFIED,
                reason_code="AUTHORITATIVE_SOURCE_CONFIRMS",
                source_ids=("src_1",),
            ),
            build_result(claim_id="c2", reason_code="ZERO_RESULTS",
                         status=VerificationStatus.UNVERIFIED),
            build_result(claim_id="c3", reason_code="SEARCH_UNAVAILABLE"),
        )
        response = VerificationResponse(results=results, verified_count=99)
        assert response.verified_count == 1
        assert response.unverified_count == 1
        assert response.insufficient_evidence_count == 1
        assert response.contradicted_count == 0
        assert response.not_applicable_count == 0

    def test_claim_ids_preserve_input_order(self) -> None:
        response = VerificationResponse(
            results=(
                build_result(claim_id="claim_003", reason_code="ZERO_RESULTS",
                             status=VerificationStatus.UNVERIFIED),
                build_result(claim_id="claim_001", reason_code="SEARCH_UNAVAILABLE"),
            )
        )
        assert response.claim_ids == ("claim_003", "claim_001")

    def test_empty_response_is_valid(self) -> None:
        response = VerificationResponse()
        assert response.results == ()
        assert response.verified_count == 0
        assert response.claim_ids == ()

    def test_batch_warnings_are_preserved(self) -> None:
        response = VerificationResponse(warnings=("Search was unavailable.",))
        assert response.warnings == ("Search was unavailable.",)