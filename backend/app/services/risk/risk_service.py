"""Risk service (Phase 6).

The application-facing entry point for the risk stage:

```
RedFlag (Phase 1) ─┐
Claim (Phase 2) ───┼─► RiskService.assess()
Verification ───────┤
Evidence (Phase 5) ─┘
                        │
                        ▼
                  RiskAssessment
                  (score + level + factors + coverage + warnings)
```

Everything the service does is deterministic arithmetic over objects earlier
phases produced. It makes no network call, consults no provider, calls no model,
searches nothing, and holds no opinion about any person or organisation.

The four properties it guarantees, each tested:

- **Traceable.** Every factor carries the red-flag, claim, evidence and source ids
  behind it. "Why did the risk score increase?" is answerable from the assessment
  alone.
- **Non-double-counting.** One underlying signal scores once. Where Phase 1, 2, 4
  and 5 all noticed the same behaviour, the red-flag factor is primary and the
  rest is attached context — see `risk_aggregation`.
- **Conservative.** Absence of evidence does not raise the score.
  `INSUFFICIENT_EVIDENCE` carries a default weight of `0`; `VERIFIED` and
  `NOT_APPLICABLE` produce no factor at all; no factor can contribute a negative
  amount, so nothing ever reduces the score either (D-006, D-020).
- **Non-accusatory.** No output field is a probability. `risk_score` is a bounded
  heuristic indicator sum, and every assessment says so in its own warnings
  (D-025).
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.schemas.claims import Claim
from app.schemas.evidence import EvidenceResponse
from app.schemas.red_flags import RedFlag
from app.schemas.risk import (
    RISK_RELEVANT_CLAIM_TYPES,
    SCORE_NOT_A_PROBABILITY as _SCORE_NOT_A_PROBABILITY,
    RiskAssessment,
    RiskFactor,
)
from app.schemas.verification import VerificationResult, VerificationStatus
from app.services.risk.risk_aggregation import (
    absorb_duplicate_signals,
    attach_absorption_context,
    attach_claims,
    attach_evidence,
    collapse_duplicate_red_flags,
    collapse_duplicate_results,
)
from app.services.risk.risk_factors import (
    REGISTER_EMPTY_REASON_CODES,
    SEARCH_INCOMPLETE_REASON_CODES,
    factors_from_red_flags,
    factors_from_verification,
)
from app.services.risk.risk_scoring import (
    level_for_score,
    score_from_contributions,
    thresholds_from_settings,
    weights_from_settings,
)

logger = get_logger(__name__)

#: Emitted on every assessment, so no report can present the score without its
#: meaning attached to it.
#:
#: Re-exported from `app.schemas.risk`, and enforced there: `RiskAssessment`
#: appends it during validation, so it cannot be omitted by any caller. The
#: service lists it explicitly only to keep it last, where a reader meets it.
SCORE_NOT_A_PROBABILITY = _SCORE_NOT_A_PROBABILITY

#: Emitted when any claim's verification could not be completed.
SEARCH_DATA_UNAVAILABLE = (
    "External search data was unavailable for at least one claim, so no "
    "independent confirmation or contradiction could be obtained for it."
)

#: Emitted when an authoritative register was reached and held no matching entry.
#:
#: Worth stating separately from `SEARCH_DATA_UNAVAILABLE` because the fact is
#: different in kind, and saying so is the point of the product: the register was
#: reached and the party was not in it. That is an observation about the public
#: record, not a gap in this analysis.
REGISTER_NO_MATCH = (
    "For at least one claim, the authoritative registers that were searched "
    "contained no matching entry."
)

#: Emitted when a material claim was searched for and not confirmed.
CLAIMS_NOT_CONFIRMED = (
    "At least one claim material to the investment could not be confirmed by "
    "any source that was searched."
)

#: Emitted when at least one claim is contradicted by an authoritative record.
CLAIMS_CONTRADICTED = (
    "At least one claim is contradicted by an authoritative record that was "
    "retrieved and is cited in the factors below."
)

#: Emitted when the same signal was seen by more than one stage.
SIGNALS_DEDUPLICATED = (
    "Some signals were detected by more than one stage of the analysis. Each is "
    "counted once; the factors that describe an already-counted signal are listed "
    "with a zero contribution."
)


class RiskService:
    """Assembles an explainable risk assessment from earlier phases.

    The service holds no state and performs no I/O. Constructing one is free and
    the same instance can assess any number of investigations.

    Example:
        >>> service = RiskService()
        >>> assessment = service.assess(red_flags, claims, results, evidence)  # doctest: +SKIP
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """Bind the service to a configuration.

        Args:
            settings: Configuration to read weights and bands from. Defaults to
                the cached application settings.
        """
        self._settings = settings or get_settings()

    def assess(
        self,
        red_flags: tuple[RedFlag, ...] = (),
        claims: tuple[Claim, ...] = (),
        verification_results: tuple[VerificationResult, ...] = (),
        evidence: tuple[EvidenceResponse, ...] = (),
    ) -> RiskAssessment:
        """Assess the documented risk in one investigation.

        Args:
            red_flags: Phase 1 red flags from the submitted content.
            claims: Phase 2 claims from the submitted content.
            verification_results: Phase 4 results, one per claim where one exists.
            evidence: Phase 5 responses, one per claim where one was assembled.

        Returns:
            A `RiskAssessment`. With no inputs at all it is a `LOW` assessment
            scoring `0` with no factors — the correct answer for "nothing was
            found", and explicitly not a finding that the content is safe
            (D-006).
        """
        weights = weights_from_settings(self._settings)
        thresholds = thresholds_from_settings(self._settings)

        unique_flags = collapse_duplicate_red_flags(tuple(red_flags))
        unique_results = collapse_duplicate_results(tuple(verification_results))
        claims_by_id = {claim.id: claim for claim in claims}

        factors = list(factors_from_red_flags(unique_flags, weights))
        factors.extend(factors_from_verification(unique_results, claims_by_id, weights))

        linked = attach_claims(tuple(factors), tuple(claims), unique_flags)
        with_evidence = attach_evidence(linked, tuple(evidence))
        absorbed = absorb_duplicate_signals(with_evidence)
        final_factors = attach_absorption_context(absorbed)

        contributions = tuple(factor.contribution for factor in final_factors)
        raw_score = score_from_contributions(contributions)
        risk_score, risk_level = level_for_score(raw_score, thresholds)

        relevant = _relevant_claims(claims)
        assessed = _assessed_claims(relevant, unique_results)
        coverage = _evidence_coverage(assessed, tuple(evidence))
        completeness = _analysis_completeness(
            len(relevant), assessed, len(evidence), bool(claims)
        )
        warnings = self._warnings(unique_results, final_factors)

        logger.debug(
            "Risk assessment complete",
            extra={
                "risk_score": risk_score,
                "risk_level": risk_level.value,
                "factors": len(final_factors),
                "relevant_claims": len(relevant),
            },
        )

        return RiskAssessment(
            risk_score=risk_score,
            raw_score=raw_score,
            risk_level=risk_level,
            factors=tuple(final_factors),
            relevant_claims=len(relevant),
            assessed_claims=assessed,
            evidence_coverage=coverage,
            analysis_completeness=completeness,
            weights=weights,
            thresholds=thresholds,
            warnings=warnings,
        )

    def assess_batch(
        self,
        claims: tuple[Claim, ...],
        verification_results: tuple[VerificationResult, ...],
        evidence: tuple[EvidenceResponse, ...] = (),
        red_flags: tuple[RedFlag, ...] = (),
    ) -> RiskAssessment:
        """Assess an investigation whose inputs are already grouped by claim.

        Identical to :meth:`assess`; the separate name exists because Phase 7 will
        call this shape, where one investigation holds many claims rather than
        one.

        Args:
            claims: Phase 2 claims.
            verification_results: Phase 4 results for those claims.
            evidence: Phase 5 responses for those claims.
            red_flags: Phase 1 red flags for the submitted content.

        Returns:
            The `RiskAssessment`.
        """
        return self.assess(red_flags, claims, verification_results, evidence)

    # -- internals ------------------------------------------------------

    @staticmethod
    def _warnings(
        results: tuple[VerificationResult, ...],
        factors: tuple[RiskFactor, ...],
    ) -> tuple[str, ...]:
        """Assemble the assessment's factual limitations.

        Every warning states what did not happen or what was found. None states
        what the user should do, none uses verdict vocabulary, and all are fixed
        strings (D-022, D-025).

        Args:
            results: The de-duplicated Phase 4 results.
            factors: The final factors.

        Returns:
            De-duplicated warnings, with `SCORE_NOT_A_PROBABILITY` always last so
            it reads as the standing caveat.
        """
        warnings: list[str] = []
        if any(
            result.reason_code in SEARCH_INCOMPLETE_REASON_CODES for result in results
        ):
            warnings.append(SEARCH_DATA_UNAVAILABLE)
        if any(result.reason_code in REGISTER_EMPTY_REASON_CODES for result in results):
            warnings.append(REGISTER_NO_MATCH)
        # These two key off whether a status is *present*, not off which factor
        # counted it. A contradicted claim that was absorbed into a red flag was
        # still contradicted, and the reader is entitled to be told. Requiring
        # `is_scoring` here would silence the most material warning in the
        # assessment precisely when de-duplication worked correctly.
        statuses = {status for factor in factors for status in factor.verification_statuses}
        if VerificationStatus.UNVERIFIED in statuses:
            warnings.append(CLAIMS_NOT_CONFIRMED)
        if VerificationStatus.CONTRADICTED in statuses:
            warnings.append(CLAIMS_CONTRADICTED)
        if any(factor.absorbed_into for factor in factors):
            warnings.append(SIGNALS_DEDUPLICATED)

        warnings.append(SCORE_NOT_A_PROBABILITY)
        return tuple(dict.fromkeys(warnings))


def _relevant_claims(claims: tuple[Claim, ...]) -> tuple[Claim, ...]:
    """Return the claims whose verification bears on the investment.

    Args:
        claims: Every extracted claim.

    Returns:
        Those whose `ClaimType` is in `RISK_RELEVANT_CLAIM_TYPES`. An
        unverifiable statement about office hours is not an investment risk
        indicator, and it must not become one by accident.
    """
    return tuple(
        claim for claim in claims if claim.claim_type in RISK_RELEVANT_CLAIM_TYPES
    )


def _assessed_claims(
    relevant: tuple[Claim, ...],
    results: tuple[VerificationResult, ...],
) -> int:
    """Count relevant claims that carry a verification result.

    Args:
        relevant: Risk-relevant claims.
        results: De-duplicated Phase 4 results.

    Returns:
        How many relevant claims were actually assessed. This is the denominator
        of `evidence_coverage`, so a claim that was never verified lowers the
        coverage denominator's numerator without inflating the score.
    """
    assessed_ids = {result.claim_id for result in results}
    return sum(1 for claim in relevant if claim.id in assessed_ids)


def _evidence_coverage(
    assessed: int,
    evidence: tuple[EvidenceResponse, ...],
) -> float:
    """Share of assessed claims for which retrievable supporting text exists.

    A claim counts as covered when its `EvidenceResponse` contains at least one
    item that Phase 5 marked `is_proof` — an authoritative, identity-matched,
    claim-relevant record with quoted text behind it.

    This is a **transparency measure**, not a confidence and not an accuracy
    score. It says how much of the investigation had real documents behind it. A
    claim that was never verified is outside the denominator by design, so an
    investigation with no external evidence reports `0.0` rather than dividing by
    zero or quietly reporting `1.0`.

    Args:
        assessed: Number of relevant claims with a verification result.
        evidence: Phase 5 responses.

    Returns:
        A value in `[0.0, 1.0]`, rounded to four places so the same inputs always
        serialise identically.
    """
    if assessed <= 0:
        return 0.0
    covered = sum(
        1 for response in evidence if any(item.is_proof for item in response.evidence)
    )
    return round(min(covered, assessed) / assessed, 4)


def _analysis_completeness(
    relevant_count: int,
    assessed: int,
    evidence_responses: int,
    had_claims: bool,
) -> float:
    """Share of the per-claim analyses that were actually performed.

    Three analyses are expected for every risk-relevant claim: **extract** (Phase 2
    produced the `Claim`, which is true by construction here), **verify** (Phase 4
    produced a result), and **assemble evidence** (Phase 5 produced a response).
    The measure is the fraction of those three that happened.

    It is deliberately a statement about *the pipeline's own work*, never about
    the content. An investigation whose search provider was unreachable scores low
    on completeness and **no higher** on risk: uncertainty is expressed here, not
    in the score (D-006).

    Args:
        relevant_count: Number of risk-relevant claims.
        assessed: Number of relevant claims with a verification result.
        evidence_responses: Number of Phase 5 responses supplied.
        had_claims: Whether any claim at all was extracted.

    Returns:
        A value in `[0.0, 1.0]`, rounded to four places.
    """
    if relevant_count <= 0:
        # Nothing was relevant, so there were no per-claim analyses to miss. The
        # only question left is whether extraction ran at all.
        return 1.0 if had_claims else 0.0
    performed = relevant_count + assessed + min(evidence_responses, relevant_count)
    return round(min(performed, 3 * relevant_count) / (3 * relevant_count), 4)


def build_risk_service(settings: Settings | None = None) -> RiskService:
    """Construct a `RiskService`.

    Args:
        settings: Configuration to bind. Defaults to the cached application
            settings.

    Returns:
        A ready service.
    """
    return RiskService(settings)


__all__ = [
    "CLAIMS_CONTRADICTED",
    "CLAIMS_NOT_CONFIRMED",
    "REGISTER_NO_MATCH",
    "SCORE_NOT_A_PROBABILITY",
    "SEARCH_DATA_UNAVAILABLE",
    "SIGNALS_DEDUPLICATED",
    "RiskService",
    "build_risk_service",
]