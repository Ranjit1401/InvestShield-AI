"""Signal de-duplication and cross-stage linking (Phase 6).

This module is where "do not double count" is actually implemented. Everything
else in Phase 6 is straightforward once the pipeline has been reduced to one
factor per underlying signal, and this file is what does that reduction.

## The problem

One behaviour — a guaranteed 30% monthly return from a self-described SEBI
registered adviser — shows up four times in the pipeline:

```
Phase 1   FAKE_REGULATORY_CLAIM, GUARANTEED_RETURN, UNREALISTIC_RETURN  (red flags)
Phase 2   a GUARANTEE_CLAIM and a REGULATORY_STATUS claim                 (claims)
Phase 4   UNVERIFIED for the claim                                        (verification)
Phase 5   an authoritative page and a general-web page                    (evidence)
```

Adding all of that up would tell the user their content committed fraud four
times over. It is one signal.

## The rules

1. **One red flag per rule.** Phase 1 already collapses a rule's occurrences into
   a single `RedFlag`; if a caller supplies the same code twice, it is collapsed
   again here, keeping the first and recording the merged occurrence count.

2. **Claims link to red-flag factors** when their evidence spans overlap in the
   submitted text, or when the claim's `ClaimType` maps to the rule via
   `CLAIM_TYPE_RED_FLAG_CODES`. Span overlap is the precise signal; the type map
   is the broader one, and both are deterministic.

3. **A verification factor describing an already-counted signal contributes 0**
   and records `absorbed_into`. It stays in the bundle, so the breakdown can show
   "this claim was contradicted, and it is already counted under
   `GUARANTEED_RETURN`" — rather than silently vanishing, and rather than being
   added twice. The primary red-flag factor gains the claim's ids and statuses
   as supporting context.

4. **Evidence never contributes.** It attaches `ev_`/`src_` ids to whichever
   factors bear on its claim, and that is all. The same document reachable from
   ten queries cannot move the score.

No weight is ever multiplied, and no factor's contribution is ever *raised* by
another factor's presence. A factor contributes its configured weight or nothing.
That is what makes `RiskFactor`'s `contribution <= weight` invariant meaningful.
"""

from __future__ import annotations

from app.schemas.claims import ClaimType
from app.schemas.common import EvidenceSpan
from app.schemas.evidence import EvidenceResponse
from app.schemas.red_flags import RedFlag, RedFlagCode
from app.schemas.risk import RiskFactor, RiskFactorOrigin
from app.schemas.verification import VerificationResult
from app.services.risk.risk_factors import red_flag_id_for

#: Broader link between a claim family and the red-flag rules that cover it.
#:
#: Used when a claim's text and a red flag's span do not overlap, which is common:
#: Phase 1 fires on "SEBI approved", Phase 2 extracts "Our firm holds SEBI
#: registration", and those are not the same characters.
#:
#: Deliberately narrow. Every entry names a claim family that is *about the same
#: underlying risk signal* as the rule, not merely adjacent to it. A
#: `PRODUCT_CLAIM` maps to nothing: an unverifiable product description is not an
#: investment risk indicator.
CLAIM_TYPE_RED_FLAG_CODES: dict[ClaimType, frozenset[RedFlagCode]] = {
    ClaimType.REGULATORY_STATUS: frozenset(
        {RedFlagCode.FAKE_REGULATORY_CLAIM, RedFlagCode.UNVERIFIED_ADVISER}
    ),
    ClaimType.CREDENTIAL_CLAIM: frozenset(
        {RedFlagCode.UNVERIFIED_ADVISER, RedFlagCode.FAKE_REGULATORY_CLAIM}
    ),
    ClaimType.GUARANTEE_CLAIM: frozenset(
        {RedFlagCode.GUARANTEED_RETURN, RedFlagCode.UNREALISTIC_RETURN}
    ),
    ClaimType.RETURN_PROMISE: frozenset(
        {RedFlagCode.GUARANTEED_RETURN, RedFlagCode.UNREALISTIC_RETURN}
    ),
    ClaimType.PROFIT_PROMISE: frozenset(
        {RedFlagCode.GUARANTEED_RETURN, RedFlagCode.UNREALISTIC_RETURN}
    ),
    ClaimType.PERFORMANCE_CLAIM: frozenset({RedFlagCode.UNREALISTIC_RETURN}),
    ClaimType.OWNERSHIP_CLAIM: frozenset({RedFlagCode.FAKE_REGULATORY_CLAIM}),
    ClaimType.AFFILIATION_CLAIM: frozenset({RedFlagCode.UNVERIFIED_ADVISER}),
    ClaimType.PAYMENT_INSTRUCTION: frozenset(
        {
            RedFlagCode.THIRD_PARTY_PAYMENT,
            RedFlagCode.ACCOUNT_ACTIVATION_FEE,
            RedFlagCode.BORROW_TO_INVEST,
        }
    ),
    ClaimType.WITHDRAWAL_CLAIM: frozenset({RedFlagCode.WITHDRAWAL_FEE}),
}


def spans_overlap(first: EvidenceSpan, second: EvidenceSpan) -> bool:
    """Whether two spans cover any of the same characters.

    Half-open intervals, matching `EvidenceSpan` semantics, so two spans that only
    touch at a boundary do not count as overlapping.

    Args:
        first: One span in the submitted text.
        second: Another span in the same text.

    Returns:
        True when the two share at least one character offset.
    """
    return first.start < second.end and second.start < first.end


def claim_links_to(claim: object, flag: RedFlag) -> bool:
    """Whether a claim and a red flag describe the same underlying signal.

    Two deterministic routes, either sufficient:

    - **Span overlap.** The claim text and the flagged text share characters in
      the submitted content. The precise signal.
    - **Claim-type mapping.** The claim's `ClaimType` maps to the rule via
      `CLAIM_TYPE_RED_FLAG_CODES`. The broader signal, needed because Phase 1 and
      Phase 2 segment text differently.

    Args:
        claim: A Phase 2 `Claim`.
        flag: A Phase 1 `RedFlag`, both located in the same submitted text.

    Returns:
        True when the two are the same underlying risk signal.
    """
    if spans_overlap(claim.evidence_span, flag.evidence_span):  # type: ignore[attr-defined]
        return True
    codes = CLAIM_TYPE_RED_FLAG_CODES.get(claim.claim_type)  # type: ignore[attr-defined]
    return bool(codes) and flag.code in codes  # type: ignore[attr-defined]


def collapse_duplicate_red_flags(
    red_flags: tuple[RedFlag, ...],
) -> tuple[RedFlag, ...]:
    """Collapse red flags that are the same rule firing twice.

    Phase 1's engine already returns one `RedFlag` per code. A caller that
    re-runs detection and concatenates, or assembles flags from two sources,
    can still hand over the same code twice — and two identical rules are one
    signal, not two.

    Args:
        red_flags: Phase 1 red flags in any order.

    Returns:
        One flag per code, in first-seen order. Where a code appeared more than
        once, the earliest-seen flag is kept, because it is the one whose span a
        reader can be shown first.
    """
    seen: set[RedFlagCode] = set()
    unique: list[RedFlag] = []
    for flag in red_flags:
        if flag.code in seen:
            continue
        seen.add(flag.code)
        unique.append(flag)
    return tuple(unique)


def collapse_duplicate_results(
    results: tuple[VerificationResult, ...],
) -> tuple[VerificationResult, ...]:
    """Collapse verification results for the same claim.

    Two results for one claim id are one assessment. The first seen wins, for the
    same reason as red flags: determinism beats recency, and an investigation
    pipeline should hand this phase one result per claim.

    Args:
        results: Phase 4 results in any order.

    Returns:
        One result per `claim_id`, in first-seen order.
    """
    seen: set[str] = set()
    unique: list[VerificationResult] = []
    for result in results:
        if result.claim_id in seen:
            continue
        seen.add(result.claim_id)
        unique.append(result)
    return tuple(unique)


def attach_claims(
    factors: tuple[RiskFactor, ...],
    claims: tuple[object, ...],
    red_flags: tuple[RedFlag, ...],
) -> tuple[RiskFactor, ...]:
    """Attach claim ids to the red-flag factors they share a signal with.

    Args:
        factors: Red-flag factors, as built by `factors_from_red_flags`.
        claims: Phase 2 claims from the same submitted text.
        red_flags: The flags the factors were built from, used to recover each
            factor's span without threading flags through the factor model.

    Returns:
        The factors with `claim_ids` populated, in input order. Verification
        factors already carry their own claim and pass through unchanged.
    """
    flags_by_id = {red_flag_id_for(flag): flag for flag in red_flags}

    updated: list[RiskFactor] = []
    for factor in factors:
        if factor.origin is not RiskFactorOrigin.RED_FLAG or not factor.red_flag_ids:
            updated.append(factor)
            continue
        flag = flags_by_id.get(factor.red_flag_ids[0])
        if flag is None:
            # A red-flag factor whose flag was not supplied cannot be linked to
            # anything. Leaving its claims empty is the honest outcome: an
            # unlinked factor loses traceability rather than gaining a guess.
            updated.append(factor)
            continue
        linked = tuple(
            claim.id  # type: ignore[attr-defined]
            for claim in claims
            if claim_links_to(claim, flag)
        )
        updated.append(
            factor
            if linked == factor.claim_ids
            else factor.model_copy(update={"claim_ids": linked})
        )
    return tuple(updated)


def attach_evidence(
    factors: tuple[RiskFactor, ...],
    evidence: tuple[EvidenceResponse, ...],
) -> tuple[RiskFactor, ...]:
    """Attach Phase 5 evidence ids to the factors bearing on each claim.

    Evidence never contributes to the score. This function only records which
    retrieved documents back each factor, so a reader can go from a factor to the
    quote that produced it. The same document reachable from many queries is
    therefore attached many times and counts once — it contributes nothing at all.

    Args:
        factors: The current factors.
        evidence: Phase 5 responses, one per claim.

    Returns:
        The factors with `evidence_ids` and `source_ids` populated, in input
        order. A claim with no evidence response contributes no ids.
    """
    evidence_by_claim = {response.claim_id: response for response in evidence}

    updated: list[RiskFactor] = []
    for factor in factors:
        if not factor.claim_ids:
            updated.append(factor)
            continue

        evidence_ids: list[str] = []
        source_ids: list[str] = []
        for claim_id in factor.claim_ids:
            response = evidence_by_claim.get(claim_id)
            if response is None:
                continue
            for item in response.evidence:
                if item.id not in evidence_ids:
                    evidence_ids.append(item.id)
                if item.source.source_id not in source_ids:
                    source_ids.append(item.source.source_id)

        evidence_tuple = tuple(evidence_ids)
        source_tuple = tuple(source_ids)
        if evidence_tuple == factor.evidence_ids and source_tuple == factor.source_ids:
            updated.append(factor)
        else:
            updated.append(
                factor.model_copy(
                    update={"evidence_ids": evidence_tuple, "source_ids": source_tuple}
                )
            )
    return tuple(updated)


def absorb_duplicate_signals(
    factors: tuple[RiskFactor, ...],
) -> tuple[RiskFactor, ...]:
    """Zero out verification factors that repeat an already-counted signal.

    This is the core de-duplication rule. A verification factor whose claim is
    already linked to a **scoring red-flag factor** is describing the same
    underlying signal, so it contributes nothing and records which factor counted
    it. The primary factor gains the claim's verification status so the
    supporting context is not lost.

    Two cases are deliberately *not* absorbed:

    - **Factors that already weigh nothing** — in practice every
      `INSUFFICIENT_EVIDENCE` factor. A gap in what could be established is not a
      duplicate of a pattern that was matched, so there is nothing to
      de-duplicate, and the `absorbed_into` pointer would claim the primary had
      accounted for a verification it never performed. They are kept visible with
      no pointer at all.
    - **Factors whose primary scored nothing.** If the red flag's weight is 0,
      the claim's own factor is the only account of that signal, and absorbing it
      would erase the finding entirely.

    Absorbed factors are **kept** in the bundle at `contribution = 0`. Dropping
    them would hide that a claim was contradicted; scoring them would double
    count. Keeping them with an explicit `absorbed_into` shows both.

    Args:
        factors: Factors with claims already attached.

    Returns:
        The factors with absorption applied, in input order.
    """
    primary_by_claim: dict[str, RiskFactor] = {}
    for factor in factors:
        if factor.origin is not RiskFactorOrigin.RED_FLAG or not factor.is_scoring:
            continue
        for claim_id in factor.claim_ids:
            primary_by_claim.setdefault(claim_id, factor)

    updated: list[RiskFactor] = []
    for factor in factors:
        if factor.origin is not RiskFactorOrigin.VERIFICATION:
            updated.append(factor)
            continue
        if factor.contribution == 0:
            # Already weighs nothing, so there is nothing to de-duplicate and
            # absorption would change no number. The `absorbed_into` pointer would
            # also be misleading: a primary that counted the *pattern* never
            # looked at why *verification* came up short. An
            # `INSUFFICIENT_EVIDENCE` factor is the case that matters, and it is
            # kept visible with no pointer at all.
            #
            # Note this is not the same test as `is_uncertainty`: an
            # `UNVERIFIED_CLAIM` factor is also "uncertain" but does carry weight,
            # and it genuinely is a second account of the same signal.
            updated.append(factor)
            continue
        primary = next(
            (
                primary_by_claim[claim_id]
                for claim_id in factor.claim_ids
                if claim_id in primary_by_claim
            ),
            None,
        )
        if primary is None or primary.id == factor.id:
            updated.append(factor)
            continue
        updated.append(
            factor.model_copy(update={"contribution": 0, "absorbed_into": primary.id})
        )
    return tuple(updated)


def attach_absorption_context(
    factors: tuple[RiskFactor, ...],
) -> tuple[RiskFactor, ...]:
    """Record absorbed factors' statuses on the factor that counted them.

    Args:
        factors: Factors after `absorb_duplicate_signals`.

    Returns:
        The factors with `verification_statuses` merged onto the primaries, in
        input order.
    """
    absorbed: dict[str, list] = {}
    for factor in factors:
        if factor.absorbed_into:
            absorbed.setdefault(factor.absorbed_into, []).append(factor)

    updated: list[RiskFactor] = []
    for factor in factors:
        dependents = absorbed.get(factor.id)
        if not dependents:
            updated.append(factor)
            continue
        statuses = list(factor.verification_statuses)
        for dependent in dependents:
            for status in dependent.verification_statuses:
                if status not in statuses:
                    statuses.append(status)
        updated.append(
            factor.model_copy(update={"verification_statuses": tuple(statuses)})
        )
    return tuple(updated)


__all__ = [
    "CLAIM_TYPE_RED_FLAG_CODES",
    "absorb_duplicate_signals",
    "attach_absorption_context",
    "attach_claims",
    "attach_evidence",
    "claim_links_to",
    "collapse_duplicate_red_flags",
    "collapse_duplicate_results",
    "spans_overlap",
]