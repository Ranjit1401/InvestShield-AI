"""Object factories shared by the Phase 6 risk tests.

Risk consumes objects from Phases 1, 2, 4 and 5. The Phase 4 and Phase 5
factories already build coherent `Claim` and `VerificationResult` objects, so this
module re-exports those and adds only the two shapes risk needs that nothing else
provides: a `RedFlag` at a chosen span, and an `EvidenceResponse` that can be
attached to a factor.

The `RedFlag` span is the reason this file exists. Phase 6 links a claim to a red
flag by **span overlap**, so a test about linking must be able to place two
signals in the same submitted text at chosen offsets — which the Phase 4
`make_claim` factory, which always spans `(0, len(text))`, cannot express.

No network, no credentials, no LLM: every fixture is in-memory.
"""

from __future__ import annotations

from app.schemas.claims import Claim, ClaimType
from app.schemas.common import EvidenceSpan
from app.schemas.evidence import (
    EvidenceItem,
    EvidenceRelation,
    EvidenceRelevance,
    EvidenceResponse,
    EvidenceSource,
    EvidenceType,
)
from app.schemas.red_flags import RedFlag, RedFlagCode, Severity
from app.schemas.verification import (
    AUTHORITATIVE_SOURCE_CONFIRMS,
    AUTHORITATIVE_SOURCE_CONTRADICTS,
    IDENTITY_AMBIGUOUS,
    IDENTITY_NOT_FOUND,
    NO_CLAIM_RELEVANT_SOURCE,
    NO_CONFIRMATION_FOUND,
    NOT_A_FACTUAL_CLAIM,
    SEARCH_UNAVAILABLE,
    ZERO_RESULTS,
    SourceTier,
    VerificationResult,
    VerificationStatus,
)
from app.schemas.search import SourceType
from tests.evidence_factories import make_assessed, make_verification
from tests.verification_factories import make_claim

#: Severity per red-flag code, matching Phase 1's rule table closely enough that
#: a test about severity is not really a test about this factory. Codes absent
#: from Phase 1's table fall back to `MEDIUM` via `severity_for`.
SEVERITY_BY_CODE: dict[RedFlagCode, Severity] = {
    RedFlagCode.GUARANTEED_RETURN: Severity.CRITICAL,
    RedFlagCode.UNREALISTIC_RETURN: Severity.HIGH,
    RedFlagCode.FAKE_REGULATORY_CLAIM: Severity.CRITICAL,
    RedFlagCode.UNVERIFIED_ADVISER: Severity.HIGH,
    RedFlagCode.THIRD_PARTY_PAYMENT: Severity.HIGH,
    RedFlagCode.ACCOUNT_ACTIVATION_FEE: Severity.HIGH,
    RedFlagCode.WITHDRAWAL_FEE: Severity.HIGH,
    RedFlagCode.BORROW_TO_INVEST: Severity.MEDIUM,
    RedFlagCode.URGENCY_PRESSURE: Severity.MEDIUM,
    RedFlagCode.SUSPICIOUS_URL: Severity.MEDIUM,
    RedFlagCode.FAKE_PROFIT_SCREENSHOT: Severity.MEDIUM,
    RedFlagCode.IMPERSONATION: Severity.HIGH,
    RedFlagCode.APK_DOWNLOAD: Severity.HIGH,
    RedFlagCode.TELEGRAM_INVESTMENT_GROUP: Severity.MEDIUM,
    RedFlagCode.WHATSAPP_INVESTMENT_GROUP: Severity.MEDIUM,
}

#: Plain-language reason per status, used where the test does not care.
DEFAULT_REASON: dict[VerificationStatus, str] = {
    VerificationStatus.VERIFIED: "An authoritative record confirms the claim.",
    VerificationStatus.UNVERIFIED: "No confirming source was found.",
    VerificationStatus.CONTRADICTED: "An authoritative record conflicts with the claim.",
    VerificationStatus.INSUFFICIENT_EVIDENCE: "Verification could not be completed.",
    VerificationStatus.NOT_APPLICABLE: "This statement is not externally verifiable.",
}


def make_flag(
    code: RedFlagCode = RedFlagCode.GUARANTEED_RETURN,
    *,
    start: int = 0,
    end: int = 40,
    matched_text: str = "guaranteed 30% monthly returns",
    severity: Severity | None = None,
) -> RedFlag:
    """Build a Phase 1 `RedFlag` occupying `[start, end)` of a submitted text.

    The `label`, `description` and `rule_reason` come from Phase 1's own rule
    catalogue, because Phase 6 reuses them verbatim and the safety tests check
    that real wording. Inventing wording here would let a Phase 1 phrasing problem
    pass the Phase 6 vocabulary ban unnoticed.

    Args:
        code: The rule that fired.
        start: Span start offset.
        end: Span end offset.
        matched_text: The text the rule matched.
        severity: Override the default severity for `code`.

    Returns:
        A `RedFlag` whose `matched_text` equals its span text.
    """
    from app.services.red_flag_rules import RULES_BY_CODE

    rule = RULES_BY_CODE[code]
    return RedFlag(
        code=code,
        name=rule.name,
        description=rule.description,
        severity=severity if severity is not None else rule.severity,
        weight=0,
        matched_text=matched_text,
        evidence_span=EvidenceSpan(start=start, end=end, text=matched_text),
        rule_reason=(
            f"The content contains text matching the {code.value} rule."
        ),
    )


def make_placed_claim(
    text: str,
    claim_type: ClaimType = ClaimType.GUARANTEE_CLAIM,
    *,
    claim_id: str = "claim_001",
    start: int = 0,
) -> Claim:
    """Build a `Claim` located at `start` in a synthetic original string.

    Args:
        text: Verbatim claim text.
        claim_type: Claim family.
        claim_id: Id to assign.
        start: Span start offset in the submitted text.

    Returns:
        A `Claim` occupying `[start, start + len(text))`.
    """
    return Claim(
        id=claim_id,
        text=text,
        claim_type=claim_type,
        confidence=0.9,
        evidence_span=EvidenceSpan(start=start, end=start + len(text), text=text),
    )


def unverified(claim_id: str = "claim_001", **kwargs: object) -> VerificationResult:
    """An `UNVERIFIED` result from a search that completed and found nothing.

    The default reason is `NO_CONFIRMATION_FOUND`, a completed-search code, so
    this produces an `UNVERIFIED_CLAIM` factor. Use :func:`unsearchable` for the
    case that must not.
    """
    return make_verification(
        claim_id,
        status=VerificationStatus.UNVERIFIED,
        reason_code=NO_CONFIRMATION_FOUND,
        reason="No confirming source was found.",
        source_ids=(),
        matched_result_ids=(),
        **kwargs,  # type: ignore[arg-type]
    )


def unsearchable(claim_id: str = "claim_001", **kwargs: object) -> VerificationResult:
    """An `INSUFFICIENT_EVIDENCE` result whose search never ran.

    This is the case that must contribute nothing: the tool could not look, which
    is not the same as looking and finding nothing.
    """
    return make_verification(
        claim_id,
        status=VerificationStatus.INSUFFICIENT_EVIDENCE,
        reason_code=SEARCH_UNAVAILABLE,
        reason="External web search is not configured.",
        confidence=0.0,
        source_ids=(),
        matched_result_ids=(),
        queries=(),
        **kwargs,  # type: ignore[arg-type]
    )


def unverified_because_search_never_ran(
    claim_id: str = "claim_001",
) -> VerificationResult:
    """An `UNVERIFIED` result whose reason says the search never ran.

    The schema permits this pairing even though Phase 4's engine does not emit it,
    and it is exactly the input that would quietly turn a connectivity failure
    into an accusation. It must not score.
    """
    return make_verification(
        claim_id,
        status=VerificationStatus.UNVERIFIED,
        reason_code=SEARCH_UNAVAILABLE,
        reason="External web search is not configured.",
        confidence=0.0,
        source_ids=(),
        matched_result_ids=(),
        queries=(),
    )


def contradicted(claim_id: str = "claim_001", **kwargs: object) -> VerificationResult:
    """A `CONTRADICTED` result backed by a cited authoritative source."""
    return make_verification(
        claim_id,
        status=VerificationStatus.CONTRADICTED,
        reason_code=AUTHORITATIVE_SOURCE_CONTRADICTS,
        reason="An authoritative record conflicts with the claim.",
        source_ids=("src_000000000002",),
        matched_result_ids=("res_000000000002",),
        **kwargs,  # type: ignore[arg-type]
    )


def verified(claim_id: str = "claim_001", **kwargs: object) -> VerificationResult:
    """A `VERIFIED` result. Produces no risk factor by design."""
    return make_verification(
        claim_id,
        status=VerificationStatus.VERIFIED,
        reason_code=AUTHORITATIVE_SOURCE_CONFIRMS,
        reason="An authoritative record confirms the claim.",
        **kwargs,  # type: ignore[arg-type]
    )


def not_applicable(claim_id: str = "claim_001") -> VerificationResult:
    """A `NOT_APPLICABLE` result. Produces no risk factor, ever."""
    return make_verification(
        claim_id,
        status=VerificationStatus.NOT_APPLICABLE,
        reason_code=NOT_A_FACTUAL_CLAIM,
        reason="This statement is not externally verifiable.",
        confidence=0.0,
        source_ids=(),
        matched_result_ids=(),
        queries=(),
    )


def evidence_for(
    claim_id: str = "claim_001",
    *,
    url: str = "https://www.sebi.gov.in/records",
    status: VerificationStatus = VerificationStatus.CONTRADICTED,
) -> EvidenceResponse:
    """Build a Phase 5 `EvidenceResponse` with one authoritative, proof-grade item.

    The `src_`/`res_` ids derive from the document's canonical URL exactly as
    Phase 4 derives them, so an evidence id attached by `attach_evidence` can be
    traced back to a real Phase 4 source id.

    Args:
        claim_id: The claim the evidence bears on.
        url: The document's URL.
        status: The Phase 4 status carried through onto the response.

    Returns:
        An `EvidenceResponse` whose single item contradicts the claim, and is
        `is_proof` because it is an authoritative, claim-relevant conflict.
    """
    assessed = make_assessed(url, title="SEBI public register", stance="contradicts")
    reason_code = (
        AUTHORITATIVE_SOURCE_CONTRADICTS
        if status is VerificationStatus.CONTRADICTED
        else NO_CLAIM_RELEVANT_SOURCE
    )
    source = EvidenceSource(
        source_id=assessed.source_id,
        result_id=assessed.result_id,
        url=assessed.url,
        canonical_url=assessed.canonical_url,
        domain=assessed.source_domain,
        title=assessed.title,
        source_type=SourceType.GOVERNMENT,
        source_tier=SourceTier.TIER_1_PRIMARY_REGULATOR,
        position=assessed.position,
    )
    item = EvidenceItem(
        id=f"ev_{assessed.result_id[4:]}",
        claim_id=claim_id,
        verification_status=status,
        evidence_type=EvidenceType.REGULATORY_RECORD,
        relationship=EvidenceRelation.CONTRADICTS,
        relevance=EvidenceRelevance.HIGH,
        excerpt="No registration record exists for the claimed adviser.",
        excerpt_origin="snippet",
        source=source,
    )
    return EvidenceResponse(
        claim_id=claim_id,
        verification_status=status,
        verification_reason_code=reason_code,
        evidence=(item,),
        sources=(source,),
    )


__all__ = [
    "DEFAULT_REASON",
    "IDENTITY_AMBIGUOUS",
    "IDENTITY_NOT_FOUND",
    "NO_CONFIRMATION_FOUND",
    "SEVERITY_BY_CODE",
    "ZERO_RESULTS",
    "contradicted",
    "evidence_for",
    "make_claim",
    "make_flag",
    "make_placed_claim",
    "make_verification",
    "not_applicable",
    "unsearchable",
    "unverified",
    "unverified_because_search_never_ran",
    "verified",
]
