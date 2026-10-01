"""Deterministic entity extraction (Phase 2, §9/§18).

Deliberately **modest**. It recognises high-precision, low-ambiguity entity
forms that can be located exactly — URLs, domains, emails, phone numbers, UPI
ids, registration numbers, IFSC/bank references, social handles, known
regulators, known platforms, financial instruments, and capitalised name
sequences with organisation/adviser suffixes.

It does **not** try to reconstruct an LLM with hundreds of regexes, and it does
not guess: a token that cannot be located with confidence is not emitted. This
extractor is the guaranteed-available half of the extraction stage; the LLM half
handles harder linguistic cases when Groq is available (§18).

Extraction identifies what the text *references*. It makes no statement about
whether any of it is legitimate (D-006).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.logging import get_logger
from app.schemas.entities import (
    REGULATOR_EXPANSIONS,
    Entity,
    EntityType,
    normalize_entity_name,
)
from app.schemas.common import make_span
from app.services.claim_extractor import CLAIM_VOCABULARY
from app.services.text_normalization import NormalizedText

logger = get_logger(__name__)

#: Upper bound on emitted entities, as a runaway guard.
MAX_ENTITIES = 60

#: Confidence per detection strategy. Extraction confidence only.
CONFIDENCE_IDENTIFIER = 0.95
CONFIDENCE_KNOWN_TERM = 0.9
CONFIDENCE_ORGANISATION = 0.7
CONFIDENCE_PERSON = 0.6


@dataclass(frozen=True)
class EntityPattern:
    """A regex-based entity detector.

    Attributes:
        entity_type: Entity family this detector produces.
        pattern: Compiled pattern with a single capturing group when present.
        label: Machine-readable detector name for diagnostics.
        exclude: Optional patterns whose match cancels this detection (for
            example, excluding an email's local part from social handles).
    """

    entity_type: EntityType
    pattern: re.Pattern[str]
    label: str
    exclude: tuple[re.Pattern[str], ...] = ()


@dataclass(frozen=True)
class RawEntity:
    """An entity located in **normalised** coordinates, before span mapping.

    Attributes:
        start: Start offset in the normalised text.
        end: End offset in the normalised text.
        entity_type: Assigned entity family.
        confidence: Extraction confidence.
        metadata: Extra attributes derived from the match itself.
    """

    start: int
    end: int
    entity_type: EntityType
    confidence: float
    metadata: dict[str, str | int | float | bool]


def _compile(source: str) -> re.Pattern[str]:
    """Compile a case-insensitive, unicode-aware pattern."""
    return re.compile(source, re.IGNORECASE | re.UNICODE)


#: Common words that must never be read as a person or company name.
_NAME_STOPWORDS = frozenset(
    {
        "a", "an", "the", "this", "that", "these", "those", "our", "we", "us", "i",
        "my", "your", "you", "their", "they", "he", "she", "it", "its", "his", "her",
        "join", "contact", "call", "pay", "send", "minimum", "invest", "investment",
        "guaranteed", "guarantees", "returns", "return", "profit", "profits", "expert",
        "experts", "team", "teams", "account", "accounts", "bank", "upi", "money",
        "offer", "opportunity", "group", "channel", "private", "limited", "company",
        "pvt", "ltd", "llp", "sebi", "rbi", "nse", "bse", "mca", "telegram",
        "whatsapp", "hello", "welcome", "thanks", "regards", "please", "dear", "sir",
        "madam", "best", "safe", "secure", "official", "guaranteed", "monthly", "daily",
        "yearly", "annual", "start", "today", "now", "broker", "adviser", "advisor",
        "consultant", "capital", "fund", "funds", "market", "share", "shares", "stock",
        "stocks", "profit", "loss", "withdrawal", "withdraw", "activate", "activation",
        "minimum", "investment", "directly", "activate", "payment", "fee", "tax",
        "hi", "hello", "hey", "sir", "madam", "dear", "regards", "sincerely",
        # Words that describe a claim or an accusation, never a named party.
        # Without these, "Verified Fraud" would be read as a person's name.
        "verified", "unverified", "fraud", "fraudulent", "scam", "phishing",
        "ponzi", "scheme", "assured", "trusted", "legit", "genuine", "free",
        "fixed", "risk", "loss", "illegal", "legal", "police", "court",
    }
)

_URL = _compile(
    r"\b(?:https?://|www\.)(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(?::\d{1,5})?(?:/[^\s]*)?"
)
_EMAIL = _compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_UPI_ID = _compile(r"\b[a-z0-9][a-z0-9._-]{1,30}@[a-z][a-z0-9-]{1,30}\b")
_IFSC = _compile(r"\b[a-z]{4}0[a-z0-9]{6}\b")
_PHONE_IN = _compile(
    r"(?<![\d\w])(?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?![\d\w])"
)
_PHONE_INTL = _compile(r"(?<![\d\w])\+\d{1,3}[\s-]?\d{3,4}[\s-]?\d{3,4}[\s-]?\d{0,4}(?![\d\w])")
_REGISTRATION = _compile(
    r"\b(?:SEBI\s*)?(?:R/?A|INH|INA|INH000|SMG|SRI|RESA|L\d{5}|A\d{6})[\s/-]?\d{4,10}\b"
    r"|\bINH0\d{6}\b"
)
_SOCIAL_HANDLE = _compile(r"(?<![\w@.])@[A-Za-z][A-Za-z0-9._]{2,29}\b")
_BANK_WORD = _compile(r"\b(?:bank\s+account|a/?c\s*(?:number|no)|account\s+(?:number|no))\b")

#: Known regulators. Normalisation expands these to their full names.
_REGULATOR = _compile(
    r"\b(?:SEBI|RBI|IRDAI|NSE|BSE|MCA|NFRA|PFRDA|SFIO|NBFC)\b"
)

#: Known platforms and messaging services.
_PLATFORM = _compile(
    r"\b(?:Telegram|WhatsApp|Signal|Discord|Slack|YouTube|Instagram|Facebook|Twitter|X\s+"
    r"TradingView|MetaTrader\s*\d*|MT4|MT5|Binance|Zerodha|Groww|Upstox|Wise|Remitly)\b"
)

#: Financial instruments and asset classes.
_INSTRUMENT = _compile(
    r"\b(?:mutual\s+funds?|SIP|ELSS|PPF|FD|recurring\s+deposit|bonds?|debentures?|"
    r"equit(?:y|ies)|stocks?|shares?|options?|futures?|forex|crypto\w*|bitcoin|"
    r"ethereum|gold|silver|commodit\w+|Nifty|Sensex|demat|demats)\b"
)

#: Organisation/adviser suffixes that make a capitalised sequence a company.
_ORG_SUFFIX = _compile(
    r"\b(?:capital|securities|financials?|fintech|advisers?|advisory|brokers?|broking|"
    r"brokerage|asset\s+management|wealth\s+management|investments?|funds?|"
    r"ventures?|holdings?|group|associates|consultants?|research|technologies|"
    r"solutions|systems|global|international)\b"
)

#: Legal-entity suffixes.
_LEGAL_SUFFIX = _compile(
    r"\b(?:pvt\.?\s*ltd\.?|private\s+limited|ltd\.?|limited|llp|inc\.?)\b"
)

#: Suffixes that specifically indicate an advisory or brokerage business.
_ADVISORY_SUFFIX = _compile(
    r"\b(?:advisers?|advisory|brokers?|broking|brokerage)\b"
)

#: Capitalised sequences of one to four tokens: candidate person/company names.
_NAME_SEQUENCE = _compile(r"\b[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}\b")

#: Titles that indicate a person is being named.
_PERSON_TITLE = re.compile(r"\b(?:mr|mrs|ms|dr|shri|smt)\.?\s+(?=[A-Z])", re.IGNORECASE)


def _host_of(url: str) -> str:
    """Return the host portion of a URL or bare domain."""
    cleaned = re.sub(r"^\w+://", "", url)
    cleaned = cleaned.split("/", 1)[0]
    cleaned = cleaned.split("?", 1)[0]
    return cleaned.split("@")[-1].split(":")[0].lower()


def _entity_patterns() -> tuple[EntityPattern, ...]:
    """Build the identifier-oriented detector list."""
    return (
        EntityPattern(EntityType.EMAIL, _EMAIL, "email"),
        EntityPattern(EntityType.UPI_ID, _UPI_ID, "upi_id"),
        EntityPattern(EntityType.WEBSITE, _URL, "url"),
        EntityPattern(EntityType.REGISTRATION_NUMBER, _REGISTRATION, "registration_number"),
        EntityPattern(EntityType.BANK_ACCOUNT, _IFSC, "ifsc"),
        EntityPattern(EntityType.SOCIAL_HANDLE, _SOCIAL_HANDLE, "social_handle", (_EMAIL,)),
        EntityPattern(EntityType.REGULATOR, _REGULATOR, "regulator"),
        EntityPattern(EntityType.PLATFORM, _PLATFORM, "platform"),
        EntityPattern(EntityType.FINANCIAL_INSTRUMENT, _INSTRUMENT, "instrument"),
        EntityPattern(EntityType.PHONE_NUMBER, _PHONE_INTL, "phone_intl"),
        EntityPattern(EntityType.PHONE_NUMBER, _PHONE_IN, "phone_in"),
    )


ENTITY_PATTERNS: tuple[EntityPattern, ...] = _entity_patterns()


def extract_entities(normalized: NormalizedText) -> list[RawEntity]:
    """Extract entities from normalised text, in source order.

    Args:
        normalized: The normalised input to scan.

    Returns:
        Entities located in normalised coordinates, ready for span mapping.
    """
    text = normalized.text
    if not text.strip():
        return []

    found: list[RawEntity] = []
    claimed: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < other_end and other_start < end for other_start, other_end in claimed)

    # --- 1. identifiers, ordered so specific forms win over generic ones ---
    for pattern in ENTITY_PATTERNS:
        for match in pattern.pattern.finditer(text):
            start, end = match.span()
            if overlaps(start, end):
                continue
            if any(exclude.search(match.group(0)) for exclude in pattern.exclude):
                continue
            value = match.group(0)
            entity_type = pattern.entity_type
            metadata: dict[str, str | int | float | bool] = {}

            if entity_type is EntityType.UPI_ID and _EMAIL.fullmatch(value):
                # A dotted local part with a real TLD is an address, not a UPI id.
                continue
            if entity_type is EntityType.WEBSITE:
                metadata["host"] = _host_of(value)
                metadata["scheme"] = "https" if value.lower().startswith("https") else "http"
            if entity_type is EntityType.SOCIAL_HANDLE:
                metadata["handle"] = value.lstrip("@")
            if entity_type is EntityType.REGULATOR:
                metadata["acronym"] = value.upper()
            if entity_type is EntityType.BANK_ACCOUNT:
                metadata["format"] = "ifsc"

            claimed.append((start, end))
            found.append(
                RawEntity(
                    start=start,
                    end=end,
                    entity_type=entity_type,
                    confidence=CONFIDENCE_IDENTIFIER
                    if pattern.label
                    in {"email", "url", "registration_number", "ifsc", "upi_id", "phone_in", "phone_intl", "social_handle"}
                    else CONFIDENCE_KNOWN_TERM,
                    metadata=metadata,
                )
            )

    # --- 2. bare domains not already covered by a URL ---
    for match in _compile(r"(?<![\w@.])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:com|net|org|io|in|co|xyz|top|site|online|app|dev|info|biz)\b").finditer(text):
        start, end = match.span()
        if overlaps(start, end):
            continue
        claimed.append((start, end))
        found.append(
            RawEntity(start, end, EntityType.DOMAIN, CONFIDENCE_IDENTIFIER, {"host": match.group(0).lower()})
        )

    # --- 3. bank account references (word, not number — never guess digits) ---
    for match in _BANK_WORD.finditer(text):
        start, end = match.span()
        if not overlaps(start, end):
            claimed.append((start, end))
            found.append(RawEntity(start, end, EntityType.BANK_ACCOUNT, CONFIDENCE_KNOWN_TERM, {"kind": "reference"}))

    # --- 4. capitalised name sequences ---
    for match in _NAME_SEQUENCE.finditer(text):
        start, end = match.span()
        if overlaps(start, end):
            continue
        value = match.group(0)
        tokens = value.split()
        if any(token.lower() in _NAME_STOPWORDS for token in tokens):
            continue
        # A capitalised phrase made entirely of claim vocabulary is claim
        # language, not a name: "Verified Fraud" must never become a person.
        if tokens and all(token.lower() in CLAIM_VOCABULARY for token in tokens):
            continue
        if len(tokens) == 1 and len(value) <= 2:
            continue

        preceding = text[max(0, start - 12) : start]
        if _PERSON_TITLE.fullmatch(preceding.strip() + " ") or _PERSON_TITLE.match(preceding):
            found.append(RawEntity(start, end, EntityType.PERSON, CONFIDENCE_PERSON, {}))
            claimed.append((start, end))
            continue

        has_org_suffix = bool(_ORG_SUFFIX.search(value))
        has_legal_suffix = bool(_LEGAL_SUFFIX.search(value))
        if has_org_suffix or has_legal_suffix:
            entity_type = (
                EntityType.INVESTMENT_ADVISER if _ADVISORY_SUFFIX.search(value) else EntityType.COMPANY
            )
            found.append(RawEntity(start, end, entity_type, CONFIDENCE_ORGANISATION, {}))
            claimed.append((start, end))
        elif len(tokens) >= 2:
            found.append(RawEntity(start, end, EntityType.PERSON, CONFIDENCE_PERSON, {}))
            claimed.append((start, end))

    found.sort(key=lambda item: (item.start, item.end))
    logger.debug(
        "Deterministic entity extraction complete",
        extra={"entities": len(found), "normalized_chars": len(text)},
    )
    return found[:MAX_ENTITIES]


def build_entity(
    raw: RawEntity,
    normalized: NormalizedText,
    entity_id: str,
) -> Entity:
    """Convert a raw entity into a schema entity with original-text spans.

    Args:
        raw: Entity in normalised coordinates.
        normalized: The normalised text it came from.
        entity_id: Deterministic id to assign.

    Returns:
        An :class:`~app.schemas.entities.Entity` whose span indexes `original`.
    """
    original_start, original_end = normalized.to_original(raw.start, raw.end)
    name = normalized.original[original_start:original_end]
    span = make_span(normalized.original, original_start, original_end)
    normalized_name = normalize_entity_name(name)
    if not normalized_name:
        normalized_name = normalize_entity_name(normalized.text[raw.start : raw.end])
    return Entity(
        id=entity_id,
        name=name,
        entity_type=raw.entity_type,
        normalized_name=normalized_name,
        confidence=raw.confidence,
        evidence_span=span,
        metadata=dict(raw.metadata),
    )


def regulator_expansions() -> dict[str, str]:
    """Return the regulator acronym map used for normalisation."""
    return dict(REGULATOR_EXPANSIONS)


__all__ = [
    "CONFIDENCE_IDENTIFIER",
    "CONFIDENCE_KNOWN_TERM",
    "CONFIDENCE_ORGANISATION",
    "CONFIDENCE_PERSON",
    "ENTITY_PATTERNS",
    "MAX_ENTITIES",
    "EntityPattern",
    "RawEntity",
    "build_entity",
    "extract_entities",
    "regulator_expansions",
]
