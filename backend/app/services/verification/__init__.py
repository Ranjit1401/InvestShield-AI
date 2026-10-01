"""Claim verification (Phase 4).

Public surface for the verification layer. Consumers import from here, never
from a module inside it:

    from app.services.verification import VerificationService, build_verification_service

Layering, and the reason each piece is separate:

    Claim + Entity
        -> build_target()        resolves *what* to look for and *where*
        -> SearchService         Phase 3: runs the bounded query set
        -> assess_results()      reads documents; says what each can speak to
        -> decide_verification() pure decision table -> VerificationResult

`target`, `query_builder`, `comparator` and `decision_engine` are pure functions
of their inputs. Only `VerificationService` touches the network, and only through
Phase 3's `SearchService`. That separation is what makes every path testable
offline, with no credentials (D-009).

Guarantees this package upholds:

* The only statuses are `VERIFIED`, `UNVERIFIED`, `CONTRADICTED`,
  `INSUFFICIENT_EVIDENCE` and `NOT_APPLICABLE`. There is no verdict status and
  none may be added (D-006).
* `CONTRADICTED` requires a direct conflict with a claim-relevant authoritative
  record. Absence of evidence — a failed search, no credentials, zero results,
  no identity match — never produces it.
* `confidence` is confidence in the status assigned, never a probability of
  fraud, loss, or the claim's truth.
* No LLM participates. Statuses, confidence and wording are deterministic and
  reproducible (D-008, D-022).
"""

from __future__ import annotations

from app.schemas.verification import (
    AUTHORITATIVE_SOURCE_CONTRADICTS,
    AUTHORITATIVE_SOURCE_CONFIRMS,
    AUTHORITATIVE_TIERS,
    CONFLICTING_AUTHORITATIVE_SOURCES,
    IDENTITY_AMBIGUOUS,
    IDENTITY_NOT_FOUND,
    NO_CLAIM_RELEVANT_SOURCE,
    NO_CONFIRMATION_FOUND,
    NO_QUERY_BUILT,
    NOT_A_FACTUAL_CLAIM,
    SEARCH_FAILED,
    SEARCH_UNAVAILABLE,
    TIER_PRIORITY,
    VERIFICATION_REASON_CODES,
    ZERO_RESULTS,
    IdentityMatch,
    SourceTier,
    VerificationResponse,
    VerificationResult,
    VerificationStatus,
)
from app.services.verification.authority_registry import (
    AUTHORITY_BY_KEY,
    AUTHORITY_SOURCES,
    AuthoritySource,
    authorities_for_claim_type,
    authority_for_domain,
    authority_for_url,
    resolve_authority,
    tier_for_domain,
    tier_for_source_type,
)
from app.services.verification.comparator import (
    AssessedSource,
    Stance,
    assess_result,
    assess_results,
    detect_stance,
    match_identity,
    match_identity_for_target,
    result_id_for,
    source_id_for,
)
from app.services.verification.decision_engine import (
    CONFIDENCE_BY_REASON,
    EXPLANATION_TEMPLATES,
    PROHIBITED_OUTPUT_TERMS,
    contains_prohibited_term,
    decide_verification,
    render_explanation,
)
from app.services.verification.query_builder import (
    CLAIM_TOPICS,
    MAX_QUERIES_PER_CLAIM,
    build_queries,
)
from app.services.verification.target import (
    NON_VERIFIABLE_CLAIM_TYPES,
    REGISTRATION_STYLE_CLAIM_TYPES,
    VerificationTarget,
    build_target,
    identifiers_for,
    identity_entities_for,
)
from app.services.verification.verification_service import (
    VerificationService,
    build_verification_service,
)

__all__ = [
    "AUTHORITATIVE_SOURCE_CONFIRMS",
    "AUTHORITATIVE_SOURCE_CONTRADICTS",
    "AUTHORITATIVE_TIERS",
    "AUTHORITY_BY_KEY",
    "AUTHORITY_SOURCES",
    "CLAIM_TOPICS",
    "CONFIDENCE_BY_REASON",
    "CONFLICTING_AUTHORITATIVE_SOURCES",
    "EXPLANATION_TEMPLATES",
    "IDENTITY_AMBIGUOUS",
    "IDENTITY_NOT_FOUND",
    "MAX_QUERIES_PER_CLAIM",
    "NON_VERIFIABLE_CLAIM_TYPES",
    "NO_CLAIM_RELEVANT_SOURCE",
    "NO_CONFIRMATION_FOUND",
    "NO_QUERY_BUILT",
    "NOT_A_FACTUAL_CLAIM",
    "PROHIBITED_OUTPUT_TERMS",
    "REGISTRATION_STYLE_CLAIM_TYPES",
    "SEARCH_FAILED",
    "SEARCH_UNAVAILABLE",
    "TIER_PRIORITY",
    "VERIFICATION_REASON_CODES",
    "ZERO_RESULTS",
    "AssessedSource",
    "AuthoritySource",
    "IdentityMatch",
    "SourceTier",
    "Stance",
    "VerificationResponse",
    "VerificationResult",
    "VerificationService",
    "VerificationStatus",
    "VerificationTarget",
    "assess_result",
    "assess_results",
    "authorities_for_claim_type",
    "authority_for_domain",
    "authority_for_url",
    "build_queries",
    "build_target",
    "build_verification_service",
    "contains_prohibited_term",
    "decide_verification",
    "detect_stance",
    "identifiers_for",
    "identity_entities_for",
    "match_identity",
    "match_identity_for_target",
    "render_explanation",
    "resolve_authority",
    "result_id_for",
    "source_id_for",
    "tier_for_domain",
    "tier_for_source_type",
]