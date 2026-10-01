"""Relationship, relevance and record-type derivation (Phase 5, §2/§3/§10).

This module is deliberately the *only* place in Phase 5 that decides what a
retrieved document means. Its single input is Phase 4's `AssessedSource` — the
object Phase 4's comparator produced after applying its identity, authority and
relevance gates.

Nothing here re-derives a stance from text, re-detects a cue, or re-classifies a
publisher. If Phase 4 said a document was `NEUTRAL`, this module cannot turn it
into `SUPPORTS` by reading the snippet a different way. Evidence explains
verification; it never re-decides it (D-020, D-021, D-023).

The mapping:

| Phase 4 assessment | Relationship | Relevance | Why |
| --- | --- | --- | --- |
| `stance == SUPPORTS` | `SUPPORTS` | `HIGH` | An identity-matched, claim-relevant authoritative record reads as supporting the claim |
| `stance == CONTRADICTS` | `CONTRADICTS` | `HIGH` | …or as directly conflicting with it |
| `identity == MATCHED`, no stance | `IDENTITY_REFERENCE` | `MEDIUM` | The document is about the right entity but is silent on the claim |
| `identity == AMBIGUOUS`, authoritative | `CONTEXT` | `LOW` | A relevant authority documents a *similarly named* party |
| anything else | `MENTIONS` | `LOW` | No relationship to the claim could be established |

The one place this module is allowed to *narrow* Phase 4 is the consistency
check in `relationship_for()`: a document Phase 4 read as supporting cannot be
presented as support for a claim Phase 4 left `UNVERIFIED` for "no matching
source". A supporting cue found in a document that failed an authority or
identity gate is context, not proof — the gates exist precisely so that one
party's page cannot confirm another party's claim.
"""

from __future__ import annotations

from app.schemas.evidence import (
    EvidenceRelation,
    EvidenceRelevance,
    EvidenceType,
)
from app.schemas.verification import IdentityMatch, VerificationStatus
from app.services.verification.comparator import AssessedSource, Stance
from app.services.evidence.source_normalizer import evidence_type_for_tier

#: Relationships that mean "this document speaks to the claim".
PROBATIVE_RELATIONS = frozenset(
    {EvidenceRelation.SUPPORTS, EvidenceRelation.CONTRADICTS}
)

#: Reason codes for which Phase 4 explicitly relied on a source. Only these may
#: be represented as `SUPPORTS` / `CONTRADICTS`, so a support cue buried in a
#: document that Phase 4 *ignored* can never be presented as proof.
PROBATIVE_REASON_CODES = frozenset(
    {
        "AUTHORITATIVE_SOURCE_CONFIRMS",
        "AUTHORITATIVE_SOURCE_CONTRADICTS",
        "CONFLICTING_AUTHORITATIVE_SOURCES",
    }
)


def is_probative(
    assessed: AssessedSource,
    status: VerificationStatus,
    reason_code: str,
) -> bool:
    """Whether Phase 4 actually relied on this document.

    Args:
        assessed: Phase 4's assessment of the document.
        status: Phase 4's status for the claim.
        reason_code: Phase 4's structured reason.

    Returns:
        True only when the document passed every Phase 4 gate *and* the claim's
        verification result was decided on the strength of such documents.
    """
    if not assessed.stance or assessed.stance == Stance.NEUTRAL:
        return False
    if not assessed.claim_relevant or assessed.identity is not IdentityMatch.MATCHED:
        return False
    return reason_code in PROBATIVE_REASON_CODES and status in (
        VerificationStatus.VERIFIED,
        VerificationStatus.CONTRADICTED,
        VerificationStatus.INSUFFICIENT_EVIDENCE,
    )


def relationship_for(
    assessed: AssessedSource,
    status: VerificationStatus,
    reason_code: str,
) -> EvidenceRelation:
    """Derive the relationship between a document and its claim.

    Args:
        assessed: Phase 4's assessment of the document.
        status: Phase 4's status for the claim.
        reason_code: Phase 4's structured reason.

    Returns:
        The `EvidenceRelation` for this document.
    """
    if is_probative(assessed, status, reason_code):
        return (
            EvidenceRelation.SUPPORTS
            if assessed.stance == Stance.SUPPORTS
            else EvidenceRelation.CONTRADICTS
        )

    if assessed.identity is IdentityMatch.MATCHED:
        return EvidenceRelation.IDENTITY_REFERENCE

    if assessed.identity is IdentityMatch.AMBIGUOUS and assessed.is_authoritative:
        # `CONTEXT` is reserved for a *relevant authority* describing a
        # similarly named party. The same ambiguity on a general web page is not
        # context anyone can act on, so it drops to `MENTIONS`.
        return EvidenceRelation.CONTEXT

    return EvidenceRelation.MENTIONS


def relevance_for(
    assessed: AssessedSource,
    relationship: EvidenceRelation,
) -> EvidenceRelevance:
    """Derive how directly an evidence item bears on its claim.

    Args:
        assessed: Phase 4's assessment of the document.
        relationship: The derived relationship.

    Returns:
        `HIGH` for probative items, `MEDIUM` when the document is about the
        right entity but silent on the claim, `LOW` otherwise.
    """
    if relationship in PROBATIVE_RELATIONS:
        return EvidenceRelevance.HIGH
    if relationship is EvidenceRelation.IDENTITY_REFERENCE:
        return EvidenceRelevance.MEDIUM
    return EvidenceRelevance.LOW


def evidence_type_for(assessed: AssessedSource) -> EvidenceType:
    """Return the record category implied by the source's authority tier.

    Args:
        assessed: Phase 4's assessment of the document.

    Returns:
        The `EvidenceType` for this source.
    """
    return evidence_type_for_tier(assessed.tier)


__all__ = [
    "PROBATIVE_REASON_CODES",
    "PROBATIVE_RELATIONS",
    "evidence_type_for",
    "is_probative",
    "relevance_for",
    "relationship_for",
]
