"""Object factories shared by the Phase 5 evidence tests.

Evidence consumes Phase 3 `SearchResult`/`SearchResponse` objects and Phase 4
`VerificationResult` objects, and derives everything else itself. These factories
re-export the Phase 4 ones and add the two Phase 4 shapes the evidence layer
needs most often: an `AssessedSource` describing what Phase 4 made of a document,
and a `VerificationResult` with a coherent status/reason pairing.

Every factory produces a *consistent* object, so a test only has to state the
one thing it is about. There is no network, no provider and no LLM here.
"""

from __future__ import annotations

from app.schemas.claims import ClaimType
from app.schemas.search import SearchResult
from app.schemas.verification import (
    AUTHORITATIVE_SOURCE_CONFIRMS,
    AUTHORITATIVE_SOURCE_CONTRADICTS,
    CONFLICTING_AUTHORITATIVE_SOURCES,
    IDENTITY_NOT_FOUND,
    NO_CLAIM_RELEVANT_SOURCE,
    NOT_A_FACTUAL_CLAIM,
    SEARCH_UNAVAILABLE,
    ZERO_RESULTS,
    IdentityMatch,
    SourceTier,
    VerificationResult,
    VerificationStatus,
)
from app.services.search.domain import canonicalize_url
from app.services.verification.comparator import (
    AssessedSource,
    Stance,
    result_id_for,
    source_id_for,
)
from tests.verification_factories import (
    error_response,
    make_claim,
    make_entity,
    make_result,
    ok_response,
    unavailable_response,
)


def make_assessed(
    url: str,
    *,
    title: str = "A record",
    domain: str = "",
    tier: SourceTier = SourceTier.TIER_1_PRIMARY_REGULATOR,
    authority_key: str = "SEBI",
    authority_name: str = "Securities and Exchange Board of India",
    identity: IdentityMatch = IdentityMatch.MATCHED,
    claim_relevant: bool = True,
    stance: str = Stance.SUPPORTS,
    matched_cue: str | None = "registered as",
    position: int = 1,
) -> AssessedSource:
    """Build a Phase 4 `AssessedSource` for one document.

    Args:
        url: The document's URL; the ``src_``/``res_`` ids are derived from its
            canonical form exactly as Phase 4 derives them.
        title: Document title.
        domain: Publisher hostname. Derived from `url` when omitted.
        tier: Phase 4 authority tier.
        authority_key: Registry key, or `None` for an unclassified publisher.
        authority_name: Human-readable authority name.
        identity: Whether the document is about the claimed entity.
        claim_relevant: Whether this authority's records can speak to the claim.
        stance: `Stance.SUPPORTS`, `Stance.CONTRADICTS` or `Stance.NEUTRAL`.
        matched_cue: The cue that produced the stance.
        position: Provider rank.

    Returns:
        An `AssessedSource` whose ids match those Phase 4 would mint.
    """
    canonical = canonicalize_url(url) or url
    return AssessedSource(
        source_id=source_id_for(canonical),
        result_id=result_id_for(canonical),
        url=url,
        canonical_url=canonical,
        title=title,
        source_domain=domain or (canonical.split("/")[2] if "//" in canonical else ""),
        tier=tier,
        authority_key=authority_key,
        authority_name=authority_name,
        identity=identity,
        claim_relevant=claim_relevant,
        stance=stance,
        matched_cue=matched_cue,
        position=position,
    )


def make_verification(
    claim_id: str = "claim_001",
    *,
    claim_type: ClaimType = ClaimType.REGULATORY_STATUS,
    status: VerificationStatus = VerificationStatus.VERIFIED,
    reason_code: str = AUTHORITATIVE_SOURCE_CONFIRMS,
    reason: str = "An authoritative record confirms the claim.",
    confidence: float = 0.8,
    source_ids: tuple[str, ...] = ("src_000000000001",),
    matched_result_ids: tuple[str, ...] = ("res_000000000001",),
    queries: tuple[str, ...] = ("Acme Capital Advisors SEBI registration",),
    warnings: tuple[str, ...] = (),
) -> VerificationResult:
    """Build a Phase 4 `VerificationResult`.

    Defaults describe a `VERIFIED` claim. Pass an explicit `status` with the
    matching `reason_code` for anything else; the schema rejects incoherent
    pairings, which is exactly what the evidence tests want to rely on.

    Args:
        claim_id: The claim described.
        claim_type: The claim family.
        status: Phase 4 outcome.
        reason_code: Structured cause; must be known to Phase 4.
        reason: Plain-language explanation.
        confidence: Confidence in the assessment.
        source_ids: Source documents relied on.
        matched_result_ids: Results that addressed the claim.
        queries: Queries issued.
        warnings: Claim-level limitations.

    Returns:
        A `VerificationResult` that passes Phase 4's own consistency checks.
    """
    return VerificationResult(
        claim_id=claim_id,
        claim_type=claim_type,
        status=status,
        reason=reason,
        reason_code=reason_code,
        confidence=confidence,
        source_ids=source_ids,
        matched_result_ids=matched_result_ids,
        queries=queries,
        warnings=warnings,
    )


def unavailable_verification(claim_id: str = "claim_001") -> VerificationResult:
    """A result for a claim whose search never ran."""
    return make_verification(
        claim_id,
        status=VerificationStatus.INSUFFICIENT_EVIDENCE,
        reason_code=SEARCH_UNAVAILABLE,
        reason="External web search is not configured.",
        confidence=0.0,
        source_ids=(),
        matched_result_ids=(),
        queries=(),
    )


def zero_results_verification(claim_id: str = "claim_001") -> VerificationResult:
    """A result for a claim whose authoritative registers had no entry."""
    return make_verification(
        claim_id,
        status=VerificationStatus.INSUFFICIENT_EVIDENCE,
        reason_code=ZERO_RESULTS,
        reason="The authoritative records searched returned no matching entry.",
        confidence=0.1,
        source_ids=(),
        matched_result_ids=(),
    )


def identity_not_found_verification(claim_id: str = "claim_001") -> VerificationResult:
    """A result for a claim where no source identified the claimed party."""
    return make_verification(
        claim_id,
        status=VerificationStatus.UNVERIFIED,
        reason_code=IDENTITY_NOT_FOUND,
        reason="No retrieved source identified the claimed entity.",
        confidence=0.0,
        source_ids=(),
        matched_result_ids=(),
    )


def not_applicable_verification(claim_id: str = "claim_001") -> VerificationResult:
    """A result for a claim that was never externally verifiable."""
    return make_verification(
        claim_id,
        claim_type=ClaimType.RETURN_PROMISE,
        status=VerificationStatus.NOT_APPLICABLE,
        reason_code=NOT_A_FACTUAL_CLAIM,
        reason="This statement is not externally verifiable.",
        confidence=0.0,
        source_ids=(),
        matched_result_ids=(),
        queries=(),
    )


def contradictory_verification(
    claim_id: str = "claim_001",
    *,
    conflicting: bool = False,
) -> VerificationResult:
    """A `CONTRADICTED` result, optionally from conflicting authorities."""
    return make_verification(
        claim_id,
        status=VerificationStatus.CONTRADICTED,
        reason_code=(
            CONFLICTING_AUTHORITATIVE_SOURCES if conflicting
            else AUTHORITATIVE_SOURCE_CONTRADICTS
        ),
        reason="An authoritative record conflicts with the claim.",
        source_ids=("src_000000000001", "src_000000000002") if conflicting
        else ("src_000000000002",),
        matched_result_ids=("res_000000000001", "res_000000000002") if conflicting
        else ("res_000000000002",),
    )


def no_relevant_source_verification(claim_id: str = "claim_001") -> VerificationResult:
    """A result for a claim where no authority could speak to it."""
    return make_verification(
        claim_id,
        status=VerificationStatus.INSUFFICIENT_EVIDENCE,
        reason_code=NO_CLAIM_RELEVANT_SOURCE,
        reason="No retrieved authority can establish this claim family.",
        confidence=0.0,
        source_ids=(),
        matched_result_ids=(),
    )


__all__ = [
    "AssessedSource",
    "IdentityMatch",
    "SearchResult",
    "SourceTier",
    "Stance",
    "VerificationResult",
    "VerificationStatus",
    "contradictory_verification",
    "error_response",
    "identity_not_found_verification",
    "make_assessed",
    "make_claim",
    "make_entity",
    "make_result",
    "make_verification",
    "no_relevant_source_verification",
    "not_applicable_verification",
    "ok_response",
    "unavailable_response",
    "unavailable_verification",
    "zero_results_verification",
]
