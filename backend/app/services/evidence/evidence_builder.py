"""Evidence construction from Phase 3/4 output (Phase 5, §7/§8).

The builder is the only component that turns retrieved text into an
`EvidenceItem`, and it is where the no-fabrication rule is enforced in code
rather than in a comment.

**Every excerpt is a verbatim slice of a `SearchResult` field.** The builder
records which field in `excerpt_origin`, and it does not concatenate title and
snippet, trim to a "relevant" sentence, drop ellipses or normalise punctuation
— because the moment a snippet is rewritten, the user can no longer check it
against the page it claims to come from. If a provider returns no snippet, the
title is used and *that* is recorded.

Inputs are Phase 3 `SearchResult` objects and Phase 4 `VerificationResult`
objects. The builder adds no network call, no LLM call, and no new judgement.
"""

from __future__ import annotations

import hashlib

from app.schemas.claims import ClaimType
from app.schemas.evidence import (
    EVIDENCE_ID_DIGEST_LENGTH,
    EVIDENCE_ID_PREFIX,
    EvidenceItem,
    EvidenceSource,
)
from app.schemas.search import SearchResult
from app.schemas.verification import VerificationResult, VerificationStatus
from app.services.evidence.relationship import (
    evidence_type_for,
    relevance_for,
    relationship_for,
)
from app.services.evidence.source_normalizer import normalize_source
from app.services.verification.comparator import AssessedSource

#: Longest excerpt stored, in characters. Providers return long snippets; a
#: bounded excerpt keeps an evidence bundle readable while always keeping a
#: prefix of the real text, so it remains verifiable. The cut is a plain
#: truncation at a word boundary — never a rephrasing.
MAX_EXCERPT_CHARS = 400


def evidence_id_for(
    claim_id: str,
    source: EvidenceSource,
    excerpt_origin: str,
    relationship: str,
    excerpt: str,
) -> str:
    """Return a stable ``ev_`` id for one evidence item.

    Derived only from the claim, the source, where the excerpt came from, the
    relationship and the excerpt itself. No counter, no clock, no random salt —
    so rebuilding the same evidence from the same inputs yields the same id, and
    two genuinely different items can never collide by accident.

    Args:
        claim_id: The claim this item bears on.
        source: The normalized source.
        excerpt_origin: `snippet` or `title`.
        relationship: The derived relationship value.
        excerpt: The verbatim excerpt.

    Returns:
        ``ev_<12 hex chars>``.
    """
    parts = "\x1f".join(
        (
            claim_id,
            source.source_id,
            source.result_id,
            excerpt_origin,
            relationship,
            excerpt.strip(),
        )
    )
    digest = hashlib.sha256(parts.encode("utf-8")).hexdigest()
    return f"{EVIDENCE_ID_PREFIX}{digest[:EVIDENCE_ID_DIGEST_LENGTH]}"


def excerpt_for(result: SearchResult) -> tuple[str, str]:
    """Return the verbatim excerpt for a result and the field it came from.

    Preference order is fixed and documented: the provider's snippet when there
    is one, otherwise the title. The two are never merged, because a stitched
    excerpt reads as a single sentence from a single place and is not.

    Args:
        result: A Phase 3 search result.

    Returns:
        ``(excerpt, origin)``. `origin` is ``snippet`` or ``title``. Both fields
        are non-empty for any valid `SearchResult`.

    Raises:
        ValueError: If the result carries neither a snippet nor a title, which
            would mean there is no retrieved text to cite.
    """
    snippet = (result.snippet or "").strip()
    if snippet:
        return _truncate(snippet), "snippet"
    title = (result.title or "").strip()
    if title:
        return _truncate(title), "title"
    raise ValueError("cannot build evidence from a result with no retrieved text")


def _truncate(text: str) -> str:
    """Shorten `text` to `MAX_EXCERPT_CHARS`, cutting on a word boundary.

    Args:
        text: Retrieved text.

    Returns:
        The text unchanged, or a prefix of it. Never a reworded version.
    """
    if len(text) <= MAX_EXCERPT_CHARS:
        return text
    window = text[:MAX_EXCERPT_CHARS]
    boundary = window.rfind(" ")
    if boundary > MAX_EXCERPT_CHARS // 2:
        window = window[:boundary]
    return f"{window.rstrip()}..."


def build_evidence_item(
    result: SearchResult,
    assessed: AssessedSource,
    verification: VerificationResult,
    *,
    claim_type: ClaimType | None = None,
    provider_query: str | None = None,
) -> EvidenceItem:
    """Build one `EvidenceItem` from a result and its Phase 4 assessment.

    Args:
        result: The Phase 3 `SearchResult`. The excerpt comes from here and
            nowhere else.
        assessed: Phase 4's `AssessedSource` for this result. Passed in rather
            than recomputed so the caller controls the target used for
            comparison, and so this function needs no claim context of its own.
        verification: The claim's Phase 4 `VerificationResult`.
        claim_type: Claim family, defaulting to the verification result's.
        provider_query: The query that surfaced this result, when known.

    Returns:
        A frozen `EvidenceItem` with full provenance.
    """
    source = normalize_source(result)
    excerpt, origin = excerpt_for(result)
    relationship = relationship_for(
        assessed, verification.status, verification.reason_code
    )

    return EvidenceItem(
        id=evidence_id_for(
            verification.claim_id, source, origin, relationship.value, excerpt
        ),
        claim_id=verification.claim_id,
        verification_status=verification.status,
        claim_type=claim_type or verification.claim_type,
        evidence_type=evidence_type_for(assessed),
        relationship=relationship,
        relevance=relevance_for(assessed, relationship),
        excerpt=excerpt,
        excerpt_origin=origin,
        source=source,
        matched_cue=assessed.matched_cue,
        provider_query=provider_query,
    )


def group_by_query(
    responses: tuple[object, ...],
) -> dict[str, tuple[SearchResult, ...]]:
    """Index results by the query that returned them.

    Evidence records which query surfaced a document, which is the difference
    between "we found this" and "we found this *when we asked the register*".

    Args:
        responses: Phase 3 `SearchResponse` objects, or anything else exposing
            a ``query`` and a ``results`` tuple.

    Returns:
        Maps each non-empty query string to its results, in input order. A
        result that reached the same query twice is recorded once.
    """
    indexed: dict[str, tuple[SearchResult, ...]] = {}
    for response in responses:
        query = getattr(response, "query", "") or ""
        results = tuple(getattr(response, "results", ()) or ())
        if not query or not results:
            continue
        existing = indexed.get(query, ())
        indexed[query] = existing + tuple(
            item for item in results if item not in existing
        )
    return indexed


def status_supports_evidence(status: VerificationStatus) -> bool:
    """Whether any evidence at all may exist for a claim in `status`.

    `NOT_APPLICABLE` claims are instructions or opinions; no record was ever
    looked up for them, so any evidence attached would have had to be invented.
    This function exists to make that impossible to do by accident.

    Args:
        status: Phase 4's status for the claim.

    Returns:
        False for `NOT_APPLICABLE`, True otherwise.
    """
    return status is not VerificationStatus.NOT_APPLICABLE


__all__ = [
    "MAX_EXCERPT_CHARS",
    "build_evidence_item",
    "evidence_id_for",
    "excerpt_for",
    "group_by_query",
    "status_supports_evidence",
]
