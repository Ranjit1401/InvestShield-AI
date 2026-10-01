"""Schema-level guarantees for the evidence layer (Phase 5).

The evidence schemas exist to make one class of bug impossible: an item that
looks like evidence but cannot be traced back to a document a provider actually
returned. These tests pin that down at construction time, where it is cheap.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.evidence import (
    ALLOWED_EXCERPT_ORIGINS,
    EVIDENCE_ID_PREFIX,
    RELATION_RANK,
    RELEVANCE_RANK,
    EvidenceBundleResponse,
    EvidenceItem,
    EvidenceRelation,
    EvidenceRelevance,
    EvidenceResponse,
    EvidenceSource,
    EvidenceType,
)
from app.schemas.search import SOURCE_PRIORITY, SourceType
from app.schemas.verification import SourceTier, VerificationStatus
from app.services.verification.comparator import result_id_for, source_id_for
from tests.evidence_factories import make_verification, not_applicable_verification


ACME_URL = "https://www.sebi.gov.in/intermediaries/acme"


def source(**overrides: object) -> EvidenceSource:
    """Build an `EvidenceSource` with valid defaults.

    The `src_`/`res_` ids are derived exactly as Phase 4 derives them, so the
    default source is a faithful stand-in for a real normalized result.
    """
    canonical = str(overrides.get("canonical_url", ACME_URL))
    fields: dict[str, object] = {
        "source_id": source_id_for(canonical),
        "result_id": result_id_for(canonical),
        "url": ACME_URL,
        "canonical_url": ACME_URL,
        "domain": "sebi.gov.in",
        "title": "Acme Capital Advisors",
        "source_type": SourceType.REGULATOR,
        "source_tier": SourceTier.TIER_1_PRIMARY_REGULATOR,
        "position": 1,
    }
    fields.update(overrides)
    return EvidenceSource(**fields)  # type: ignore[arg-type]


def item(**overrides: object) -> EvidenceItem:
    """Build an `EvidenceItem` with valid defaults."""
    fields: dict[str, object] = {
        "id": f"{EVIDENCE_ID_PREFIX}abcdef012345",
        "claim_id": "claim_001",
        "verification_status": VerificationStatus.VERIFIED,
        "evidence_type": EvidenceType.REGULATORY_RECORD,
        "relationship": EvidenceRelation.SUPPORTS,
        "relevance": EvidenceRelevance.HIGH,
        "excerpt": "Acme Capital Advisors is registered as an investment adviser.",
        "excerpt_origin": "snippet",
        "source": source(),
    }
    fields.update(overrides)
    return EvidenceItem(**fields)  # type: ignore[arg-type]


class TestEvidenceSource:
    """A source is a retrieved document, carried through unchanged."""

    def test_fields_are_frozen(self) -> None:
        document = source()
        with pytest.raises(ValidationError):
            document.title = "Something else"  # type: ignore[misc]

    def test_ids_come_from_the_phases_that_minted_them(self) -> None:
        """`src_`/`res_` are Phase 4's ids, not a Phase 5 re-derivation."""
        document = source()
        assert document.source_id == source_id_for(document.canonical_url)
        assert document.result_id == result_id_for(document.canonical_url)

    def test_source_priority_is_phase_three_priority_unchanged(self) -> None:
        assert source().source_priority == SOURCE_PRIORITY[SourceType.REGULATOR]

    def test_priority_is_derived_not_stored(self) -> None:
        """A second copy of the number could drift from Phase 3's table."""
        assert "source_priority" not in source().model_fields

    def test_tiers_one_to_three_are_authoritative(self) -> None:
        for tier in SourceTier:
            document = source(source_tier=tier)
            expected = tier in {
                SourceTier.TIER_1_PRIMARY_REGULATOR,
                SourceTier.TIER_2_GOVERNMENT,
                SourceTier.TIER_3_EXCHANGE,
            }
            assert document.is_authoritative is expected, tier.value

    def test_an_unknown_publisher_is_not_authoritative(self) -> None:
        assert source(
            source_type=SourceType.UNKNOWN,
            source_tier=SourceTier.UNKNOWN,
        ).is_authoritative is False


class TestEvidenceItemProvenance:
    """Nothing may be an evidence item without a traceable origin."""

    def test_fields_are_frozen(self) -> None:
        with pytest.raises(ValidationError):
            item().excerpt = "Rewritten."  # type: ignore[misc]

    def test_an_empty_excerpt_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="non-empty excerpt"):
            item(excerpt="   ")

    @pytest.mark.parametrize("origin", sorted(ALLOWED_EXCERPT_ORIGINS))
    def test_allowed_origins_are_accepted(self, origin: str) -> None:
        assert item(excerpt_origin=origin).excerpt_origin == origin

    @pytest.mark.parametrize("origin", ["summary", "generated", "page_body", ""])
    def test_any_other_origin_is_rejected(self, origin: str) -> None:
        """Only `snippet` and `title` are real retrieved fields."""
        with pytest.raises(ValidationError, match="excerpt_origin"):
            item(excerpt_origin=origin)

    def test_an_id_without_the_evidence_prefix_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="evidence id"):
            item(id="src_abcdef012345")

    def test_a_blank_claim_id_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            item(claim_id="")

    def test_a_source_is_required(self) -> None:
        fields = item().model_dump()
        fields.pop("source")
        with pytest.raises(ValidationError):
            EvidenceItem.model_validate(fields)


class TestEvidenceItemSemantics:
    """Relationship, relevance and proof are kept as separate facts."""

    def test_a_supports_item_is_proof(self) -> None:
        assert item().is_proof is True

    def test_a_contradiction_is_proof_too(self) -> None:
        assert item(relationship=EvidenceRelation.CONTRADICTS).is_proof is True

    @pytest.mark.parametrize(
        "relation",
        [
            EvidenceRelation.IDENTITY_REFERENCE,
            EvidenceRelation.CONTEXT,
            EvidenceRelation.MENTIONS,
        ],
    )
    def test_neutral_relationships_are_never_proof(self, relation: EvidenceRelation) -> None:
        """Proof means "speaks to the claim", not "was retrieved"."""
        assert item(
            relationship=relation,
            relevance=EvidenceRelevance.HIGH,
        ).is_proof is False

    def test_high_relevance_alone_is_not_proof(self) -> None:
        assert item(
            relationship=EvidenceRelation.IDENTITY_REFERENCE,
            relevance=EvidenceRelevance.HIGH,
        ).is_proof is False

    def test_proof_never_depends_on_the_verification_status(self) -> None:
        """A `CONTRADICTED` claim still produces proof items; that is the point."""
        contradicted = item(verification_status=VerificationStatus.CONTRADICTED)
        assert contradicted.is_proof is True

    def test_no_member_of_the_vocabulary_states_a_verdict(self) -> None:
        """`SUPPORTS` is a relationship, never "the claim is true"."""
        for relation in EvidenceRelation:
            assert relation.value in {
                "SUPPORTS",
                "CONTRADICTS",
                "IDENTITY_REFERENCE",
                "CONTEXT",
                "MENTIONS",
            }

    def test_relevance_is_not_a_risk_score(self) -> None:
        """Relevance describes closeness; there is deliberately no numeric score."""
        assert "score" not in " ".join(item().model_dump()).casefold()
        assert all(not member.value.isdigit() for member in EvidenceRelevance)

    def test_the_record_type_does_not_encode_a_relationship(self) -> None:
        """D-023: type and relationship must stay independent axes."""
        types = {member.value for member in EvidenceType}
        assert not types & {"CLAIM_SUPPORT", "CLAIM_CONTRADICTION", "CONTEXTUAL"}

    def test_trace_path_links_claim_evidence_result_and_source(self) -> None:
        document = item()
        path = document.trace_path
        assert "->" in path
        assert "claim_001" in path
        assert document.result_id in path
        assert document.source_id in path

    def test_trace_path_is_ascii(self) -> None:
        """It is written to logs and consoles, so it must never need a codec."""
        path = item().trace_path
        assert path.isascii(), path

    def test_display_shorthands_reach_the_source(self) -> None:
        document = item()
        assert document.source_id == document.source.source_id
        assert document.result_id == document.source.result_id
        assert document.url == document.source.url


class TestEvidenceResponse:
    """Counts are recomputed, and mixing claims is rejected."""

    def test_counts_are_derived_from_the_items(self) -> None:
        response = EvidenceResponse(
            claim_id="claim_001",
            verification_status=VerificationStatus.INSUFFICIENT_EVIDENCE,
            evidence=(
                item(),
                item(
                    id=f"{EVIDENCE_ID_PREFIX}000000000002",
                    relationship=EvidenceRelation.CONTRADICTS,
                ),
                item(
                    id=f"{EVIDENCE_ID_PREFIX}000000000003",
                    relationship=EvidenceRelation.MENTIONS,
                    relevance=EvidenceRelevance.LOW,
                ),
            ),
            sources=(source(),),
        )
        assert response.evidence_count == 3
        assert response.supports_count == 1
        assert response.contradicts_count == 1
        assert response.context_count == 1
        assert response.source_count == 1
        assert response.proof_count == 2

    def test_an_empty_bundle_has_honest_zero_counts(self) -> None:
        response = EvidenceResponse(claim_id="claim_001")
        assert response.evidence_count == 0
        assert response.proof_count == 0
        assert response.sources == ()

    def test_evidence_from_another_claim_is_rejected(self) -> None:
        """A mixed bundle would stay internally consistent and be unauditable."""
        with pytest.raises(ValidationError, match="belongs to claim"):
            EvidenceResponse(
                claim_id="claim_002",
                evidence=(item(claim_id="claim_001"),),
                sources=(source(),),
            )

    def test_evidence_citing_an_unlisted_source_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not listed in sources"):
            EvidenceResponse(claim_id="claim_001", evidence=(item(),), sources=())

    def test_built_at_is_never_part_of_an_item_id(self) -> None:
        """Timestamps must not make identical evidence non-identical."""
        first = EvidenceResponse(claim_id="claim_001", evidence=(item(),), sources=(source(),))
        second = EvidenceResponse(claim_id="claim_001", evidence=(item(),), sources=(source(),))
        assert first.built_at != second.built_at
        assert first.evidence[0].id == second.evidence[0].id


class TestEvidenceBundleResponse:
    """"We found nothing" stays visible instead of vanishing."""

    def test_totals_aggregate_across_claims(self) -> None:
        bundle = EvidenceBundleResponse(
            responses=(
                EvidenceResponse(
                    claim_id="claim_001",
                    evidence=(item(),),
                    sources=(source(),),
                ),
                EvidenceResponse(claim_id="claim_002"),
            )
        )
        assert bundle.evidence_count == 1
        assert bundle.claims_with_evidence == 1
        assert bundle.claims_without_evidence == 1

    def test_two_responses_for_one_claim_are_rejected(self) -> None:
        """A duplicate would double-count the same evidence."""
        with pytest.raises(ValidationError, match="duplicate responses"):
            EvidenceBundleResponse(
                responses=(
                    EvidenceResponse(claim_id="claim_001"),
                    EvidenceResponse(claim_id="claim_001"),
                )
            )


class TestVocabularies:
    """The three axes are small, closed and non-overlapping."""

    def test_the_five_record_types(self) -> None:
        assert {member.value for member in EvidenceType} == {
            "REGULATORY_RECORD",
            "GOVERNMENT_RECORD",
            "EXCHANGE_RECORD",
            "OFFICIAL_ENTITY_SOURCE",
            "SEARCH_RESULT",
        }

    def test_the_five_relationships(self) -> None:
        assert len(EvidenceRelation) == 5

    def test_the_three_relevance_levels(self) -> None:
        assert [member.value for member in EvidenceRelevance] == ["HIGH", "MEDIUM", "LOW"]

    def test_every_relationship_has_a_sort_rank(self) -> None:
        assert set(RELATION_RANK) == set(EvidenceRelation)

    def test_every_relevance_has_a_sort_rank(self) -> None:
        assert set(RELEVANCE_RANK) == set(EvidenceRelevance)

    def test_support_outranks_conflict_outranks_context(self) -> None:
        assert (
            RELATION_RANK[EvidenceRelation.SUPPORTS]
            < RELATION_RANK[EvidenceRelation.CONTRADICTS]
            < RELATION_RANK[EvidenceRelation.MENTIONS]
        )

    def test_relevance_ranks_run_most_to_least_direct(self) -> None:
        assert (
            RELEVANCE_RANK[EvidenceRelevance.HIGH]
            < RELEVANCE_RANK[EvidenceRelevance.MEDIUM]
            < RELEVANCE_RANK[EvidenceRelevance.LOW]
        )


def test_status_does_not_change_what_an_item_means() -> None:
    """Phase 4's status is carried, never used to re-judge the document."""
    assert make_verification().status is VerificationStatus.VERIFIED
    assert not_applicable_verification().status is VerificationStatus.NOT_APPLICABLE
