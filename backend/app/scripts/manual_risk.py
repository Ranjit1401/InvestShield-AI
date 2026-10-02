"""Manual smoke test for the Phase 6 risk engine.

Not part of the pytest suite. Run it directly to see what the risk layer does in
this environment, offline, with no key and no network:

    cd backend
    python -m app.scripts.manual_risk

Three passes, in increasing order of how much they should worry you:

1. **Nothing found.** An investigation with no indicators scores 0. This is the
   case a reader is most likely to misread, so the script prints the standing
   caveat alongside it rather than showing a bare zero.
2. **One signal, four stages.** A guaranteed-return promise detected by Phase 1,
   extracted by Phase 2, unverified by Phase 4 and backed by a search result from
   Phase 5. The script shows the score is charged **once**, and that the other
   three views are attached to it as provenance rather than added to it.
3. **Could not look.** The same content with the search provider unavailable. The
   score is 0, not higher, because absence of evidence is not evidence of risk.

Pass 3 is the one to watch. The failure mode this whole phase is built against is
a tool that reports its own blindness as somebody else's guilt.
"""

from __future__ import annotations

import sys

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
from app.schemas.red_flags import RedFlagCode
from app.schemas.search import SourceType
from app.schemas.verification import (
    NO_CONFIRMATION_FOUND,
    SEARCH_UNAVAILABLE,
    SourceTier,
    VerificationStatus,
)
from app.services.risk import RiskService
from app.services.red_flag_engine import RedFlagEngine

CONTENT = (
    "Acme Capital Advisors is SEBI approved and guarantees 30% monthly returns. "
    "Join our Telegram group today."
)


def build_claim(
    claim_id: str, text: str, claim_type: ClaimType, start: int
) -> Claim:
    """Build a claim located inside ``CONTENT`` at ``start``."""
    return Claim(
        id=claim_id,
        text=text,
        claim_type=claim_type,
        confidence=0.9,
        evidence_span=EvidenceSpan(start=start, end=start + len(text), text=text),
    )


def build_evidence(claim_id: str) -> EvidenceResponse:
    """Build a Phase 5 response holding one authoritative, conflicting record."""
    source = EvidenceSource(
        source_id="src_000000000001",
        result_id="res_000000000001",
        url="https://www.sebi.gov.in/records",
        canonical_url="https://www.sebi.gov.in/records",
        domain="sebi.gov.in",
        title="SEBI public register",
        source_type=SourceType.REGULATOR,
        source_tier=SourceTier.TIER_1_PRIMARY_REGULATOR,
    )
    item = EvidenceItem(
        id="ev_000000000001",
        claim_id=claim_id,
        verification_status=VerificationStatus.CONTRADICTED,
        evidence_type=EvidenceType.REGULATORY_RECORD,
        relationship=EvidenceRelation.CONTRADICTS,
        relevance=EvidenceRelevance.HIGH,
        excerpt="No registration record exists for the claimed adviser.",
        excerpt_origin="snippet",
        source=source,
    )
    return EvidenceResponse(
        claim_id=claim_id,
        verification_status=VerificationStatus.CONTRADICTED,
        verification_reason_code="AUTHORITATIVE_SOURCE_CONTRADICTS",
        evidence=(item,),
        sources=(source,),
    )


def print_assessment(title: str, assessment: object) -> None:
    """Print one assessment in the shape a report would use it.

    Args:
        title: What this pass is demonstrating.
        assessment: The `RiskAssessment` to print.
    """
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")
    print(f"risk score   : {assessment.risk_score} / {assessment.thresholds.ceiling}")
    print(f"risk level   : {assessment.risk_level.value}")
    print(f"factors      : {assessment.total_factors} emitted, "
          f"{assessment.scoring_factors} scoring, "
          f"{assessment.deduplicated_factors} de-duplicated")
    print(f"claims       : {assessment.relevant_claims} risk-relevant, "
          f"{assessment.assessed_claims} assessed")
    print(f"coverage     : {assessment.evidence_coverage:.2f}  "
          f"completeness: {assessment.analysis_completeness:.2f}")

    print("\nfactor breakdown:")
    for factor in assessment.factors:
        if factor.absorbed_into:
            mark = f"absorbed into {factor.absorbed_into}"
        elif factor.is_scoring:
            mark = f"+{factor.contribution}"
        else:
            mark = "0 (weighs nothing)"
        print(f"  [{factor.origin.value:12}] {factor.factor_type.value}")
        print(f"      {factor.label}")
        print(f"      {mark}  (weight {factor.weight})")
        print(f"      because: {factor.reason}")
        if factor.evidence_ids:
            print(f"      evidence: {', '.join(factor.evidence_ids)}")
        if factor.verification_statuses:
            statuses = ", ".join(s.value for s in factor.verification_statuses)
            print(f"      verification context: {statuses}")

    print("\nwarnings:")
    for warning in assessment.warnings:
        print(f"  - {warning}")


def pass_nothing_found(service: RiskService) -> None:
    """No indicators at all. The correct answer, and easy to misread."""
    print_assessment("PASS 1  Nothing found", service.assess())


def pass_one_signal_four_stages(service: RiskService) -> None:
    """One behaviour noticed by four stages must score exactly once."""
    engine = RedFlagEngine()
    flags = tuple(engine.detect(CONTENT))

    claim = build_claim(
        "claim_001", "Acme Capital Advisors is SEBI approved",
        ClaimType.REGULATORY_STATUS, CONTENT.index("Acme Capital Advisors is SEBI"),
    )

    from app.services.verification import VerificationResult

    unverified = VerificationResult(
        claim_id=claim.id,
        claim_type=claim.claim_type,
        status=VerificationStatus.UNVERIFIED,
        reason="No confirming source was found for the registration claim.",
        reason_code=NO_CONFIRMATION_FOUND,
        confidence=0.1,
    )

    print(f"\ndetected {len(flags)} red flag(s) in the sample content:")
    for flag in flags:
        print(f"  {flag.code.value:28} severity={flag.severity.value}")

    print_assessment(
        "PASS 2  One signal, four stages",
        service.assess(
            flags,
            (claim,),
            (unverified,),
            (build_evidence(claim.id),),
        ),
    )

    print(
        "\nThe registration claim was extracted by Phase 2 and found unconfirmed by\n"
        "Phase 4, while Phase 1 fired a rule over the same words. One signal, one\n"
        "contribution: the other view is kept as provenance at zero."
    )


def pass_could_not_look(service: RiskService) -> None:
    """No search data. The score must not go up."""
    engine = RedFlagEngine()
    flags = tuple(engine.detect(CONTENT))
    claim = build_claim(
        "claim_001", "Acme Capital Advisors is SEBI approved",
        ClaimType.REGULATORY_STATUS, CONTENT.index("Acme Capital Advisors is SEBI"),
    )

    from app.services.verification import VerificationResult

    unsearchable = VerificationResult(
        claim_id=claim.id,
        claim_type=claim.claim_type,
        status=VerificationStatus.INSUFFICIENT_EVIDENCE,
        reason="External web search is not configured.",
        reason_code=SEARCH_UNAVAILABLE,
        confidence=0.0,
    )

    print_assessment(
        "PASS 3  Search unavailable (the claim could not be checked)",
        service.assess(flags, (claim,), (unsearchable,), ()),
    )
    print(
        "\nCompare with PASS 2: the pattern indicators are still counted, and the\n"
        "unverifiable claim contributes nothing. The gap is reported, not scored."
    )


def main() -> int:
    """Run all three passes and return a process exit code."""
    from app.services.risk import weights_from_settings

    service = RiskService()
    print(f"Sample content under investigation:\n  {CONTENT!r}\n")

    weights = weights_from_settings()
    print(f"Red-flag weights in force ({len(weights.red_flag_weights)} rules):")
    for code in RedFlagCode:
        print(f"  {code.value:28} {weights.weight_for(code)}")
    print(
        f"  contradicted_claim         {weights.contradicted_claim}\n"
        f"  unverified_claim           {weights.unverified_claim}\n"
        f"  insufficient_evidence      {weights.insufficient_evidence}"
    )

    pass_nothing_found(service)
    pass_one_signal_four_stages(service)
    pass_could_not_look(service)

    print(
        "\nReminder: the score is a transparent heuristic indicator of documented\n"
        "risk factors. It is not a probability of fraud, of financial loss, or of\n"
        "the investment failing, and it is not a recommendation to invest or not\n"
        "invest."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
