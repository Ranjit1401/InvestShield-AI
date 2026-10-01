"""Source normalization for evidence (Phase 5, §5/§13).

Turns a Phase 3 `SearchResult` into an `EvidenceSource` — a *named, traceable*
document — without re-deciding anything about it.

Three Phase 3/4 rules are reused rather than reimplemented, because a second
implementation of any of them is a second answer to the same question:

- `canonicalize_url()` (Phase 3) decides URL identity, so
  `https://example.com/page` and `https://example.com/page/` collapse to one
  source here exactly as they do in search de-duplication.
- `source_id_for()` / `result_id_for()` (Phase 4) mint the `src_` and `res_`
  identifiers, so the ids a reader sees in a report are the same ones Phase 4
  cited in `VerificationResult.source_ids`.
- `resolve_authority()` (Phase 4) assigns the authority tier, so evidence can
  never tier a host differently from the layer that was allowed to act on it
  (D-018).

Nothing here fetches a page. The system has only ever seen the title and the
snippet the provider returned, and that is exactly what an `EvidenceSource`
records (D-023).
"""

from __future__ import annotations

from app.schemas.evidence import EvidenceType, EvidenceSource
from app.schemas.search import SearchResult
from app.schemas.verification import SourceTier
from app.services.search.domain import canonicalize_url, extract_domain
from app.services.verification.authority_registry import resolve_authority
from app.services.verification.comparator import result_id_for, source_id_for

#: Maps Phase 4's authority tiers onto the evidence record categories.
#:
#: Derived from the tier rather than decided independently, so there is exactly
#: one authority classification in the system. Tiers 4+ and everything
#: unclassified collapse into `SEARCH_RESULT`: a page may be perfectly good
#: context without being a record anyone can settle a claim on.
EVIDENCE_TYPE_BY_TIER: dict[SourceTier, EvidenceType] = {
    SourceTier.TIER_1_PRIMARY_REGULATOR: EvidenceType.REGULATORY_RECORD,
    SourceTier.TIER_2_GOVERNMENT: EvidenceType.GOVERNMENT_RECORD,
    SourceTier.TIER_3_EXCHANGE: EvidenceType.EXCHANGE_RECORD,
    SourceTier.TIER_4_OFFICIAL_ENTITY: EvidenceType.OFFICIAL_ENTITY_SOURCE,
}

#: Fallback for every tier that is not a recognised record.
DEFAULT_EVIDENCE_TYPE = EvidenceType.SEARCH_RESULT


def canonical_url_for(result: SearchResult) -> str:
    """Return the canonical URL of a search result.

    Prefers the URL Phase 3 already canonicalised, so evidence keys line up with
    search de-duplication. Falls back to re-canonicalising `url` when a caller
    built a `SearchResult` by hand without the derived field.

    Args:
        result: A Phase 3 search result.

    Returns:
        The canonical URL, guaranteed non-empty.
    """
    if result.canonical_url.strip():
        return result.canonical_url
    return canonicalize_url(result.url) or result.url.strip()


def evidence_type_for_tier(tier: SourceTier) -> EvidenceType:
    """Return the record category implied by an authority tier.

    Args:
        tier: Phase 4 authority tier.

    Returns:
        The matching `EvidenceType`, or `SEARCH_RESULT`.
    """
    return EVIDENCE_TYPE_BY_TIER.get(tier, DEFAULT_EVIDENCE_TYPE)


def normalize_source(result: SearchResult) -> EvidenceSource:
    """Convert a Phase 3 `SearchResult` into a traceable `EvidenceSource`.

    Args:
        result: A retrieved result. Every field is copied or derived; nothing is
            invented and no page is fetched.

    Returns:
        An `EvidenceSource` carrying Phase 3's URL, title, domain, publisher
        category and retrieval time, plus Phase 4's authority tier and the
        ``src_``/``res_`` ids.
    """
    canonical = canonical_url_for(result)
    tier, _authority = resolve_authority(result.source_domain or result.url)
    domain = result.source_domain or extract_domain(result.url) or ""

    return EvidenceSource(
        source_id=source_id_for(canonical),
        result_id=result_id_for(canonical),
        url=result.url,
        canonical_url=canonical,
        domain=domain,
        title=result.title,
        source_type=result.source_type,
        source_tier=tier,
        retrieved_at=result.retrieved_at,
        position=result.position,
    )


__all__ = [
    "DEFAULT_EVIDENCE_TYPE",
    "EVIDENCE_TYPE_BY_TIER",
    "canonical_url_for",
    "evidence_type_for_tier",
    "normalize_source",
]
