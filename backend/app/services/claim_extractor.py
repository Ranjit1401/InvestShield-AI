"""Deterministic claim extraction (Phase 2, §5/§6).

This is the **baseline** extractor. It runs with no API key and its output is
what the pipeline falls back to when Groq is unavailable or fails.

Approach:

1. Split the normalised text into sentences, then into clauses (conjunctions
   and separators), so one long message never becomes one claim.
2. Drop conversational filler, which is not investigable (§6).
3. Detect typed claim signals in each clause using a small catalogue.
4. Emit one claim per clause, typed by signal precedence. When a clause makes
   both a *promise* and a *credential/regulatory* claim, emit the promise claim
   for the whole clause plus a separate fragment claim for the credential part,
   so both are independently verifiable in Phase 4.
5. If a clause has no signal but is still assertive (it carries a number or a
   finite predicate), emit an `OTHER` claim rather than dropping information.

No free-form text is produced and nothing is inferred beyond the input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.logging import get_logger
from app.schemas.claims import (
    PROMISE_CLAIM_TYPES,
    VERIFIABLE_CLAIM_TYPES,
    ClaimType,
)
from app.services.red_flag_rules import SENTENCE_BOUNDARY
from app.services.text_normalization import NormalizedText

logger = get_logger(__name__)

#: Maximum claims returned for one input, as a runaway guard.
MAX_CLAIMS = 40


@dataclass(frozen=True)
class ClaimSignal:
    """A typed cue that a clause makes a claim of a particular family.

    Attributes:
        claim_type: The claim family this cue indicates.
        pattern: Compiled case-insensitive pattern over normalised text.
        label: Short machine-readable cue name, used for diagnostics.
    """

    claim_type: ClaimType
    pattern: re.Pattern[str]
    label: str


@dataclass(frozen=True)
class RawClaim:
    """A claim located in **normalised** coordinates, before span mapping.

    Attributes:
        start: Start offset in the normalised text.
        end: End offset in the normalised text.
        claim_type: Assigned claim family.
        confidence: Extraction confidence, not a truth assessment.
        signals: All claim families observed in the same clause.
        is_complete_sentence: False when this claim is a clause or fragment.
    """

    start: int
    end: int
    claim_type: ClaimType
    confidence: float
    signals: tuple[ClaimType, ...]
    is_complete_sentence: bool


def _compile(source: str) -> re.Pattern[str]:
    """Compile a case-insensitive, unicode-aware pattern."""
    return re.compile(source, re.IGNORECASE | re.UNICODE)


#: Clause boundaries. Single newlines are soft (Phase 1 already established
#: that marketing posts are line-broken).
_CLAUSE_SPLIT = re.compile(
    r"\s*(?:[;•·]|\.\.\.|,\s*(?:and|but)\s+)\s*"
    r"|\s+(?:and|but|so|then|while|whereas)\s+",
    re.IGNORECASE,
)

#: Conversational filler. Not claims: nothing here can be verified (§6).
_FILLER = (
    _compile(r"^(?:hello|hi|hey|namaste|dear\s+\w+|good\s+(?:morning|afternoon|evening))\b[^.!?]*[.!?]?$"),
    _compile(r"^welcome\s+to\b"),
    _compile(r"^(?:contact|reach)\s+us\b[^.!?]*(?:details|more|enquiry|enquiry|info)"),
    _compile(r"^(?:kindly\s+)?(?:note|contact|dm|message)\s+(?:us|me)\b[^.!?]*[.!?]?$"),
    _compile(r"^(?:thank\s+you|thanks|regards|best\s+regards|sincerely)\b"),
    _compile(r"^(?:please\s+)?(?:join|add)\s+(?:us|me)\b[^.!?]*[.!?]?$"),
)

#: Predicate words that make a clause assertive even without a typed signal.
_FINITE_PREDICATE = _compile(
    r"\b(?:is|are|was|were|has|have|will|shall|can|provides?|offers?|manages?|"
    r"gives?|delivers?|allows?|requires?|charges?|includes?|runs?|operates?|"
    r"guarantees?|generates?|earn|earns?|holds?|costs?|starts?|ends?|opens?|closes?)\b"
)

#: Numbers, percentages or money make a clause worth investigating.
_NUMERIC_MARKER = re.compile(r"\d|%|₹|\$|€|£", re.UNICODE)

#: Strong claim cues. Order within this tuple does not matter; precedence comes
#: from :data:`CLAIM_TYPE_PRECEDENCE`.
_CLAIM_SIGNAL_SOURCES: tuple[tuple[ClaimType, str, str], ...] = (
    # --- promises ---
    (ClaimType.GUARANTEE_CLAIM, r"\b(?:guarantee[sd]?|guaranteeing|assure[sd]?|assured)\b", "guarantee"),
    (ClaimType.GUARANTEE_CLAIM, r"\bno[\s\-]?loss\b|\brisk[\s\-]?free\b|\b100%\s*(?:safe|profit|assured)\b", "no_loss_or_risk_free"),
    (ClaimType.GUARANTEE_CLAIM, r"गारंटी|ज़िम्मेदार|पक्का", "guarantee_devanagari"),
    (ClaimType.RETURN_PROMISE, r"\b\d+(?:\.\d+)?\s*%[^.\n]{0,30}?\b(?:return|returns|yield|roi)\b", "percent_return"),
    (ClaimType.RETURN_PROMISE, r"\b(?:return|returns|yield|roi)\b[^.\n]{0,30}?\b\d+(?:\.\d+)?\s*%", "return_percent"),
    (ClaimType.RETURN_PROMISE, r"\b(?:monthly|daily|weekly|annual|annually|per\s+annum)\b[^.\n]{0,30}?\b(?:return|returns|profit|yield|roi|payout|income)\b", "periodic_return"),
    (ClaimType.RETURN_PROMISE, r"\b(?:return|returns)\b[^.\n]{0,20}?\b(?:of|at)\s*\d", "numeric_return"),
    (ClaimType.PROFIT_PROMISE, r"\b(?:profit|profits|earnings|winnings|income|payout|payouts|मुनाफा|लाभ)\b", "profit_word"),
    (ClaimType.PROFIT_PROMISE, r"\bearn|earns|earning\b", "earn_verb"),
    (ClaimType.GUARANTEE_CLAIM, r"\b(?:guaranteed|assured|promised)\b[^.\n]{0,25}?\b(?:profit|income|return|payout|gain)", "promised_outcome"),
    # --- regulatory / credential ---
    (ClaimType.REGULATORY_STATUS, r"\b(?:sebi|rbi|irdai|nse|bse|mca|nfra|pfrda|sfio)\b[\s\-_]*(?:approved|registered|authori[sz]ed|certified|regulated|licensed|guaranteed)", "regulator_status"),
    (ClaimType.REGULATORY_STATUS, r"\b(?:approved|registered|authori[sz]ed|licensed|certified|regulated|guaranteed)\s+(?:by|with|from|under)\s+(?:the\s+)?(?:sebi|rbi|irdai|nse|bse|mca)\b", "status_by_regulator"),
    (ClaimType.REGULATORY_STATUS, r"\b(?:government|govt\.?|central\s+government|state\s+government)\b[\s\-_]*(?:approved|registered|authori[sz]ed|licensed|sanctioned|backed)\b", "government_approval"),
    (ClaimType.REGULATORY_STATUS, r"\b(?:fully|100%|completely)\s+(?:approved|registered|regulated|licensed|sebi)\b", "blanket_approval"),
    (ClaimType.CREDENTIAL_CLAIM, r"\b(?:certified|licensed|qualified|registered|professional|accredited|verified)\s+(?:\w+\s+){0,2}(?:adviser|advisor|consultant|planner|analyst|expert|manager|broker)\b", "professional_credential"),
    (ClaimType.CREDENTIAL_CLAIM, r"\b(?:investment|financial|trading|wealth|research)\s+(?:adviser|advisor|analyst|expert|consultant)\b", "named_role"),
    (ClaimType.CREDENTIAL_CLAIM, r"\b(?:sebi|rbi)\b[\s\-_]*(?:expert|experts|adviser|advisor|consultant|analysts)\b", "regulator_expert"),
    (ClaimType.CREDENTIAL_CLAIM, r"\b(?:chartered\s+accountant|ca\b|csm|fpia|cfp|chartered\s+financial\s+analyst)\b", "professional_designation"),
    # --- identity / ownership ---
    (ClaimType.OWNERSHIP_CLAIM, r"\b(?:we|our|they|their)\b[^.\n]{0,25}?\b(?:owns?|owned|subsidiary|proprietary|parent\s+company)\b", "ownership"),
    (ClaimType.AFFILIATION_CLAIM, r"\b(?:our|our\s+in[\s\-]?house|house)\s+(?:expert|experts|advisory|team|teams|partners?|analysts?)\b", "our_team"),
    (ClaimType.AFFILIATION_CLAIM, r"\bin\s+(?:association|partnership|collaboration)\s+with\b|\bpartnered\s+with\b|\bassociated\s+with\b", "partnership"),
    (ClaimType.AFFILIATION_CLAIM, r"\b(?:i|we)\s*(?:'m|am|are)\s+(?:an?\s+)?(?:\w+\s+){0,2}(?:adviser|advisor|agent|representative|analyst|consultant)\b", "self_designation"),
    (ClaimType.COMPANY_CLAIM, r"\b(?:we|our|they)\s+(?:are|is|was)\s+(?:a|an|the)\s+(?:\w+\s+){0,3}(?:company|firm|enterprise|startup|platform|group)\b", "company_identity"),
    (ClaimType.COMPANY_CLAIM, r"\b(?:established|founded|incorporated|registered\s+company)\b|\bpvt\.?\s*ltd\.?\b|\blimited\b|\bllp\b|\binc\.?\b", "company_registration"),
    (ClaimType.COMPANY_CLAIM, r"\b(?:years|decades)\s+of\s+(?:experience|expertise)\b", "company_track_record"),
    # --- product ---
    (ClaimType.PRODUCT_CLAIM, r"\b(?:our|the)\s+(?:trading\s+|investment\s+|ai\s+)?(?:app|application|platform|software|robot|bot|tool|system|service|terminal|brokerage)\b", "product_reference"),
    (ClaimType.PRODUCT_CLAIM, r"\b(?:features?|provides?|offers?|includes?|supports?)\b[^.\n]{0,40}?\b(?:trading|signals|portfolio|analysis|automation)\b", "product_feature"),
    # --- payment / withdrawal ---
    (ClaimType.PAYMENT_INSTRUCTION, r"\b(?:pay|payment|payments|send|transfer|remit|wire|deposit|contribute)\b[^.\n]{0,40}?\b(?:account|upi|bank|wallet|usdt|btc|crypto|amount|funds?|money|₹|\d)\b", "payment_instruction"),
    (ClaimType.PAYMENT_INSTRUCTION, r"\bupi\s*(?:id|number)?\b|\bifsc\b|\bneft\b|\bimps\b|\brtgs\b|\bqr\s*code\b", "payment_identifier"),
    (ClaimType.PAYMENT_INSTRUCTION, r"\b(?:activat|unlock|register|open)\w*\b[^.\n]{0,30}?\b(?:account|trading|platform)\b", "activation_instruction"),
    (ClaimType.WITHDRAWAL_CLAIM, r"\bwithdraw\w*\b[^.\n]{0,40}?\b(?:within|in \d+|fee|charge|tax|profit|funds?|balance|24|48)\b", "withdrawal_terms"),
    (ClaimType.WITHDRAWAL_CLAIM, r"\bunlock\s+(?:your|the)\b|\brelease\s+(?:your|the)\s+(?:funds?|profits?|money)\b", "unlock_funds"),
    (ClaimType.WITHDRAWAL_CLAIM, r"\bwithdrawal\b[^.\n]{0,40}?\b(?:fee|charge|tax|processing)\b", "withdrawal_fee"),
    # --- historical performance ---
    (ClaimType.PERFORMANCE_CLAIM, r"\b(?:last\s+(?:year|month|quarter)|in\s+(?:19|20)\d{2}|since\s+inception|historically|previously|reported|recorded|delivered|achieved)\b", "historical_performance"),
    (ClaimType.PERFORMANCE_CLAIM, r"\b\d[\d,]*\+?\s*(?:users|customers|clients|subscribers|downloads)\b|\b(?:aum|assets\s+under\s+management)\b", "scale_claim"),
    (ClaimType.PERFORMANCE_CLAIM, r"\b(?:winner|award|ranked|top\s+\d+|best\s+(?:adviser|broker|platform))\b", "award_claim"),
    # --- opportunity / call to action ---
    (ClaimType.INVESTMENT_OPPORTUNITY, r"\b(?:invest|investment|investing|scheme|opportunity|portfolio|stake|subscription)\b", "invest_verb"),
    (ClaimType.INVESTMENT_OPPORTUNITY, r"\b(?:join|register|signup|sign\s*up|subscribe|enrol|enroll|apply)\b", "join_verb"),
    (ClaimType.INVESTMENT_OPPORTUNITY, r"\bminimum\s+(?:investment|amount|deposit|entry)\b|\bentry\s+(?:fee|amount)\b", "entry_terms"),
    (ClaimType.INVESTMENT_OPPORTUNITY, r"\b(?:private|vip|signal|trading|investment)\s+(?:group|channel|community)\b", "messaging_group"),
)

CLAIM_SIGNALS: tuple[ClaimSignal, ...] = tuple(
    ClaimSignal(claim_type=claim_type, pattern=_compile(source), label=label)
    for claim_type, source, label in _CLAIM_SIGNAL_SOURCES
)

_LITERAL_WORD = re.compile(r"[A-Za-z]{4,}")

#: Every literal word that appears in the signal patterns, e.g. "guarantee",
#: "verified", "profit". Entity extraction uses this to tell claim language apart
#: from names: a capitalised phrase built entirely from these words is a claim,
#: not a person or company. Deriving it from the patterns keeps the two stages
#: in step without a second hand-maintained word list.
CLAIM_VOCABULARY: frozenset[str] = frozenset(
    word.lower()
    for _, source, _ in _CLAIM_SIGNAL_SOURCES
    for word in _LITERAL_WORD.findall(source)
)

#: Weak cues used only when a clause has no strong signal but is still assertive.
_HINT_SOURCES: tuple[tuple[ClaimType, str], ...] = (
    (ClaimType.INVESTMENT_OPPORTUNITY, r"\bminimum\s+(?:investment|amount|deposit|entry)\b|\b₹\s?\d"),
    (ClaimType.PAYMENT_INSTRUCTION, r"\b(?:pay|payment|activate|activation|unlock)\w*\b"),
    (ClaimType.WITHDRAWAL_CLAIM, r"\bwithdraw\w*\b|\bunlock\b"),
    (ClaimType.PRODUCT_CLAIM, r"\b(?:app|platform|robot|bot|trading)\b"),
    (ClaimType.INVESTMENT_OPPORTUNITY, r"\b(?:group|channel|join|invest\w*)\b"),
)

_HINTS: tuple[tuple[ClaimType, re.Pattern[str]], ...] = tuple(
    (claim_type, _compile(source)) for claim_type, source in _HINT_SOURCES
)

#: Type precedence when a clause triggers several signals. Lower wins.
CLAIM_TYPE_PRECEDENCE: dict[ClaimType, int] = {
    ClaimType.GUARANTEE_CLAIM: 10,
    ClaimType.RETURN_PROMISE: 11,
    ClaimType.PROFIT_PROMISE: 12,
    ClaimType.REGULATORY_STATUS: 20,
    ClaimType.CREDENTIAL_CLAIM: 21,
    ClaimType.AFFILIATION_CLAIM: 22,
    ClaimType.OWNERSHIP_CLAIM: 23,
    ClaimType.COMPANY_CLAIM: 24,
    ClaimType.WITHDRAWAL_CLAIM: 30,
    ClaimType.PAYMENT_INSTRUCTION: 31,
    ClaimType.PERFORMANCE_CLAIM: 40,
    ClaimType.PRODUCT_CLAIM: 41,
    ClaimType.INVESTMENT_OPPORTUNITY: 50,
    ClaimType.OTHER: 99,
}

#: Extraction-confidence bands. These describe extractor certainty only.
CONFIDENCE_SIGNALLED = 0.85
CONFIDENCE_FRAGMENT = 0.7
CONFIDENCE_HINT_ONLY = 0.6


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Split text into sentence spans, using the Phase 1 boundary definition.

    Args:
        text: Normalised text.

    Returns:
        ``(start, end)`` offsets, trimmed of surrounding whitespace.
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in SENTENCE_BOUNDARY.finditer(text):
        _append_trimmed(spans, text, cursor, match.start())
        cursor = match.end()
    _append_trimmed(spans, text, cursor, len(text))
    return spans


def clause_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Split a sentence into clause spans.

    Args:
        text: Normalised text.
        start: Sentence start offset.
        end: Sentence end offset.

    Returns:
        Clause ``(start, end)`` offsets, trimmed of surrounding whitespace.
    """
    sentence = text[start:end]
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in _CLAUSE_SPLIT.finditer(sentence):
        _append_trimmed(spans, sentence, cursor, match.start(), base=start)
        cursor = match.end()
    _append_trimmed(spans, sentence, cursor, len(sentence), base=start)
    return spans


def _append_trimmed(
    spans: list[tuple[int, int]],
    text: str,
    start: int,
    end: int,
    base: int = 0,
) -> None:
    """Append a whitespace-trimmed span, ignoring empty or punctuation-only text."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    while start < end and text[start] in ".,;:!?-–—•":
        start += 1
    while end > start and text[end - 1] in ".,;:!?-–—•":
        end -= 1
    if start < end and any(char.isalnum() for char in text[start:end]):
        spans.append((base + start, base + end))


def is_filler(clause: str) -> bool:
    """Return True when a clause is conversational filler rather than a claim.

    Args:
        clause: The clause text.

    Returns:
        Whether the clause should be discarded.
    """
    stripped = clause.strip()
    if not stripped:
        return True
    if any(pattern.match(stripped) for pattern in _FILLER):
        return True
    # Very short, verb-free greetings such as emoji-only lines.
    if len(stripped) <= 3 and not _NUMERIC_MARKER.search(stripped):
        return True
    return False


def is_assertive(clause: str) -> bool:
    """Return True when a clause states something rather than greeting.

    Args:
        clause: The clause text.

    Returns:
        Whether the clause carries a predicate, a number, or money.
    """
    return bool(_FINITE_PREDICATE.search(clause) or _NUMERIC_MARKER.search(clause))


def detect_signals(clause: str) -> tuple[ClaimType, ...]:
    """Return the claim families cued by a clause, in precedence order.

    Args:
        clause: The clause text.

    Returns:
        Distinct claim types, most significant first.
    """
    found = {signal.claim_type for signal in CLAIM_SIGNALS if signal.pattern.search(clause)}
    return tuple(sorted(found, key=lambda item: CLAIM_TYPE_PRECEDENCE[item]))


def _pick_type(clause: str, signals: tuple[ClaimType, ...]) -> ClaimType:
    """Return the claim type for a clause, falling back to weak hints."""
    if signals:
        return signals[0]
    for claim_type, pattern in _HINTS:
        if pattern.search(clause):
            return claim_type
    return ClaimType.OTHER


def extract_claims(normalized: NormalizedText) -> list[RawClaim]:
    """Extract claims from normalised text, in source order.

    Args:
        normalized: The normalised input to scan.

    Returns:
        Claims located in normalised coordinates, ready for span mapping.
    """
    text = normalized.text
    if not text.strip():
        return []

    claims: list[RawClaim] = []
    seen: set[tuple[int, int, ClaimType]] = set()

    def emit(start: int, end: int, claim_type: ClaimType, confidence: float, signals: tuple[ClaimType, ...], complete: bool) -> None:
        if len(claims) >= MAX_CLAIMS:
            return
        if (start, end, claim_type) in seen:
            return
        seen.add((start, end, claim_type))
        claims.append(RawClaim(start, end, claim_type, confidence, signals, complete))

    for sentence_start, sentence_end in sentence_spans(text):
        sentence = text[sentence_start:sentence_end]
        if is_filler(sentence):
            continue

        for clause_start, clause_end in clause_spans(text, sentence_start, sentence_end):
            clause = text[clause_start:clause_end]
            if is_filler(clause):
                continue

            signals = detect_signals(clause)
            promise_signals = tuple(item for item in signals if item in PROMISE_CLAIM_TYPES)
            verification_signals = tuple(item for item in signals if item in VERIFIABLE_CLAIM_TYPES)

            if not signals:
                if not is_assertive(clause):
                    continue
                emit(
                    clause_start,
                    clause_end,
                    _pick_type(clause, ()),
                    CONFIDENCE_HINT_ONLY,
                    (),
                    clause_end >= sentence_end,
                )
                continue

            primary_type = _pick_type(clause, signals)
            complete = clause_end >= sentence_end

            # A promise plus a credential in one clause yields two claims: the
            # promise for the whole clause, and the credential for the part of
            # the clause before the promise, so both are independently
            # verifiable later.
            if promise_signals and verification_signals:
                fragment_start, fragment_end = _trim_span(text, clause_start, clause_end)
                promise_start = min(
                    match.start()
                    for signal in CLAIM_SIGNALS
                    if signal.claim_type in promise_signals
                    for match in signal.pattern.finditer(text, clause_start, clause_end)
                )
                head_start, head_end = _trim_span(text, fragment_start, promise_start)
                if head_end - head_start >= 4:
                    fragment_type = verification_signals[0]
                    emit(
                        head_start,
                        head_end,
                        fragment_type,
                        CONFIDENCE_FRAGMENT,
                        verification_signals,
                        False,
                    )

            emit(
                clause_start,
                clause_end,
                primary_type,
                CONFIDENCE_SIGNALLED if complete else CONFIDENCE_FRAGMENT,
                signals,
                complete,
            )

    claims.sort(key=lambda item: (item.start, item.end))
    logger.debug(
        "Deterministic claim extraction complete",
        extra={"claims": len(claims), "normalized_chars": len(text)},
    )
    return claims


def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Trim whitespace and trailing separators from a span."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and (text[end - 1].isspace() or text[end - 1] in "-–—,:;"):
        end -= 1
    return start, end


__all__ = [
    "CLAIM_SIGNALS",
    "CLAIM_TYPE_PRECEDENCE",
    "CLAIM_VOCABULARY",
    "CONFIDENCE_FRAGMENT",
    "CONFIDENCE_HINT_ONLY",
    "CONFIDENCE_SIGNALLED",
    "MAX_CLAIMS",
    "ClaimSignal",
    "RawClaim",
    "clause_spans",
    "detect_signals",
    "extract_claims",
    "is_assertive",
    "is_filler",
    "sentence_spans",
]
