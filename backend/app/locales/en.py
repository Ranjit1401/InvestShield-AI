"""English report presentation resources (Phase 15).

The investigation itself is language-independent. Every fact the
pipeline produces — claims, entities, red flags, verification
verdicts, evidence and the risk score — stays in the language the
pipeline produced it in. What this module supplies is the *presentation*
layer alone: the report's section titles, the human-readable form of
every controlled vocabulary value the report renders, and the fixed
report prose. Nothing here carries a score, a verdict or a fact.

The three parts are kept separate so a translator can find exactly
what to translate and a test can assert exactly what must never
change:

- ``sections`` — the report's section titles, keyed by a stable key.
- ``labels`` — one map per controlled vocabulary, keyed by the
  vocabulary's own value, so a client looks a label up by the code it
  already branches on rather than by English prose.
- ``fixed_text`` — the summary template and the fixed report prose.
  The summary template carries ``{placeholders}`` the localization
  service fills with canonical, language-neutral numbers; the wording
  around them is the only thing translated.
"""

from __future__ import annotations

SECTIONS: dict[str, str] = {
    "risk_assessment": "Risk Assessment",
    "summary": "Summary",
    "claims": "Claims",
    "entities": "Entities",
    "red_flags": "Red Flags",
    "verification": "Verification",
    "evidence": "Evidence",
    "why_flagged": "Why This Was Flagged",
    "timeline": "Investigation Timeline",
    "safety_guidance": "Safety Guidance",
    "limitations": "Limitations",
    "sources": "Sources",
    "disclaimer": "Disclaimer",
}

LABELS: dict[str, dict[str, str]] = {
    "risk_level": {
        "LOW": "Low",
        "MEDIUM": "Medium",
        "HIGH": "High",
        "CRITICAL": "Critical",
    },
    "severity": {
        "LOW": "Low",
        "MEDIUM": "Medium",
        "HIGH": "High",
        "CRITICAL": "Critical",
    },
    "verification_status": {
        "VERIFIED": "Verified",
        "UNVERIFIED": "Unverified",
        "CONTRADICTED": "Contradicted",
        "INSUFFICIENT_EVIDENCE": "Insufficient Evidence",
        "NOT_APPLICABLE": "Not Applicable",
    },
    "evidence_relation": {
        "SUPPORTS": "Supports",
        "CONTRADICTS": "Contradicts",
        "IDENTITY_REFERENCE": "Identity Reference",
        "CONTEXT": "Context",
        "MENTIONS": "Mentions",
    },
    "evidence_relevance": {
        "HIGH": "High",
        "MEDIUM": "Medium",
        "LOW": "Low",
    },
    "timeline_status": {
        "STARTED": "Started",
        "COMPLETED": "Completed",
        "PARTIAL": "Partial",
        "FAILED": "Failed",
        "SKIPPED": "Skipped",
    },
    "factor_origin": {
        "RED_FLAG": "Red Flag",
        "VERIFICATION": "Verification",
    },
    "investigation_status": {
        "COMPLETED": "Completed",
        "PARTIAL": "Partial",
        "FAILED": "Failed",
    },
    "input_type": {
        "TEXT": "Text",
        "URL": "Website",
        "IMAGE": "Screenshot",
        "PDF": "PDF",
    },
}

FIXED_TEXT: dict[str, str] = {
    "summary_with_risk": (
        "Investigation complete. Risk level {risk_level} "
        "(score {risk_score}). {red_flag_count} red flag(s), "
        "{claim_count} claim(s), {verified_count} verified."
    ),
    "summary_without_risk": (
        "Investigation did not complete. {red_flag_count} red flag(s), "
        "{claim_count} claim(s) recorded before the run stopped."
    ),
    "safety_guidance": (
        "This report records what was found and what could be verified. "
        "It is not investment advice. Verify any registration, return "
        "promise or authority independently before acting."
    ),
    "risk_caveat": (
        "The risk score is a transparent heuristic indicator of documented "
        "risk factors. It is not a probability of fraud, of financial "
        "loss, or of the investment failing, and it is not a "
        "recommendation to invest or not invest."
    ),
    "disclaimer": (
        "InvestShield AI is an investigation tool. It records what was "
        "found and what could be verified. It does not provide financial, "
        "legal or investment advice, and it does not determine whether "
        "any investment is safe, fraudulent or suitable."
    ),
}

RESOURCES: dict[str, dict] = {
    "sections": SECTIONS,
    "labels": LABELS,
    "fixed_text": FIXED_TEXT,
}
