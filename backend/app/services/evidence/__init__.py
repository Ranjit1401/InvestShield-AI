"""Claim → evidence → source wiring (Phase 5).

Public surface for the evidence layer. Consumers import from here, never from a
module inside it:

    from app.services.evidence import EvidenceService, build_evidence_service

Layering, and the reason each piece is separate:

    Claim + Entity ──► build_target()          (Phase 4, reused unchanged)
    SearchResult ────► assess_results()        (Phase 4, reused unchanged)
    Verification ────► relationship_for()      ← the only judgement in Phase 5
                 └──► build_evidence_item()    ← verbatim excerpt + provenance
                 └──► dedupe + order           ← deterministic output
                          ↓
                  EvidenceResponse

The layering exists to make one guarantee checkable: **no evidence without a
retrieved document.** Every `EvidenceItem` is built from a `SearchResult` it was
handed, carries that result's canonical URL, domain, title, publisher category
and retrieval time, and quotes only text the provider actually returned. There is
no summariser, no paraphrase, no enrichment, and no model in this path (D-023).

What this layer deliberately does not do:

- It does not fetch pages. It has never seen anything beyond title and snippet,
  and its excerpts say so via `excerpt_origin`.
- It does not classify sources. `SourceType` is Phase 3's, `SourceTier` is
  Phase 4's, and both are carried through (D-012, D-018).
- It does not decide anything. `relationship` and `relevance` are read off
  Phase 4's `AssessedSource`; `verification_status` is copied, never recomputed
  (D-020, D-021).
- It does not score risk, and `EvidenceRelevance` must never be summed into one
  (D-007).
"""

from __future__ import annotations

from app.schemas.evidence import (
    ALLOWED_EXCERPT_ORIGINS,
    EVIDENCE_ID_DIGEST_LENGTH,
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
from app.services.evidence.evidence_builder import (
    MAX_EXCERPT_CHARS,
    build_evidence_item,
    evidence_id_for,
    excerpt_for,
    group_by_query,
    status_supports_evidence,
)
from app.services.evidence.evidence_dedupe import (
    dedupe_evidence,
    distinct_sources,
    evidence_key,
    excerpt_key,
    order_evidence,
    order_key,
)
from app.services.evidence.evidence_service import (
    NO_EVIDENCE_WARNINGS,
    NO_RESULTS_WARNING,
    EvidenceService,
    build_evidence_service,
)
from app.services.evidence.relationship import (
    PROBATIVE_REASON_CODES,
    PROBATIVE_RELATIONS,
    evidence_type_for,
    is_probative,
    relevance_for,
    relationship_for,
)
from app.services.evidence.source_normalizer import (
    DEFAULT_EVIDENCE_TYPE,
    EVIDENCE_TYPE_BY_TIER,
    canonical_url_for,
    evidence_type_for_tier,
    normalize_source,
)

__all__ = [
    "ALLOWED_EXCERPT_ORIGINS",
    "DEFAULT_EVIDENCE_TYPE",
    "EVIDENCE_ID_DIGEST_LENGTH",
    "EVIDENCE_ID_PREFIX",
    "EVIDENCE_TYPE_BY_TIER",
    "MAX_EXCERPT_CHARS",
    "NO_EVIDENCE_WARNINGS",
    "NO_RESULTS_WARNING",
    "PROBATIVE_REASON_CODES",
    "PROBATIVE_RELATIONS",
    "RELATION_RANK",
    "RELEVANCE_RANK",
    "EvidenceBundleResponse",
    "EvidenceItem",
    "EvidenceRelation",
    "EvidenceRelevance",
    "EvidenceResponse",
    "EvidenceService",
    "EvidenceSource",
    "EvidenceType",
    "build_evidence_item",
    "build_evidence_service",
    "canonical_url_for",
    "dedupe_evidence",
    "distinct_sources",
    "evidence_id_for",
    "evidence_key",
    "evidence_type_for",
    "evidence_type_for_tier",
    "excerpt_for",
    "excerpt_key",
    "group_by_query",
    "is_probative",
    "normalize_source",
    "order_evidence",
    "order_key",
    "relevance_for",
    "relationship_for",
    "status_supports_evidence",
]
