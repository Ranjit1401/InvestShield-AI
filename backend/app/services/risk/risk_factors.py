"""Risk factor construction (Phase 6).

A *factor* is one documented risk indicator plus the provenance that produced it.
This module turns Phase 1 `RedFlag` objects and Phase 4 `VerificationResult`
objects into `RiskFactor` objects. It makes no judgement of its own: it does not
detect anything, does not re-verify anything, and does not read any text.

Three rules shape everything here.

**One signal, one factor.** A guaranteed-return promise is noticed by Phase 1 as
a red flag, by Phase 2 as a claim, by Phase 4 as something to verify and by
Phase 5 as context. That is one risk signal reported four ways, and it must score
once. `risk_aggregation` is where that is enforced; this module only builds the
factors, each carrying the ids that let aggregation recognise the overlap.

**Red flags are primary.** Where a red flag already counts a signal, the
verification result becomes supporting context on that factor rather than a second
factor. The `verification_statuses` field exists for exactly this.

**Wording is templated.** Every `label`, `description`, `reason` and `source` a
user could read is rendered from a fixed string here, never generated. The same
reason D-022 gives for verification explanations applies to risk factors: an
explanation whose presence depends on sampling is an accusation whose presence
depends on sampling (D-022, D-025).
"""

from __future__ import annotations

import hashlib

from app.schemas.claims import ClaimType
from app.schemas.red_flags import RedFlag, RedFlagCode, Severity
from app.schemas.risk import (
    RED_FLAG_ID_PREFIX,
    RISK_FACTOR_ID_PREFIX,
    RISK_ID_DIGEST_LENGTH,
    RISK_RELEVANT_CLAIM_TYPES,
    RiskFactor,
    RiskFactorOrigin,
    RiskWeights,
    VerificationFactorType,
)
from app.schemas.verification import (
    IDENTITY_AMBIGUOUS,
    IDENTITY_NOT_FOUND,
    NO_CLAIM_RELEVANT_SOURCE,
    NO_CONFIRMATION_FOUND,
    NO_QUERY_BUILT,
    SEARCH_FAILED,
    SEARCH_UNAVAILABLE,
    ZERO_RESULTS,
    VerificationResult,
    VerificationStatus,
)

#: Reason codes meaning "the search ran and found nothing that establishes the
#: claim". These are the only `UNVERIFIED` reasons that produce an
#: `UNVERIFIED_CLAIM` factor.
#:
#: `SEARCH_UNAVAILABLE`, `SEARCH_FAILED`, `ZERO_RESULTS`, `NO_QUERY_BUILT` and
#: `NO_CLAIM_RELEVANT_SOURCE` are deliberately absent. They mean *we could not
#: look*, not *we looked and found nothing*, and conflating the two is exactly
#: how a tool that cannot reach the internet ends up accusing someone.
UNVERIFIED_REASON_CODES: frozenset[str] = frozenset(
    {
        NO_CONFIRMATION_FOUND,
        IDENTITY_NOT_FOUND,
        IDENTITY_AMBIGUOUS,
    }
)

#: Reason codes meaning the search never actually completed, so nothing can be
#: concluded from the absence of results.
#:
#: These drive the "search data was unavailable" warning and lower
#: `analysis_completeness`. They never produce risk contribution on their own: an
#: investigation that could not reach the internet has *less* evidence, not more
#: risk (D-006).
SEARCH_INCOMPLETE_REASON_CODES: frozenset[str] = frozenset(
    {
        SEARCH_UNAVAILABLE,
        SEARCH_FAILED,
        NO_QUERY_BUILT,
        NO_CLAIM_RELEVANT_SOURCE,
    }
)

#: Reason codes meaning the authoritative registers were reached and held no
#: matching entry.
#:
#: Worth separating from `SEARCH_INCOMPLETE_REASON_CODES` because the fact is
#: different in kind, and saying so is the whole point of this product: the tool
#: reached the regulator's register and the firm was not in it. That is a real
#: observation about the public record, not a gap in the analysis.
REGISTER_EMPTY_REASON_CODES: frozenset[str] = frozenset({ZERO_RESULTS})

#: Fixed wording per verification factor type. Never generated.
_VERIFICATION_LABELS: dict[VerificationFactorType, tuple[str, str]] = {
    VerificationFactorType.CONTRADICTED_CLAIM: (
        "Claim Contradicted by an Authoritative Record",
        "An authoritative record directly conflicts with a claim made in the "
        "submitted content.",
    ),
    VerificationFactorType.UNVERIFIED_CLAIM: (
        "Material Claim Not Confirmed by Any Searched Source",
        "A search for a material claim completed without finding a source that "
        "establishes it. This is a gap in the public record, not a finding that "
        "the claim is false.",
    ),
    VerificationFactorType.INSUFFICIENT_EVIDENCE: (
        "Claim Could Not Be Independently Assessed",
        "Verification could not be completed for a claim, so no conclusion was "
        "reached. This records uncertainty and is not itself a risk indicator.",
    ),
}

#: Fixed wording per verification factor type for the `reason` field.
_VERIFICATION_REASONS: dict[VerificationFactorType, str] = {
    VerificationFactorType.CONTRADICTED_CLAIM: (
        "Phase 4 found an authoritative, claim-relevant record for the claimed "
        "entity that directly conflicts with this claim."
    ),
    VerificationFactorType.UNVERIFIED_CLAIM: (
        "Phase 4 searched for this claim and no authoritative source was found "
        "that establishes it. No source disputes it either."
    ),
    VerificationFactorType.INSUFFICIENT_EVIDENCE: (
        "Phase 4 could not complete verification for this claim, so no "
        "supporting or contradicting source was available."
    ),
}


def _digest(*parts: str) -> str:
    """Return a stable short digest over `parts`.

    Args:
        parts: Strings to bind together, separated by an ASCII unit separator so
            no two different inputs can produce the same joined string.

    Returns:
        `RISK_ID_DIGEST_LENGTH` hex characters.
    """
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:RISK_ID_DIGEST_LENGTH]


def red_flag_id_for(flag: RedFlag) -> str:
    """Return a stable id for a Phase 1 red flag.

    Phase 1 deduplicates per rule and does not mint ids, so Phase 6 derives one
    from the rule code and the exact span it fired on. The convention matches
    Phase 4's `src_`/`res_` and Phase 5's `ev_`: derived, never sequential, so
    the same finding keeps the same id across runs.

    Args:
        flag: The red flag.

    Returns:
        ``rf_<12 hex chars>``.
    """
    return f"{RED_FLAG_ID_PREFIX}{_digest(flag.code.value, str(flag.evidence_span.start), str(flag.evidence_span.end), flag.matched_text)}"


def risk_factor_id_for(
    origin: RiskFactorOrigin,
    factor_type: RedFlagCode | VerificationFactorType,
    primary_ids: tuple[str, ...],
) -> str:
    """Return a stable id for one risk factor.

    Derived from the factor's origin, its type and the ids it cites, so the same
    signal always yields the same id — which is what lets a stored report and a
    re-run be compared, and what lets `absorbed_into` point at something durable.

    Args:
        origin: Which stage produced the factor.
        factor_type: The red-flag code or verification factor type.
        primary_ids: The ids that identify this particular factor.

    Returns:
        ``rsk_<12 hex chars>``.
    """
    return f"{RISK_FACTOR_ID_PREFIX}{_digest(origin.value, factor_type.value, *primary_ids)}"


def factors_from_red_flags(
    red_flags: tuple[RedFlag, ...],
    weights: RiskWeights,
) -> tuple[RiskFactor, ...]:
    """Build one primary factor per red flag.

    Red-flag factors are always **primary**: they are emitted with their full
    contribution, and any verification result covering the same underlying signal
    is attached to them later rather than scored again.

    Args:
        red_flags: Phase 1 red flags, already collapsed to one per code by
            `risk_aggregation`.
        weights: The weight snapshot in force.

    Returns:
        Factors in input order, each carrying its red-flag id.
    """
    factors: list[RiskFactor] = []
    for flag in red_flags:
        flag_id = red_flag_id_for(flag)
        weight = weights.weight_for(flag.code)
        factors.append(
            RiskFactor(
                id=risk_factor_id_for(
                    RiskFactorOrigin.RED_FLAG, flag.code, (flag_id,)
                ),
                origin=RiskFactorOrigin.RED_FLAG,
                factor_type=flag.code,
                label=flag.name,
                description=flag.description,
                reason=flag.rule_reason,
                source=f"red flag {flag_id}",
                severity=flag.severity,
                weight=weight,
                contribution=weight,
                red_flag_ids=(flag_id,),
                is_uncertainty=False,
            )
        )
    return tuple(factors)


def factor_type_for(result: VerificationResult) -> VerificationFactorType | None:
    """Return the factor type a verification result produces, if any.

    The mapping is exhaustive and deliberate:

    - `VERIFIED` → **no factor.** An authoritative confirmation is not a risk
      indicator, and no member of `VerificationFactorType` encodes a risk
      *reduction*, because this phase accumulates documented indicators and does
      not net them against each other (D-020).
    - `CONTRADICTED` → `CONTRADICTED_CLAIM`.
    - `UNVERIFIED` → `UNVERIFIED_CLAIM`, but only when the reason code says a
      search actually completed.
    - `INSUFFICIENT_EVIDENCE` → `INSUFFICIENT_EVIDENCE`, the uncertainty factor.
      The status alone decides this, whatever the reason: every route into that
      status means the same thing, that no conclusion was reached.
    - `NOT_APPLICABLE` → **no factor**, always. No record was ever looked up, so
      there is nothing to assess and nothing to invent (D-006).

    Args:
        result: The claim's Phase 4 result.

    Returns:
        The factor type, or `None` when this result produces no factor.
    """
    if result.status is VerificationStatus.VERIFIED:
        return None
    if result.status is VerificationStatus.NOT_APPLICABLE:
        return None
    if result.status is VerificationStatus.CONTRADICTED:
        return VerificationFactorType.CONTRADICTED_CLAIM
    if result.status is VerificationStatus.UNVERIFIED:
        if result.reason_code in UNVERIFIED_REASON_CODES:
            return VerificationFactorType.UNVERIFIED_CLAIM
        return None
    if result.status is VerificationStatus.INSUFFICIENT_EVIDENCE:
        return VerificationFactorType.INSUFFICIENT_EVIDENCE
    return None


def severity_for_factor(factor_type: VerificationFactorType) -> Severity:
    """Return the intrinsic severity of a verification factor type.

    Args:
        factor_type: The verification factor type.

    Returns:
        Its `Severity`. `CONTRADICTED_CLAIM` is `CRITICAL` because it is the
        only factor backed by a direct authoritative conflict; the two "could
        not establish this" factors are `MEDIUM` and never higher, because
        uncertainty is not wrongdoing.
    """
    if factor_type is VerificationFactorType.CONTRADICTED_CLAIM:
        return Severity.CRITICAL
    if factor_type is VerificationFactorType.UNVERIFIED_CLAIM:
        return Severity.MEDIUM
    return Severity.LOW


def factors_from_verification(
    results: tuple[VerificationResult, ...],
    claims_by_id: dict[str, object],
    weights: RiskWeights,
) -> tuple[RiskFactor, ...]:
    """Build one factor per verification result that bears on the investment.

    Args:
        results: Phase 4 results, already collapsed to one per claim by
            `risk_aggregation`.
        claims_by_id: Phase 2 claims keyed by id, used to check that the claim
            type is risk-relevant and to carry the claim text into the reason.
        weights: The weight snapshot in force.

    Returns:
        Factors in input order. A result for a claim that is absent from
        `claims_by_id`, or whose `ClaimType` is not risk-relevant, produces no
        factor — an unverifiable statement about office hours is not an
        investment risk indicator.
    """
    factors: list[RiskFactor] = []
    for result in results:
        claim = claims_by_id.get(result.claim_id)
        if claim is None:
            continue
        if claim.claim_type not in RISK_RELEVANT_CLAIM_TYPES:  # type: ignore[attr-defined]
            continue

        factor_type = factor_type_for(result)
        if factor_type is None:
            continue

        weight = weights.weight_for(factor_type)
        label, description = _VERIFICATION_LABELS[factor_type]
        factors.append(
            RiskFactor(
                id=risk_factor_id_for(
                    RiskFactorOrigin.VERIFICATION, factor_type, (result.claim_id,)
                ),
                origin=RiskFactorOrigin.VERIFICATION,
                factor_type=factor_type,
                label=label,
                description=description,
                reason=_reason_for(result, factor_type),
                source=f"claim {result.claim_id} ({result.status.value})",
                severity=severity_for_factor(factor_type),
                weight=weight,
                contribution=weight,
                claim_ids=(result.claim_id,),
                verification_statuses=(result.status,),
                is_uncertainty=factor_type
                in (
                    VerificationFactorType.UNVERIFIED_CLAIM,
                    VerificationFactorType.INSUFFICIENT_EVIDENCE,
                ),
            )
        )
    return tuple(factors)


def _reason_for(
    result: VerificationResult, factor_type: VerificationFactorType
) -> str:
    """Render the fixed explanation for a verification factor.

    Args:
        result: The claim's Phase 4 result.
        factor_type: The factor type it produced.

    Returns:
        A fixed sentence naming the claim and the status. Phase 4's own `reason`
        is not reused verbatim here: it is written for a verification report, and
        this sentence is written for a risk breakdown.
    """
    base = _VERIFICATION_REASONS[factor_type]
    return f'Claim "{result.claim_id}" — {result.status.value}. {base}'


def claim_is_risk_relevant(claim_type: ClaimType) -> bool:
    """Whether a claim type bears on investment safety.

    Args:
        claim_type: The claim's family.

    Returns:
        True when the claim's verification status may produce a risk factor.
    """
    return claim_type in RISK_RELEVANT_CLAIM_TYPES


__all__ = [
    "REGISTER_EMPTY_REASON_CODES",
    "SEARCH_INCOMPLETE_REASON_CODES",
    "UNVERIFIED_REASON_CODES",
    "claim_is_risk_relevant",
    "factor_type_for",
    "factors_from_red_flags",
    "factors_from_verification",
    "red_flag_id_for",
    "risk_factor_id_for",
    "severity_for_factor",
]