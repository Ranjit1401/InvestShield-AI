"""Tests for the Phase 2 extraction schemas.

The schemas are the contract between extraction and every later stage, so these
tests assert the invariants that the rest of the pipeline relies on: exact
verbatim evidence, frozen models, complete relationship integrity, and the
deliberate absence of any verification-verdict field.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.claims import (
    PROMISE_CLAIM_TYPES,
    VERIFIABLE_CLAIM_TYPES,
    Claim,
    ClaimType,
)
from app.schemas.common import EvidenceSpan, make_span, span_slice_matches
from app.schemas.entities import (
    IDENTITY_ENTITY_TYPES,
    PAYMENT_ENTITY_TYPES,
    Entity,
    EntityType,
    normalize_entity_name,
)
from app.schemas.extraction import (
    ClaimEntityLink,
    ExtractionMode,
    ExtractionResult,
    RelationshipType,
)

EXPECTED_CLAIM_TYPES = {
    "REGULATORY_STATUS",
    "RETURN_PROMISE",
    "PROFIT_PROMISE",
    "PERFORMANCE_CLAIM",
    "CREDENTIAL_CLAIM",
    "COMPANY_CLAIM",
    "PRODUCT_CLAIM",
    "INVESTMENT_OPPORTUNITY",
    "PAYMENT_INSTRUCTION",
    "WITHDRAWAL_CLAIM",
    "AFFILIATION_CLAIM",
    "OWNERSHIP_CLAIM",
    "GUARANTEE_CLAIM",
    "OTHER",
}

EXPECTED_ENTITY_TYPES = {
    "PERSON",
    "COMPANY",
    "ORGANIZATION",
    "REGULATOR",
    "BROKER",
    "INVESTMENT_ADVISER",
    "PLATFORM",
    "WEBSITE",
    "DOMAIN",
    "PRODUCT",
    "FINANCIAL_INSTRUMENT",
    "LOCATION",
    "SOCIAL_HANDLE",
    "REGISTRATION_NUMBER",
    "BANK_ACCOUNT",
    "UPI_ID",
    "PHONE_NUMBER",
    "EMAIL",
    "OTHER",
}

EXPECTED_RELATIONSHIP_TYPES = {"MENTIONS", "SUBJECT"}

#: Words that would indicate the schemas are already judging truth. Extraction
#: must never do that; verification is a later stage.
FORBIDDEN_FIELD_NAMES = (
    "verdict",
    "is_true",
    "truth",
    "is_fraud",
    "fraud_probability",
    "scam_score",
    "risk_score",
    "label",
    "confidence_score_truth",
)


class TestEvidenceSpan:
    """`EvidenceSpan` must index the original text exactly."""

    def test_make_span_slices_original_text(self) -> None:
        original = "Guaranteed 30% returns."
        span = make_span(original, 0, 14)

        assert span.text == "Guaranteed 30%"
        assert original[span.start : span.end] == span.text
        assert span_slice_matches(original, span)

    @pytest.mark.parametrize(("start", "end"), [(-1, 3), (6, 6), (6, 2), (0, 500)])
    def test_make_span_rejects_invalid_ranges(self, start: int, end: int) -> None:
        with pytest.raises(ValueError):
            make_span("Contact us", start, end)

    def test_end_must_follow_start(self) -> None:
        with pytest.raises(ValueError):
            EvidenceSpan(start=6, end=2, text="")

    def test_slice_match_helper_detects_drift(self) -> None:
        original = "Contact support@example.com today"
        span = EvidenceSpan(start=0, end=7, text="Contact")

        assert span_slice_matches(original, span)
        assert not span_slice_matches("Other text here", span)


class TestClaimSchema:
    """Claim contract: verbatim text, bounded confidence, no truth claims."""

    def test_all_required_claim_types_exist(self) -> None:
        assert {member.value for member in ClaimType} == EXPECTED_CLAIM_TYPES

    def test_promise_and_verifiable_sets_are_disjoint_subsets(self) -> None:
        for claim_type in PROMISE_CLAIM_TYPES:
            assert claim_type in set(ClaimType)
        assert PROMISE_CLAIM_TYPES <= set(ClaimType)
        assert VERIFIABLE_CLAIM_TYPES <= set(ClaimType)

    def test_guarantee_is_always_a_promise(self) -> None:
        assert ClaimType.GUARANTEE_CLAIM in PROMISE_CLAIM_TYPES
        assert ClaimType.RETURN_PROMISE in PROMISE_CLAIM_TYPES
        assert ClaimType.PROFIT_PROMISE in PROMISE_CLAIM_TYPES

    def test_claim_requires_verbatim_text(self) -> None:
        span = EvidenceSpan(start=0, end=5, text="We are")

        with pytest.raises(ValidationError):
            Claim(
                id="claim_001",
                text="We are not",
                claim_type=ClaimType.GUARANTEE_CLAIM,
                confidence=0.8,
                evidence_span=span,
            )

    @pytest.mark.parametrize("confidence", [-0.1, 1.1])
    def test_confidence_is_bounded(self, confidence: float) -> None:
        with pytest.raises(ValidationError):
            Claim(
                id="claim_001",
                text="We are",
                claim_type=ClaimType.OTHER,
                confidence=confidence,
                evidence_span=EvidenceSpan(start=0, end=5, text="We are"),
            )

    def test_claim_is_frozen(self) -> None:
        claim = Claim(
            id="claim_001",
            text="We are",
            claim_type=ClaimType.OTHER,
            confidence=0.5,
            evidence_span=EvidenceSpan(start=0, end=5, text="We are"),
        )

        with pytest.raises(ValidationError):
            claim.text = "tampered"

    def test_signals_and_metadata_have_defaults(self) -> None:
        claim = Claim(
            id="claim_001",
            text="We are",
            claim_type=ClaimType.OTHER,
            confidence=0.5,
            evidence_span=EvidenceSpan(start=0, end=5, text="We are"),
        )

        assert claim.signals == ()
        assert claim.metadata == {}
        assert claim.is_complete_sentence is True


class TestEntitySchema:
    """Entity contract: known types, normalised names, verbatim spans."""

    def test_all_required_entity_types_exist(self) -> None:
        assert {member.value for member in EntityType} == EXPECTED_ENTITY_TYPES

    def test_identity_and_payment_sets_are_valid_subsets(self) -> None:
        assert IDENTITY_ENTITY_TYPES <= set(EntityType)
        assert PAYMENT_ENTITY_TYPES <= set(EntityType)

    def test_normalize_entity_name_expands_legal_suffix(self) -> None:
        assert normalize_entity_name("Acme Capital Pvt. Ltd.") == "acme capital private limited"
        assert normalize_entity_name("ACME   capital") == "acme capital"

    def test_normalize_entity_name_keeps_identifier_punctuation(self) -> None:
        # "@" and "." are structural in UPI ids, emails and domains; removing
        # them would merge distinct identifiers.
        assert normalize_entity_name("Acme.Funds@okaxis") == "acme.funds@okaxis"
        assert normalize_entity_name("acme-finance.example") == "acme-finance.example"

    def test_normalize_entity_name_strips_edge_punctuation(self) -> None:
        assert normalize_entity_name("Rupee-Mitra Advisors LLP.") == (
            "rupee-mitra advisors limited liability partnership"
        )

    def test_normalize_entity_name_is_stable_across_spacing(self) -> None:
        assert normalize_entity_name("Telegram") == normalize_entity_name("telegram")

    def test_normalized_name_defaults_to_input(self) -> None:
        entity = Entity(
            id="entity_001",
            name="Acme Capital",
            entity_type=EntityType.COMPANY,
            normalized_name="acme capital",
            confidence=0.95,
            evidence_span=EvidenceSpan(start=0, end=12, text="Acme Capital"),
        )

        assert entity.key == (EntityType.COMPANY, "acme capital")
        with pytest.raises(ValidationError):
            entity.name = "changed"


class TestExtractionResultSchema:
    """Result contract: mode always explicit, relationships always resolvable."""

    def test_modes_are_exactly_three(self) -> None:
        assert {member.value for member in ExtractionMode} == {"LLM", "FALLBACK", "PARTIAL"}

    def test_relationship_types(self) -> None:
        assert {member.value for member in RelationshipType} == EXPECTED_RELATIONSHIP_TYPES

    def test_default_mode_is_fallback(self) -> None:
        assert ExtractionResult().extraction_mode is ExtractionMode.FALLBACK

    def test_relationships_must_reference_existing_ids(self) -> None:
        span = EvidenceSpan(start=0, end=5, text="We are")
        claim = Claim(
            id="claim_001",
            text="We are",
            claim_type=ClaimType.OTHER,
            confidence=0.5,
            evidence_span=span,
        )

        with pytest.raises(ValidationError):
            ExtractionResult(
                claims=(claim,),
                relationships=(
                    ClaimEntityLink(claim_id="claim_001", entity_id="entity_999"),
                ),
            )

    def test_valid_relationships_are_accepted(self) -> None:
        span = EvidenceSpan(start=0, end=5, text="We are")
        claim = Claim(
            id="claim_001",
            text="We are",
            claim_type=ClaimType.OTHER,
            confidence=0.5,
            evidence_span=span,
        )
        entity = Entity(
            id="entity_001",
            name="We",
            entity_type=EntityType.ORGANIZATION,
            normalized_name="we",
            confidence=0.5,
            evidence_span=EvidenceSpan(start=0, end=2, text="We"),
        )

        result = ExtractionResult(
            claims=(claim,),
            entities=(entity,),
            relationships=(
                ClaimEntityLink(
                    claim_id="claim_001",
                    entity_id="entity_001",
                    relationship=RelationshipType.SUBJECT,
                ),
            ),
        )

        assert result.relationships[0].relationship is RelationshipType.SUBJECT

    def test_result_has_no_verdict_field(self) -> None:
        fields = set(ExtractionResult.model_fields)

        for forbidden in FORBIDDEN_FIELD_NAMES:
            assert forbidden not in fields
        for claim_field in Claim.model_fields:
            assert claim_field not in FORBIDDEN_FIELD_NAMES
        for entity_field in Entity.model_fields:
            assert entity_field not in FORBIDDEN_FIELD_NAMES