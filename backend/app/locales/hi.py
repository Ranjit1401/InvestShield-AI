"""Hindi report presentation resources (Phase 15).

Only the presentation layer is localized: section titles, the
human-readable form of every controlled vocabulary value, and the
fixed report prose. No score, verdict, claim, entity, red-flag code,
verification status, evidence URL or risk level is translated here —
those are canonical facts that stay identical in every language.

The summary template carries ``{placeholders}`` the localization
service fills with canonical, language-neutral numbers; only the
wording around them is translated.
"""

from __future__ import annotations

SECTIONS: dict[str, str] = {
    "risk_assessment": "जोखिम आकलन",
    "summary": "सारांश",
    "claims": "दावे",
    "entities": "संस्थाएँ",
    "red_flags": "लाल झंडे",
    "verification": "सत्यापन",
    "evidence": "साक्ष्य",
    "why_flagged": "यह क्यों चिह्नित किया गया",
    "timeline": "जांच समयरेखा",
    "safety_guidance": "सुरक्षा मार्गदर्शन",
    "limitations": "सीमाएँ",
    "sources": "स्रोत",
    "disclaimer": "अस्वीकरण",
}

LABELS: dict[str, dict[str, str]] = {
    "risk_level": {
        "LOW": "निम्न",
        "MEDIUM": "मध्यम",
        "HIGH": "उच्च",
        "CRITICAL": "गंभीर",
    },
    "severity": {
        "LOW": "निम्न",
        "MEDIUM": "मध्यम",
        "HIGH": "उच्च",
        "CRITICAL": "गंभीर",
    },
    "verification_status": {
        "VERIFIED": "सत्यापित",
        "UNVERIFIED": "असत्यापित",
        "CONTRADICTED": "विरोधाभासी",
        "INSUFFICIENT_EVIDENCE": "अपर्याप्त साक्ष्य",
        "NOT_APPLICABLE": "लागू नहीं",
    },
    "evidence_relation": {
        "SUPPORTS": "समर्थन करता है",
        "CONTRADICTS": "विरोध करता है",
        "IDENTITY_REFERENCE": "पहचान संदर्भ",
        "CONTEXT": "संदर्भ",
        "MENTIONS": "उल्लेख",
    },
    "evidence_relevance": {
        "HIGH": "उच्च",
        "MEDIUM": "मध्यम",
        "LOW": "निम्न",
    },
    "timeline_status": {
        "STARTED": "प्रारंभ",
        "COMPLETED": "पूर्ण",
        "PARTIAL": "आंशिक",
        "FAILED": "विफल",
        "SKIPPED": "छोड़ा गया",
    },
    "factor_origin": {
        "RED_FLAG": "लाल झंडा",
        "VERIFICATION": "सत्यापन",
    },
    "investigation_status": {
        "COMPLETED": "पूर्ण",
        "PARTIAL": "आंशिक",
        "FAILED": "विफल",
    },
    "input_type": {
        "TEXT": "पाठ",
        "URL": "वेबसाइट",
        "IMAGE": "स्क्रीनशॉट",
        "PDF": "पीडीएफ",
    },
}

FIXED_TEXT: dict[str, str] = {
    "summary_with_risk": (
        "जांच पूर्ण। जोखिम स्तर {risk_level} (स्कोर {risk_score})। "
        "{red_flag_count} लाल झंडे, {claim_count} दावे, "
        "{verified_count} सत्यापित।"
    ),
    "summary_without_risk": (
        "जांच पूर्ण नहीं हुई। रुकने से पहले {red_flag_count} लाल झंडे, "
        "{claim_count} दावे दर्ज किए गए।"
    ),
    "safety_guidance": (
        "यह रिपोर्ट दर्ज करती है कि क्या मिला और क्या सत्यापित "
        "हो सका। यह निवेश सलाह नहीं है। किसी भी पंजीकरण, रिटर्न "
        "वादे या अधिकारी को कार्रवाई से पहले स्वतंत्र रूप से "
        "सत्यापित करें।"
    ),
    "risk_caveat": (
        "जोखिम स्कोर दस्तावेज़ीकृत जोखिम कारकों का एक पारदर्शी "
        "ह्युरिस्टिक संकेतक है। यह धोखाधड़ी, वित्तीय हानि या निवेश "
        "के विफल होने की प्रायिकता नहीं है, और यह निवेश करने या "
        "न करने की सिफारिश नहीं है।"
    ),
    "disclaimer": (
        "इन्वेस्टशील्ड एआई एक जांच उपकरण है। यह दर्ज करता है कि "
        "क्या मिला और क्या सत्यापित हो सका। यह वित्तीय, कानूनी या "
        "निवेश सलाह प्रदान नहीं करता, और यह निर्धारित नहीं करता "
        "कि कोई निवेश सुरक्षित, धोखाधड़ी या उपयुक्त है।"
    ),
}

RESOURCES: dict[str, dict] = {
    "sections": SECTIONS,
    "labels": LABELS,
    "fixed_text": FIXED_TEXT,
}
