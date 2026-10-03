"""Marathi report presentation resources (Phase 15).

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
    "entities": "संस्था",
    "red_flags": "लाल धजे",
    "verification": "सत्यापन",
    "evidence": "पुरावे",
    "why_flagged": "हे का चिन्हांकित केले",
    "timeline": "तपासण कालानुक्रम",
    "safety_guidance": "सुरक्षा मार्गदर्शन",
    "limitations": "मर्यादा",
    "sources": "स्रोत",
    "disclaimer": "अस्वीकरण",
}

LABELS: dict[str, dict[str, str]] = {
    "risk_level": {
        "LOW": "कमी",
        "MEDIUM": "मध्यम",
        "HIGH": "उच्च",
        "CRITICAL": "गंभीर",
    },
    "severity": {
        "LOW": "कमी",
        "MEDIUM": "मध्यम",
        "HIGH": "उच्च",
        "CRITICAL": "गंभीर",
    },
    "verification_status": {
        "VERIFIED": "सत्यापित",
        "UNVERIFIED": "असत्यापित",
        "CONTRADICTED": "विरोधाभासी",
        "INSUFFICIENT_EVIDENCE": "अपुरा पुरावा",
        "NOT_APPLICABLE": "लागू नाही",
    },
    "evidence_relation": {
        "SUPPORTS": "समर्थन देते",
        "CONTRADICTS": "विरोध करते",
        "IDENTITY_REFERENCE": "ओळख संदर्भ",
        "CONTEXT": "संदर्भ",
        "MENTIONS": "उल्लेख",
    },
    "evidence_relevance": {
        "HIGH": "उच्च",
        "MEDIUM": "मध्यम",
        "LOW": "कमी",
    },
    "timeline_status": {
        "STARTED": "सुरुवात",
        "COMPLETED": "पूर्ण",
        "PARTIAL": "आंशिक",
        "FAILED": "अयशस्वी",
        "SKIPPED": "वगळले",
    },
    "factor_origin": {
        "RED_FLAG": "लाल धजा",
        "VERIFICATION": "सत्यापन",
    },
    "investigation_status": {
        "COMPLETED": "पूर्ण",
        "PARTIAL": "आंशिक",
        "FAILED": "अयशस्वी",
    },
    "input_type": {
        "TEXT": "मजकूर",
        "URL": "संकेतस्थळ",
        "IMAGE": "स्क्रीनशॉट",
        "PDF": "पीडीएफ",
    },
}

FIXED_TEXT: dict[str, str] = {
    "summary_with_risk": (
        "तपासण पूर्ण. जोखिम स्तर {risk_level} (स्कोर {risk_score}). "
        "{red_flag_count} लाल धजा, {claim_count} दावे, "
        "{verified_count} सत्यापित."
    ),
    "summary_without_risk": (
        "तपासण पूर्ण झाले नाही. थांबण्यापूर्वी {red_flag_count} "
        "लाल धजा, {claim_count} दावे नोंदवले गेले."
    ),
    "safety_guidance": (
        "हा अहवाल नोंदवतो की काय सापडले आणि काय "
        "सत्यापित झाले. हे निवेश सल्ला नाही. कोणत्याही "
        "नोंदणी, रिटर्न वचन किंवा अधिकाऱ्यांची कारवाईपूर्वी "
        "स्वतंत्रपणे सत्यापित करा."
    ),
    "risk_caveat": (
        "जोखिम गुणांक दस्तऐवेजीकृत जोखिम घटकांचा एक "
        "पारदर्शक ह्युरिस्टिक सूचक आहे. हे धोखाधडी, आर्थिक "
        "नुकसान किंवा निवेश अयशस्वी होण्याची संभाव्यता नाही, "
        "आणि हे निवेश करायचा किंवा करू नयेची सूचवा नाही."
    ),
    "disclaimer": (
        "इन्व्हेस्टशील्ड एआई हे एक तपासण साधन आहे. हे नोंदवते "
        "की काय सापडले आणि काय सत्यापित झाले. हे वित्तीय, "
        "कायदेशीर किंवा निवेश सल्ला देत नाही, आणि हे ठरवत "
        "नाही की कोणताही निवेश सुरक्षित, धोखाधडी किंवा योग्य आहे."
    ),
}

RESOURCES: dict[str, dict] = {
    "sections": SECTIONS,
    "labels": LABELS,
    "fixed_text": FIXED_TEXT,
}
