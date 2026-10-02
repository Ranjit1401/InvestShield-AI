"""Risk level â†’ factor â†’ score wiring (Phase 6).

Public surface for the risk layer. Consumers import from here, never from a module
inside it:

    from app.services.risk import RiskService, build_risk_service

The layering, and the reason each piece is separate:

    RedFlag + VerificationResult â†’ factors_from_*()     â†’ one factor per signal
                    + RiskWeights â†’ absorb / attach       â†’ no double counting
                                â†’ score_from_contributions()
                                          â†”
                                    RiskAssessment

Every stage is a pure function over already-produced objects, and every stage is
separately testable. `RiskService` is only the composition of them, so a scoring
question can always be answered by reading the stage that decides it rather than
by debugging a pipeline.

The design rules this layer enforces, each with tests:

- **No I/O, ever.** No network call, no provider, no model, no clock-dependent
  branch. Same inputs, same assessment, every run.
- **No double counting.** One underlying signal scores once, no matter how many
  stages noticed it. A de-duplicated factor is *kept* at contribution ``0`` with
  an ``absorbed_into`` pointer, so the breakdown stays complete (D-025).
- **Absence of evidence is not risk.** `INSUFFICIENT_EVIDENCE` weights ``0`` by
  default, `VERIFIED` and `NOT_APPLICABLE` produce no factor, and nothing can
  contribute a negative amount, so no finding can lower the score either
  (D-006, D-020).
- **No verdicts, no probabilities, no advice.** No field is a probability of
  fraud or loss; the bands are product heuristics, snapshotted onto the
  assessment alongside the weights that produced the score (D-007, D-022).

One thing the service will not do, and should not be asked to: judge whether a
person or company is legitimate, or whether an investment is sound. It counts
documented indicators and says what they were. That boundary is the product.
"""

from __future__ import annotations

from app.schemas.risk import (
    RED_FLAG_ID_PREFIX,
    RISK_FACTOR_ID_PREFIX,
    RISK_ID_DIGEST_LENGTH,
    RISK_LEVEL_ORDER,
    RISK_RELEVANT_CLAIM_TYPES,
    RiskAssessment,
    RiskFactor,
    RiskFactorOrigin,
    RiskLevel,
    RiskThresholds,
    RiskWeights,
    VerificationFactorType,
)
from app.services.risk.risk_aggregation import (
    CLAIM_TYPE_RED_FLAG_CODES,
    absorb_duplicate_signals,
    attach_absorption_context,
    attach_claims,
    attach_evidence,
    claim_links_to,
    collapse_duplicate_red_flags,
    collapse_duplicate_results,
    spans_overlap,
)
from app.services.risk.risk_factors import (
    REGISTER_EMPTY_REASON_CODES,
    SEARCH_INCOMPLETE_REASON_CODES,
    UNVERIFIED_REASON_CODES,
    claim_is_risk_relevant,
    factor_type_for,
    factors_from_red_flags,
    factors_from_verification,
    red_flag_id_for,
    risk_factor_id_for,
    severity_for_factor,
)
from app.services.risk.risk_scoring import (
    apply_ceiling,
    band_for,
    level_for_score,
    score_from_contributions,
    thresholds_from_settings,
    weights_from_settings,
)
from app.services.risk.risk_service import (
    CLAIMS_CONTRADICTED,
    CLAIMS_NOT_CONFIRMED,
    REGISTER_NO_MATCH,
    SCORE_NOT_A_PROBABILITY,
    SEARCH_DATA_UNAVAILABLE,
    SIGNALS_DEDUPLICATED,
    RiskService,
    build_risk_service,
)

__all__ = [
    "CLAIMS_CONTRADICTED",
    "CLAIMS_NOT_CONFIRMED",
    "CLAIM_TYPE_RED_FLAG_CODES",
    "RED_FLAG_ID_PREFIX",
    "REGISTER_EMPTY_REASON_CODES",
    "REGISTER_NO_MATCH",
    "RISK_FACTOR_ID_PREFIX",
    "RISK_ID_DIGEST_LENGTH",
    "RISK_LEVEL_ORDER",
    "RISK_RELEVANT_CLAIM_TYPES",
    "SCORE_NOT_A_PROBABILITY",
    "SEARCH_DATA_UNAVAILABLE",
    "SEARCH_INCOMPLETE_REASON_CODES",
    "SIGNALS_DEDUPLICATED",
    "UNVERIFIED_REASON_CODES",
    "RiskAssessment",
    "RiskFactor",
    "RiskFactorOrigin",
    "RiskLevel",
    "RiskService",
    "RiskThresholds",
    "RiskWeights",
    "VerificationFactorType",
    "absorb_duplicate_signals",
    "apply_ceiling",
    "attach_absorption_context",
    "attach_claims",
    "attach_evidence",
    "band_for",
    "build_risk_service",
    "claim_is_risk_relevant",
    "claim_links_to",
    "collapse_duplicate_red_flags",
    "collapse_duplicate_results",
    "factor_type_for",
    "factors_from_red_flags",
    "factors_from_verification",
    "level_for_score",
    "red_flag_id_for",
    "risk_factor_id_for",
    "score_from_contributions",
    "severity_for_factor",
    "spans_overlap",
    "thresholds_from_settings",
    "weights_from_settings",
]
