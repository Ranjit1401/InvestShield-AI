"""Relationship, relevance and record-type derivation (Phase 5).

This module is the only place in Phase 5 that decides what a retrieved document
*means*, and its single input is Phase 4's `AssessedSource`. The tests below
exist to prove it cannot decide anything Phase 4 did not: a document Phase 4 read
as neutral cannot become support here, and a supporting cue found in a document
that failed an authority or identity gate cannot be presented as proof.
"""

from __future__ import annotations

import pytest

from app.schemas.evidence import EvidenceRelation, EvidenceRelevance, EvidenceType
from app.schemas.verification import (
    AUTHORITATIVE_SOURCE_CONFIRMS,
    AUTHORITATIVE_SOURCE_CONTRADICTS,
    CONFLICTING_AUTHORITATIVE_SOURCES,
    IDENTITY_NOT_FOUND,
    NO_CLAIM_RELEVANT_SOURCE,
    NOT_A_FACTUAL_CLAIM,
    ZERO_RESULTS,
    IdentityMatch,
    SourceTier,
    VerificationStatus,
)
from app.services.evidence.relationship import (
    PROBATIVE_REASON_CODES,
    PROBATIVE_RELATIONS,
    evidence_type_for,
    is_probative,
    relevance_for,
    relationship_for,
)
from tests.evidence_factories import Stance, make_assessed

ACME = "https://www.sebi.gov.in/intermediaries/acme"


def relation_for(assessed: object, status: VerificationStatus, reason: str) -> EvidenceRelation:
    """Shorthand for deriving a relationship from a Phase 4 assessment."""
    return relationship_for(assessed, status, reason)  # type: ignore[arg-type]


class TestProbativeGate:
    """`SUPPORTS`/`CONTRADICTS` require every Phase 4 gate to have passed."""

    def test_a_supporting_authoritative_record_is_probative(self) -> None:
        assessed = make_assessed(ACME, stance=Stance.SUPPORTS)
        assert is_probative(
            assessed, VerificationStatus.VERIFIED, AUTHORITATIVE_SOURCE_CONFIRMS
        )

    def test_a_neutral_document_is_never_probative(self) -> None:
        assessed = make_assessed(ACME, stance=Stance.NEUTRAL, matched_cue=None)
        assert not is_probative(
            assessed, VerificationStatus.VERIFIED, AUTHORITATIVE_SOURCE_CONFIRMS
        )

    def test_no_cue_at_all_is_never_probative(self) -> None:
        assessed = make_assessed(ACME, stance="", matched_cue=None)
        assert not is_probative(
            assessed, VerificationStatus.VERIFIED, AUTHORITATIVE_SOURCE_CONFIRMS
        )

    def test_an_unmatched_identity_is_never_probative(self) -> None:
        """The gates exist so one party's page cannot confirm another's claim."""
        assessed = make_assessed(ACME, identity=IdentityMatch.AMBIGUOUS)
        assert not is_probative(
            assessed, VerificationStatus.VERIFIED, AUTHORITATIVE_SOURCE_CONFIRMS
        )

    def test_an_absent_identity_is_never_probative(self) -> None:
        assessed = make_assessed(ACME, identity=IdentityMatch.ABSENT)
        assert not is_probative(
            assessed, VerificationStatus.VERIFIED, AUTHORITATIVE_SOURCE_CONFIRMS
        )

    def test_an_unofficial_publisher_is_never_probative(self) -> None:
        """A supporting cue on a random site is not confirmation."""
        assessed = make_assessed(
            "https://random-blog.example/post",
            tier=SourceTier.TIER_6_GENERAL_WEB,
            authority_key=None,
            authority_name=None,
            claim_relevant=False,
        )
        assert not is_probative(
            assessed, VerificationStatus.VERIFIED, AUTHORITATIVE_SOURCE_CONFIRMS
        )

    def test_a_claim_irrelevant_authority_is_never_probative(self) -> None:
        assessed = make_assessed(ACME, claim_relevant=False)
        assert not is_probative(
            assessed, VerificationStatus.VERIFIED, AUTHORITATIVE_SOURCE_CONFIRMS
        )

    @pytest.mark.parametrize(
        "reason",
        [IDENTITY_NOT_FOUND, NO_CLAIM_RELEVANT_SOURCE, ZERO_RESULTS, NOT_A_FACTUAL_CLAIM],
    )
    def test_a_reason_phase_four_never_relied_on_a_source(self, reason: str) -> None:
        """These outcomes mean nothing was relied on, so nothing is proof."""
        assessed = make_assessed(ACME, stance=Stance.SUPPORTS)
        assert not is_probative(assessed, VerificationStatus.UNVERIFIED, reason)

    def test_an_unverified_status_blocks_proof_even_with_a_supporting_cue(self) -> None:
        """Phase 4 left the claim unverified "for no matching source"."""
        assessed = make_assessed(ACME, stance=Stance.SUPPORTS)
        assert not is_probative(assessed, VerificationStatus.UNVERIFIED, IDENTITY_NOT_FOUND)

    def test_a_not_applicable_status_is_never_probative(self) -> None:
        assessed = make_assessed(ACME, stance=Stance.SUPPORTS)
        assert not is_probative(
            assessed, VerificationStatus.NOT_APPLICABLE, NOT_A_FACTUAL_CLAIM
        )

    @pytest.mark.parametrize(
        "reason", sorted(PROBATIVE_REASON_CODES)
    )
    def test_the_probative_reason_codes_are_the_authoritative_ones(
        self, reason: str
    ) -> None:
        assert reason in {
            AUTHORITATIVE_SOURCE_CONFIRMS,
            AUTHORITATIVE_SOURCE_CONTRADICTS,
            CONFLICTING_AUTHORITATIVE_SOURCES,
        }


class TestRelationship:
    """The mapping from Phase 4's assessment to a relationship."""

    def test_a_supporting_record_supports(self) -> None:
        assessed = make_assessed(ACME, stance=Stance.SUPPORTS)
        assert (
            relation_for(assessed, VerificationStatus.VERIFIED, AUTHORITATIVE_SOURCE_CONFIRMS)
            is EvidenceRelation.SUPPORTS
        )

    def test_a_contradicting_record_contradicts(self) -> None:
        assessed = make_assessed(ACME, stance=Stance.CONTRADICTS)
        assert (
            relation_for(
                assessed,
                VerificationStatus.CONTRADICTED,
                AUTHORITATIVE_SOURCE_CONTRADICTS,
            )
            is EvidenceRelation.CONTRADICTS
        )

    def test_both_sides_of_a_conflict_are_kept(self) -> None:
        """A disagreement is reported as a disagreement, not resolved by fiat."""
        reason = CONFLICTING_AUTHORITATIVE_SOURCES
        status = VerificationStatus.CONTRADICTED
        supporting = make_assessed(ACME, stance=Stance.SUPPORTS)
        conflicting = make_assessed(
            "https://www.sebi.gov.in/orders/acme", stance=Stance.CONTRADICTS
        )
        assert relation_for(supporting, status, reason) is EvidenceRelation.SUPPORTS
        assert relation_for(conflicting, status, reason) is EvidenceRelation.CONTRADICTS

    def test_a_matched_but_silent_record_is_an_identity_reference(self) -> None:
        """About the right entity, says nothing about the claim."""
        assessed = make_assessed(
            ACME, identity=IdentityMatch.MATCHED, stance=Stance.NEUTRAL, matched_cue=None
        )
        assert (
            relation_for(assessed, VerificationStatus.INSUFFICIENT_EVIDENCE, ZERO_RESULTS)
            is EvidenceRelation.IDENTITY_REFERENCE
        )

    def test_a_similarly_named_party_is_context(self) -> None:
        assessed = make_assessed(
            "https://www.sebi.gov.in/intermediaries/acme-holdings",
            identity=IdentityMatch.AMBIGUOUS,
            stance=Stance.NEUTRAL,
            matched_cue=None,
        )
        assert (
            relation_for(
                assessed, VerificationStatus.UNVERIFIED, IDENTITY_NOT_FOUND
            )
            is EvidenceRelation.CONTEXT
        )

    def test_an_unrelated_record_is_only_mentioned(self) -> None:
        assessed = make_assessed(
            "https://www.sebi.gov.in/circulars/returns",
            identity=IdentityMatch.ABSENT,
            stance=Stance.NEUTRAL,
            matched_cue=None,
        )
        assert (
            relation_for(
                assessed,
                VerificationStatus.INSUFFICIENT_EVIDENCE,
                NO_CLAIM_RELEVANT_SOURCE,
            )
            is EvidenceRelation.MENTIONS
        )

    def test_a_supporting_cue_on_an_unrelated_blog_page_is_only_mentioned(self) -> None:
        """The single most important narrowing in the layer."""
        assessed = make_assessed(
            "https://random-blog.example/acme",
            identity=IdentityMatch.AMBIGUOUS,
            tier=SourceTier.TIER_6_GENERAL_WEB,
            authority_key=None,
            authority_name=None,
            claim_relevant=False,
            stance=Stance.SUPPORTS,
        )
        assert (
            relation_for(assessed, VerificationStatus.UNVERIFIED, IDENTITY_NOT_FOUND)
            is EvidenceRelation.MENTIONS
        )

    def test_a_supporting_cue_on_a_lookalike_regulator_page_is_context(self) -> None:
        """A regulator's page about a *different* company informs but proves nothing."""
        assessed = make_assessed(
            "https://www.sebi.gov.in/intermediaries/acme-holdings",
            identity=IdentityMatch.AMBIGUOUS,
            stance=Stance.SUPPORTS,
        )
        assert (
            relation_for(assessed, VerificationStatus.UNVERIFIED, IDENTITY_NOT_FOUND)
            is EvidenceRelation.CONTEXT
        )

    def test_ambiguity_on_a_general_web_page_is_not_context(self) -> None:
        """`CONTEXT` is reserved for a relevant authority, not any similar name."""
        assessed = make_assessed(
            "https://random-blog.example/acme",
            identity=IdentityMatch.AMBIGUOUS,
            tier=SourceTier.TIER_6_GENERAL_WEB,
            authority_key=None,
            stance=Stance.NEUTRAL,
            matched_cue=None,
        )
        assert (
            relation_for(assessed, VerificationStatus.UNVERIFIED, IDENTITY_NOT_FOUND)
            is EvidenceRelation.MENTIONS
        )

    def test_a_neutral_record_is_never_promoted_by_phase_five(self) -> None:
        """The only inputs are Phase 4's fields; no text is re-read here."""
        assessed = make_assessed(
            ACME, identity=IdentityMatch.MATCHED, stance=Stance.NEUTRAL, matched_cue=None
        )
        assert relation_for(
            assessed, VerificationStatus.VERIFIED, AUTHORITATIVE_SOURCE_CONFIRMS
        ) is not EvidenceRelation.SUPPORTS

    def test_every_relationship_is_reachable(self) -> None:
        """The mapping covers the assessment space, so nothing falls through."""
        produced = set()
        for identity in IdentityMatch:
            for stance in (Stance.SUPPORTS, Stance.CONTRADICTS, Stance.NEUTRAL):
                for status, reason in (
                    (VerificationStatus.VERIFIED, AUTHORITATIVE_SOURCE_CONFIRMS),
                    (VerificationStatus.CONTRADICTED, AUTHORITATIVE_SOURCE_CONTRADICTS),
                    (VerificationStatus.UNVERIFIED, IDENTITY_NOT_FOUND),
                    (VerificationStatus.INSUFFICIENT_EVIDENCE, NO_CLAIM_RELEVANT_SOURCE),
                ):
                    produced.add(
                        relation_for(make_assessed(ACME, identity=identity, stance=stance), status, reason)
                    )
        assert produced == set(EvidenceRelation)


class TestRelevance:
    """Relevance describes closeness to the claim, never weight or risk."""

    def test_probative_items_are_highly_relevant(self) -> None:
        for relation in PROBATIVE_RELATIONS:
            assert relevance_for(make_assessed(ACME), relation) is EvidenceRelevance.HIGH

    def test_an_identity_reference_is_medium(self) -> None:
        assert (
            relevance_for(make_assessed(ACME), EvidenceRelation.IDENTITY_REFERENCE)
            is EvidenceRelevance.MEDIUM
        )

    @pytest.mark.parametrize(
        "relation", [EvidenceRelation.CONTEXT, EvidenceRelation.MENTIONS]
    )
    def test_context_and_mentions_are_low(self, relation: EvidenceRelation) -> None:
        assert relevance_for(make_assessed(ACME), relation) is EvidenceRelevance.LOW

    def test_relevance_is_not_derived_from_the_publisher_tier(self) -> None:
        """A regulator's irrelevant page is not more relevant than a match."""
        authoritative = make_assessed(ACME)
        unofficial = make_assessed(
            "https://random-blog.example/post",
            tier=SourceTier.TIER_6_GENERAL_WEB,
            authority_key=None,
        )
        assert relevance_for(authoritative, EvidenceRelation.MENTIONS) is (
            relevance_for(unofficial, EvidenceRelation.MENTIONS)
        )


class TestEvidenceTypeDerivation:
    """Record type comes from the tier, and adds no new information."""

    def test_a_tier_one_record_is_a_regulatory_record(self) -> None:
        assessed = make_assessed(ACME, tier=SourceTier.TIER_1_PRIMARY_REGULATOR)
        assert evidence_type_for(assessed) is EvidenceType.REGULATORY_RECORD

    def test_a_general_web_page_is_a_plain_search_result(self) -> None:
        assessed = make_assessed(
            "https://random-blog.example/post", tier=SourceTier.TIER_6_GENERAL_WEB
        )
        assert evidence_type_for(assessed) is EvidenceType.SEARCH_RESULT

    def test_the_type_does_not_vary_with_stance(self) -> None:
        """One document, one record type — the relationship carries the rest."""
        supporting = make_assessed(ACME, stance=Stance.SUPPORTS)
        neutral = make_assessed(ACME, stance=Stance.NEUTRAL, matched_cue=None)
        assert evidence_type_for(supporting) is evidence_type_for(neutral)
