"""Verification decisions and explanations (Phase 4, §5).

One pure function turns a claim, its searches, and the assessed documents into
exactly one `VerificationResult`. No LLM, no randomness, no hidden state: the
same inputs always produce the same status, reason code, confidence and wording
(D-008, D-022).

The decision table, in order:

| # | Condition | Status | Reason code |
| --- | --- | --- | --- |
| 1 | Claim family is an instruction or opinion | `NOT_APPLICABLE` | `NOT_A_FACTUAL_CLAIM` |
| 2 | No query could be built | `INSUFFICIENT_EVIDENCE` | `NO_QUERY_BUILT` |
| 3 | Search never attempted (no credentials) | `INSUFFICIENT_EVIDENCE` | `SEARCH_UNAVAILABLE` |
| 4 | Search attempted and failed | `INSUFFICIENT_EVIDENCE` | `SEARCH_FAILED` |
| 5 | No result from a relevant authority | `INSUFFICIENT_EVIDENCE` | `NO_CLAIM_RELEVANT_SOURCE` |
| 6 | Only a similarly named organisation matched | `INSUFFICIENT_EVIDENCE` | `IDENTITY_AMBIGUOUS` |
| 7 | No result named the claimed entity | `UNVERIFIED` | `IDENTITY_NOT_FOUND` |
| 8 | Successful searches, zero results, registration-style claim | `UNVERIFIED` | `ZERO_RESULTS` |
| 9 | Successful searches, zero results, other claim family | `INSUFFICIENT_EVIDENCE` | `NO_CONFIRMATION_FOUND` |
| 10 | Relevant authoritative source supports | `VERIFIED` | `AUTHORITATIVE_SOURCE_CONFIRMS` |
| 11 | Relevant authoritative source contradicts | `CONTRADICTED` | `AUTHORITATIVE_SOURCE_CONTRADICTS` |
| 12 | Authoritative sources both support and contradict | `INSUFFICIENT_EVIDENCE` | `CONFLICTING_AUTHORITATIVE_SOURCES` |

Two properties hold by construction and are guarded by tests:

* **Absence is never contradiction.** Rows 5–9 cannot produce `CONTRADICTED`.
  Only row 11 can, and only from a direct conflict with a claim-relevant
  authoritative record (D-006).
* **`UNVERIFIED` is not an accusation.** Its wording says what was searched and
  what was not found, never that the claim is false.

Explanations are rendered from fixed templates keyed by reason code. No wording
is generated, so the phrasing of a status cannot drift into an accusation, and
it can be audited line by line (D-022).
"""

from __future__ import annotations

import re

from app.schemas.search import SearchResponse, SearchResult, SearchStatus
from app.schemas.verification import (
    AUTHORITATIVE_SOURCE_CONTRADICTS,
    AUTHORITATIVE_SOURCE_CONFIRMS,
    CONFLICTING_AUTHORITATIVE_SOURCES,
    IDENTITY_AMBIGUOUS,
    IDENTITY_NOT_FOUND,
    NO_CLAIM_RELEVANT_SOURCE,
    NO_CONFIRMATION_FOUND,
    NO_QUERY_BUILT,
    NOT_A_FACTUAL_CLAIM,
    SEARCH_FAILED,
    SEARCH_UNAVAILABLE,
    ZERO_RESULTS,
    IdentityMatch,
    VerificationResult,
    VerificationStatus,
)
from app.services.verification.comparator import (
    AssessedSource,
    Stance,
    assess_results,
)
from app.services.verification.target import VerificationTarget

#: Explanation templates, one per reason code. `{placeholders}` are filled from a
#: fixed context dict; an absent placeholder renders as an empty string rather
#: than raising, so a missing detail can never break a report.
EXPLANATION_TEMPLATES: dict[str, str] = {
    AUTHORITATIVE_SOURCE_CONFIRMS: (
        "An official {authority} record directly supports this claim about {subject}. "
        "This confirms what the record states; it is a statement about the record "
        "and nothing more."
    ),
    NO_CONFIRMATION_FOUND: (
        "Records relevant to this claim were searched, but none of them addressed "
        "this specific claim about {subject}. This is not evidence that the claim "
        "is false."
    ),
    AUTHORITATIVE_SOURCE_CONTRADICTS: (
        "An official {authority} record directly conflicts with this claim about "
        "{subject}. This reports a disagreement with the record only."
    ),
    CONFLICTING_AUTHORITATIVE_SOURCES: (
        "Official records disagree about this claim for {subject}: {support_count} "
        "support it and {contradiction_count} conflict with it. The disagreement "
        "could not be resolved from the records retrieved."
    ),
    SEARCH_UNAVAILABLE: (
        "External search was not configured, so no source could be consulted and "
        "this claim was not checked."
    ),
    SEARCH_FAILED: (
        "The external search for this claim did not complete, so no source could "
        "be consulted."
    ),
    ZERO_RESULTS: (
        "No matching entry for {subject} was found in the authoritative records "
        "searched. This is an absence of confirmation, not a finding that the "
        "claim is false."
    ),
    IDENTITY_AMBIGUOUS: (
        "Search results referred to a similarly named organisation rather than "
        "{subject}, so the claim could not be attributed to a single entity."
    ),
    IDENTITY_NOT_FOUND: (
        "No retrieved source identified {subject} as the organisation this claim "
        "is about, so the claim could not be confirmed."
    ),
    NO_CLAIM_RELEVANT_SOURCE: (
        "Results were retrieved, but none came from an authority whose records can "
        "address this type of claim about {subject}."
    ),
    NO_QUERY_BUILT: (
        "No searchable form of this claim could be constructed from the claim text "
        "and its linked entities, so nothing was searched."
    ),
    NOT_A_FACTUAL_CLAIM: (
        "This statement is an instruction or opinion rather than a factual assertion "
        "about a named party, so external records cannot confirm it."
    ),
}

#: Confidence in the *status assigned*, given the evidence found. Never a
#: probability of fraud, of loss, or of the claim's truth.
#:
#: A status that asserts something about the world (`VERIFIED`, `CONTRADICTED`)
#: scores higher than one that reports our own limits, and "we could not look"
#: scores zero because it is exactly that: no evidence was gathered (D-006).
CONFIDENCE_BY_REASON: dict[str, float] = {
    AUTHORITATIVE_SOURCE_CONFIRMS: 0.80,
    AUTHORITATIVE_SOURCE_CONTRADICTS: 0.80,
    CONFLICTING_AUTHORITATIVE_SOURCES: 0.40,
    ZERO_RESULTS: 0.45,
    IDENTITY_NOT_FOUND: 0.45,
    NOT_A_FACTUAL_CLAIM: 0.60,
    NO_CONFIRMATION_FOUND: 0.35,
    NO_CLAIM_RELEVANT_SOURCE: 0.30,
    IDENTITY_AMBIGUOUS: 0.25,
    SEARCH_UNAVAILABLE: 0.0,
    SEARCH_FAILED: 0.0,
    NO_QUERY_BUILT: 0.0,
}

#: Adjustment applied to `VERIFIED`/`CONTRADICTED` confidence by the tier of the
#: best source relied on. A primary regulator outranks an exchange listing.
_TIER_CONFIDENCE_BONUS: dict[str, float] = {
    "TIER_1_PRIMARY_REGULATOR": 0.05,
    "TIER_2_GOVERNMENT": 0.0,
    "TIER_3_EXCHANGE": -0.05,
}

#: Maximum warning lines attached to one result, so a provider that returns many
#: notes cannot crowd out the claim-level explanation.
MAX_WARNINGS = 4

#: Terminology that must never appear in user-facing verification output. The
#: product reports facts about claims; it does not deliver verdicts (D-006,
#: D-022).
PROHIBITED_OUTPUT_TERMS: frozenset[str] = frozenset(
    {
        "scam",
        "scams",
        "scamming",
        "fraud",
        "frauds",
        "fraudulent",
        "fraudulence",
        "safe",
        "safely",
        "safety",
        "dangerous",
        "danger",
        "buy",
        "sell",
        "invest",
        "legitimate",
        "legit",
    }
)

_PROHIBITED_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(PROHIBITED_OUTPUT_TERMS)) + r")\b", re.IGNORECASE
)

#: Fixed warnings attached per outcome, independent of provider wording.
_STATUS_WARNINGS: dict[str, tuple[str, ...]] = {
    AUTHORITATIVE_SOURCE_CONFIRMS: (
        "Verification covers this claim only; other statements in the same content "
        "are assessed separately.",
    ),
    AUTHORITATIVE_SOURCE_CONTRADICTS: (
        "This status reflects a conflict with an official record only, and is not "
        "an assessment of the opportunity.",
    ),
    CONFLICTING_AUTHORITATIVE_SOURCES: (
        "Official records disagreed, so no status could be assigned to this claim.",
    ),
    ZERO_RESULTS: (
        "Absence of a matching record is not evidence that the claim is false.",
    ),
    IDENTITY_NOT_FOUND: (
        "A source naming a different or similarly named organisation cannot confirm "
        "this claim.",
    ),
    IDENTITY_AMBIGUOUS: (
        "Results referred to a similarly named organisation; identity could not be "
        "established from the sources retrieved.",
    ),
    NO_CLAIM_RELEVANT_SOURCE: (
        "Only sources that cannot settle this claim family were available; they "
        "carry no verdict.",
    ),
    NO_CONFIRMATION_FOUND: (
        "No source addressed this claim; this is not evidence that the claim is "
        "false.",
    ),
    SEARCH_UNAVAILABLE: (
        "External search is unavailable, so no claim in this investigation could be "
        "checked against external sources.",
    ),
    SEARCH_FAILED: (
        "One or more external searches did not complete; coverage of this claim is "
        "limited.",
    ),
    NO_QUERY_BUILT: (
        "Nothing was searched for this claim, so it remains unassessed.",
    ),
    NOT_A_FACTUAL_CLAIM: (
        "This statement is not externally verifiable, so no search was performed "
        "for it.",
    ),
}


def contains_prohibited_term(text: str) -> str | None:
    """Return the first prohibited term found in `text`, or ``None``.

    Used by the guard test that asserts verification output never contains
    verdict language.

    Args:
        text: Text to inspect.

    Returns:
        The matched term in lower case, or ``None``.
    """
    match = _PROHIBITED_PATTERN.search(text or "")
    return match.group(1).lower() if match else None


def render_explanation(reason_code: str, context: dict[str, str] | None = None) -> str:
    """Render the fixed explanation for a reason code.

    Args:
        reason_code: One of `VERIFICATION_REASON_CODES`.
        context: Placeholder values. Unknown or missing keys render empty.

    Returns:
        The explanation text.

    Raises:
        KeyError: If `reason_code` has no template, which would mean the schema
            and the decision engine have drifted apart.
    """
    template = EXPLANATION_TEMPLATES[reason_code]
    values = {key: str(value) for key, value in (context or {}).items()}
    return " ".join(template.format_map(_SafeDict(values)).split())


class _SafeDict(dict[str, str]):
    """Mapping that renders missing placeholders as empty text."""

    def __missing__(self, key: str) -> str:  # pragma: no cover - trivial
        return ""


def _dedupe_warnings(*groups: tuple[str, ...]) -> tuple[str, ...]:
    """Merge warning groups, preserving order and dropping duplicates."""
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for warning in group:
            cleaned = " ".join(warning.split())
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            merged.append(cleaned)
    return tuple(merged[:MAX_WARNINGS])


def _collect_results(responses: tuple[SearchResponse, ...]) -> tuple[SearchResult, ...]:
    """Collect de-duplicated results from every successful response.

    Args:
        responses: Every response issued for the claim, in query order.

    Returns:
        Results in first-seen order, de-duplicated by canonical URL so one
        document found by two queries is assessed once.
    """
    seen: set[str] = set()
    collected: list[SearchResult] = []
    for response in responses:
        if response.status is not SearchStatus.OK:
            continue
        for result in response.results:
            key = result.canonical_url or result.url
            if key in seen:
                continue
            seen.add(key)
            collected.append(result)
    return tuple(collected)


def _result(
    target: VerificationTarget,
    status: VerificationStatus,
    reason_code: str,
    context: dict[str, str],
    *,
    confidence: float | None = None,
    source_ids: tuple[str, ...] = (),
    result_ids: tuple[str, ...] = (),
    queries: tuple[str, ...] = (),
    extra_warnings: tuple[str, ...] = (),
) -> VerificationResult:
    """Assemble one `VerificationResult` with consistent fields.

    Args:
        target: The claim's verification target.
        status: The decided status.
        reason_code: The structured cause.
        context: Placeholders for the explanation template.
        confidence: Override for the confidence table.
        source_ids: Documents relied on. Only populated for statuses that assert
            a source said something; contextual documents are deliberately left
            in `matched_result_ids` so no status appears to have support behind
            it that was not found.
        result_ids: Search results that addressed the claim.
        queries: Queries actually issued.
        extra_warnings: Additional warnings to merge in.

    Returns:
        A validated `VerificationResult`.
    """
    warnings = _dedupe_warnings(
        _STATUS_WARNINGS.get(reason_code, ()), extra_warnings
    )
    resolved_confidence = (
        confidence if confidence is not None else CONFIDENCE_BY_REASON[reason_code]
    )
    return VerificationResult(
        claim_id=target.claim_id,
        claim_type=target.claim_type,
        status=status,
        reason=render_explanation(reason_code, context),
        reason_code=reason_code,
        confidence=max(0.0, min(1.0, round(resolved_confidence, 2))),
        source_ids=source_ids,
        matched_result_ids=result_ids,
        queries=queries,
        warnings=warnings,
    )


def _context_for(target: VerificationTarget, authority_name: str | None = None) -> dict[str, str]:
    """Build the explanation context for a target.

    Args:
        target: The verification target.
        authority_name: Name of the authority relied on, when one was.

    Returns:
        Placeholder values, always including a human-readable subject.
    """
    return {
        "subject": target.subject or "the named party",
        "authority": authority_name or "official",
    }


def _best_tier_bonus(sources: tuple[AssessedSource, ...]) -> float:
    """Return the confidence adjustment for the best-tier source relied on."""
    if not sources:
        return 0.0
    best = min(sources, key=lambda source: source.rank)
    return _TIER_CONFIDENCE_BONUS.get(best.tier.value, 0.0)


def decide_verification(
    target: VerificationTarget,
    responses: tuple[SearchResponse, ...] = (),
) -> VerificationResult:
    """Decide the verification status of one claim.

    Args:
        target: The resolved verification target.
        responses: Every `SearchResponse` issued for this claim, in query order.
            Empty means no search was attempted.

    Returns:
        A validated `VerificationResult`. The function is pure: the same target
        and responses always produce the same status, reason, confidence,
        citations and warnings.

    Raises:
        KeyError: If the decision table references a reason code with no
            confidence or explanation template, which means the tables have
            drifted apart.
    """
    context = _context_for(target)
    issued_queries = tuple(
        dict.fromkeys(
            response.query for response in responses if response.query.strip()
        )
    )

    # Row 1: an instruction or an opinion cannot be confirmed by a record.
    if not target.is_applicable:
        return _result(
            target,
            VerificationStatus.NOT_APPLICABLE,
            NOT_A_FACTUAL_CLAIM,
            context,
            queries=(),
        )

    # Row 2: either no query could be built, or none was ever issued. Both mean
    # nothing was looked up, which is a limitation and not a finding.
    if not issued_queries or not responses:
        return _result(
            target,
            VerificationStatus.INSUFFICIENT_EVIDENCE,
            NO_QUERY_BUILT,
            context,
            queries=issued_queries,
        )

    ok_responses = tuple(
        response for response in responses if response.status is SearchStatus.OK
    )
    error_responses = tuple(
        response for response in responses if response.status is SearchStatus.ERROR
    )
    unavailable_responses = tuple(
        response for response in responses if response.status is SearchStatus.UNAVAILABLE
    )
    provider_warnings = _dedupe_warnings(
        *(response.warnings for response in responses)
    )
    error_codes = tuple(
        dict.fromkeys(
            response.error_code for response in error_responses if response.error_code
        )
    )

    # Rows 3 and 4: "we could not look" is not "we found nothing".
    if not ok_responses:
        if error_responses:
            return _result(
                target,
                VerificationStatus.INSUFFICIENT_EVIDENCE,
                SEARCH_FAILED,
                context,
                queries=issued_queries,
                extra_warnings=_dedupe_warnings(
                    provider_warnings,
                    tuple(f"Search error code: {code}." for code in error_codes),
                ),
            )
        return _result(
            target,
            VerificationStatus.INSUFFICIENT_EVIDENCE,
            SEARCH_UNAVAILABLE,
            context,
            queries=issued_queries,
            extra_warnings=provider_warnings,
        )

    results = _collect_results(responses)
    degraded_note = ""
    if error_responses or unavailable_responses:
        degraded_note = (
            f"{len(error_responses) + len(unavailable_responses)} of "
            f"{len(responses)} search(es) did not complete, so coverage of this "
            "claim is partial."
        )

    # Rows 8 and 9: successful searches that matched nothing.
    if not results:
        if target.is_registration_style:
            return _result(
                target,
                VerificationStatus.UNVERIFIED,
                ZERO_RESULTS,
                context,
                queries=issued_queries,
                extra_warnings=_dedupe_warnings(provider_warnings, (degraded_note,)),
            )
        return _result(
            target,
            VerificationStatus.INSUFFICIENT_EVIDENCE,
            NO_CONFIRMATION_FOUND,
            context,
            queries=issued_queries,
            extra_warnings=_dedupe_warnings(provider_warnings, (degraded_note,)),
        )

    assessments = assess_results(results, target)
    result_ids = tuple(
        assessment.result_id
        for assessment in assessments
        if assessment.identity is IdentityMatch.MATCHED
    )
    relevant = tuple(assessment for assessment in assessments if assessment.claim_relevant)

    # Row 5: nothing retrieved could speak to this claim family.
    if not relevant:
        return _result(
            target,
            VerificationStatus.INSUFFICIENT_EVIDENCE,
            NO_CLAIM_RELEVANT_SOURCE,
            context,
            queries=issued_queries,
            result_ids=result_ids,
            extra_warnings=_dedupe_warnings(provider_warnings, (degraded_note,)),
        )

    identity_matched = tuple(
        assessment
        for assessment in relevant
        if assessment.identity is IdentityMatch.MATCHED
    )

    # Row 6: only a similarly named organisation appears.
    if not identity_matched:
        if any(assessment.identity is IdentityMatch.AMBIGUOUS for assessment in relevant):
            return _result(
                target,
                VerificationStatus.INSUFFICIENT_EVIDENCE,
                IDENTITY_AMBIGUOUS,
                context,
                queries=issued_queries,
                extra_warnings=_dedupe_warnings(provider_warnings, (degraded_note,)),
            )
        # Row 7: the claim's party simply is not in anything we found.
        return _result(
            target,
            VerificationStatus.UNVERIFIED,
            IDENTITY_NOT_FOUND,
            context,
            queries=issued_queries,
            extra_warnings=_dedupe_warnings(provider_warnings, (degraded_note,)),
        )

    supporting = tuple(
        sorted(
            (item for item in identity_matched if item.stance == Stance.SUPPORTS),
            key=lambda item: item.rank,
        )
    )
    contradicting = tuple(
        sorted(
            (item for item in identity_matched if item.stance == Stance.CONTRADICTS),
            key=lambda item: item.rank,
        )
    )

    # Row 12: authoritative sources disagree; refuse to pick a side.
    if supporting and contradicting:
        conflict_context = dict(context)
        conflict_context["support_count"] = str(len(supporting))
        conflict_context["contradiction_count"] = str(len(contradicting))
        return _result(
            target,
            VerificationStatus.INSUFFICIENT_EVIDENCE,
            CONFLICTING_AUTHORITATIVE_SOURCES,
            conflict_context,
            queries=issued_queries,
            result_ids=result_ids,
            extra_warnings=_dedupe_warnings(provider_warnings, (degraded_note,)),
        )

    # Row 10: an authoritative, claim-relevant record directly supports the claim.
    if supporting:
        best = supporting[0]
        return _result(
            target,
            VerificationStatus.VERIFIED,
            AUTHORITATIVE_SOURCE_CONFIRMS,
            _context_for(target, best.authority_name),
            confidence=CONFIDENCE_BY_REASON[AUTHORITATIVE_SOURCE_CONFIRMS]
            + _best_tier_bonus(supporting),
            source_ids=tuple(item.source_id for item in supporting),
            result_ids=result_ids,
            queries=issued_queries,
            extra_warnings=_dedupe_warnings(provider_warnings, (degraded_note,)),
        )

    # Row 11: an authoritative, claim-relevant record directly conflicts.
    if contradicting:
        best = contradicting[0]
        return _result(
            target,
            VerificationStatus.CONTRADICTED,
            AUTHORITATIVE_SOURCE_CONTRADICTS,
            _context_for(target, best.authority_name),
            confidence=CONFIDENCE_BY_REASON[AUTHORITATIVE_SOURCE_CONTRADICTS]
            + _best_tier_bonus(contradicting),
            source_ids=tuple(item.source_id for item in contradicting),
            result_ids=result_ids,
            queries=issued_queries,
            extra_warnings=_dedupe_warnings(provider_warnings, (degraded_note,)),
        )

    # The claim's party was found, but no retrieved record speaks to the claim.
    return _result(
        target,
        VerificationStatus.INSUFFICIENT_EVIDENCE,
        NO_CLAIM_RELEVANT_SOURCE,
        context,
        queries=issued_queries,
        result_ids=result_ids,
        extra_warnings=_dedupe_warnings(provider_warnings, (degraded_note,)),
    )


__all__ = [
    "CONFIDENCE_BY_REASON",
    "EXPLANATION_TEMPLATES",
    "MAX_WARNINGS",
    "PROHIBITED_OUTPUT_TERMS",
    "contains_prohibited_term",
    "decide_verification",
    "render_explanation",
]