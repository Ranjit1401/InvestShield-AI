"""Claim ↔ source comparison (Phase 4, §4).

This module reads retrieved documents and reports what each one *can* say about
a specific claim. It is pure matching — no network, no LLM, no verdict. The
decision about what those matches mean is `decision_engine.py`'s job, and
keeping them apart is what makes both testable.

Three tests must pass before any document is allowed to influence a status:

1. **Identity.** The document must be about the exact entity the claim names.
   Matching is token-boundary based and rejects *prefix* matches: `ABC Capital`
   is not confirmed by a page about `ABC Capital Advisors`, and a page that
   mentions `ABC Capital Holdings` without the plain name is *ambiguous*
   rather than matching.
2. **Authority.** The publisher must be in the registry and its tier must be
   authoritative. An unlisted official host is recorded and reported, but can
   never settle a claim (D-018).
3. **Relevance.** That authority's records must be relevant to *this* claim
   family. SEBI can establish who is registered; it cannot establish what a
   scheme returns (D-006).

A document that fails any of these is still cited as context in Phase 5, but it
cannot support or contradict anything here.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

from app.schemas.claims import ClaimType
from app.schemas.entities import normalize_entity_name
from app.schemas.search import SearchResult
from app.schemas.verification import (
    AUTHORITATIVE_TIERS,
    TIER_PRIORITY,
    IdentityMatch,
    SourceTier,
)
from app.services.verification.authority_registry import resolve_authority
from app.services.verification.target import VerificationTarget

#: Characters stripped before matching, mirroring entity normalisation: `@`,
#: `.`, `-` and `&` are kept because they are structural in identifiers.
_MATCH_PUNCTUATION = re.compile(r"[^\w\s&.@-]+", re.UNICODE)
_WHITESPACE_RUN = re.compile(r"\s+")
_EDGE_PUNCTUATION = re.compile(r"^[\s.\-@&]+|[\s.\-@&]+$")

#: Tokens that, when they immediately follow a matched name, indicate the
#: document is about a *longer, different* name.
#:
#: `ABC Capital` must not be confirmed by a page about `ABC Capital Advisors`,
#: `ABC Capital Limited` or `ABC Capital Holdings`. Conservatively rejecting the
#: match costs an unverified claim; wrongly accepting it would transfer one
#: entity's registration status to another — the exact failure mode that makes a
#: verification product untrustworthy.
NAME_CONTINUATION_TOKENS: frozenset[str] = frozenset(
    {
        "advisers",
        "advisors",
        "advisor",
        "advisory",
        "asset",
        "assets",
        "associates",
        "capital",
        "consultancy",
        "consultants",
        "corp",
        "corporation",
        "finance",
        "financial",
        "global",
        "group",
        "holdings",
        "inc",
        "incorporated",
        "india",
        "international",
        "limited",
        "llp",
        "ltd",
        "management",
        "partners",
        "private",
        "pvt",
        "securities",
        "services",
        "solutions",
        "technologies",
        "ventures",
    }
)

#: Phrases in an authoritative record that state the opposite of a positive
#: claim. Evaluated *before* support cues, because "the registration of X has
#: been cancelled" contains the word "registered".
CONTRADICTION_CUES: tuple[str, ...] = (
    "not registered",
    "no longer registered",
    "not a registered",
    "registration cancelled",
    "registration revoked",
    "registration suspended",
    "registration expired",
    "registration lapsed",
    "registration withdrawn",
    "de-registered",
    "deregistered",
    "struck off",
    "delisted",
    "not listed",
    "no registration record",
    "no such registration",
    "not found in the records",
    "does not appear in the records",
    "no record of",
    "prohibited from",
    "cease and desist",
    "revoked",
    "cancelled",
    "suspended",
)

#: Support cues per claim family — the vocabulary the relevant register uses.
SUPPORT_CUES: dict[ClaimType, tuple[str, ...]] = {
    ClaimType.REGULATORY_STATUS: (
        "registered as",
        "registered with",
        "is registered",
        "registration no",
        "registration number",
        "registered entity",
        "has been registered",
    ),
    ClaimType.CREDENTIAL_CLAIM: (
        "registered as",
        "registration number",
        "certificate of registration",
        "license",
        "licensed",
    ),
    ClaimType.COMPANY_CLAIM: (
        "company master data",
        "corporate identity number",
        "incorporation",
        "incorporated on",
        "registered office",
    ),
    ClaimType.OWNERSHIP_CLAIM: (
        "director",
        "promoter",
        "shareholding",
        "shareholder",
        "beneficial owner",
    ),
    ClaimType.AFFILIATION_CLAIM: (
        "group company",
        "subsidiary",
        "affiliate",
        "associated company",
        "holding company",
    ),
    ClaimType.PERFORMANCE_CLAIM: (
        "disclosure",
        "performance",
        "returns",
        "aum",
        "nav",
    ),
    ClaimType.WITHDRAWAL_CLAIM: (
        "withdrawal",
        "redemption",
        "notice period",
    ),
}

#: Cues used for a claim family with no authority relevance of its own. A source
#: is never claim-relevant for such a family, so these only apply if relevance is
#: widened by a caller.
DEFAULT_SUPPORT_CUES: tuple[str, ...] = ("registered as", "registration number")

#: Number of hex characters kept from the canonical-URL digest. Twelve is enough
#: to make a collision between distinct sources implausible while keeping ids
#: short and stable across runs.
SOURCE_ID_DIGEST_LENGTH = 12


class Stance:
    """How a single document relates to a claim. Not a verdict."""

    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    NEUTRAL = "NEUTRAL"


def normalize_for_match(text: str | None) -> str:
    """Normalize free text for boundary-safe identity matching.

    Args:
        text: Raw document text.

    Returns:
        Lower-cased, punctuation-trimmed text with collapsed whitespace.
    """
    if not text:
        return ""
    value = unicodedata.normalize("NFC", str(text)).strip().lower()
    value = _MATCH_PUNCTUATION.sub(" ", value)
    value = _WHITESPACE_RUN.sub(" ", value)
    return _EDGE_PUNCTUATION.sub("", value).strip()


def _tokens(normalized: str) -> tuple[str, ...]:
    """Split normalized text into whitespace-delimited tokens."""
    return tuple(normalized.split()) if normalized else ()


def source_id_for(canonical_url: str) -> str:
    """Return a stable id for the *source document* at `canonical_url`.

    Args:
        canonical_url: Canonical URL from Phase 3.

    Returns:
        ``src_<12 hex chars>``, derived from the canonical URL only, so the same
        document keeps the same id across claims, queries and runs.
    """
    digest = hashlib.sha256((canonical_url or "").encode("utf-8")).hexdigest()
    return f"src_{digest[:SOURCE_ID_DIGEST_LENGTH]}"


def result_id_for(canonical_url: str) -> str:
    """Return a stable id for the individual search result at `canonical_url`.

    Distinct from `source_id_for()` so Phase 5 can later distinguish "a document
        we looked at" from "a result that addressed this claim" (D-020).

    Args:
        canonical_url: Canonical URL from Phase 3.

    Returns:
        ``res_<12 hex chars>``.
    """
    digest = hashlib.sha256((canonical_url or "").encode("utf-8")).hexdigest()
    return f"res_{digest[:SOURCE_ID_DIGEST_LENGTH]}"


def _contains_phrase(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    """Token-boundary substring test.

    Args:
        haystack: Document tokens.
        needle: Phrase tokens.

    Returns:
        True when `needle` appears as a contiguous token run in `haystack`.
    """
    if not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(haystack[index : index + width] == needle for index in range(len(haystack) - width + 1))


def match_identity(text: str, subject: str) -> IdentityMatch:
    """Decide whether `text` is about exactly the entity named by `subject`.

    A plain name inside a longer name is rejected, not accepted:

    ```
    match_identity("ABC Capital Advisors profile", "ABC Capital")  -> AMBIGUOUS
    match_identity("ABC Capital is registered", "ABC Capital")      -> MATCHED
    match_identity("An unrelated site", "ABC Capital")              -> ABSENT
    ```

    Args:
        text: Document text (title plus snippet).
        subject: The claimed entity name.

    Returns:
        `MATCHED`, `AMBIGUOUS` when only a longer name matched, or `ABSENT`.
    """
    needle = _tokens(normalize_for_match(normalize_entity_name(subject)))
    if not needle:
        return IdentityMatch.ABSENT

    haystack = _tokens(normalize_for_match(text))
    if not haystack:
        return IdentityMatch.ABSENT

    width = len(needle)
    saw_extension = False
    for index in range(len(haystack) - width + 1):
        if haystack[index : index + width] != needle:
            continue
        following = haystack[index + width] if index + width < len(haystack) else ""
        if following and following in NAME_CONTINUATION_TOKENS:
            saw_extension = True
            continue
        return IdentityMatch.MATCHED

    return IdentityMatch.AMBIGUOUS if saw_extension else IdentityMatch.ABSENT


def match_identity_for_target(text: str, target: VerificationTarget) -> IdentityMatch:
    """Match `text` against every party named by a claim.

    Args:
        text: Document text (title plus snippet).
        target: The verification target.

    Returns:
        The strongest match across the claim's identity entities. A plain match
        on any named party is `MATCHED`; only longer-name matches yield
        `AMBIGUOUS`.
    """
    subjects = [entity.name for entity in target.identity_entities if entity.name.strip()]
    if not subjects and target.subject.strip():
        subjects = [target.subject]

    best = IdentityMatch.ABSENT
    for subject in subjects:
        verdict = match_identity(text, subject)
        if verdict is IdentityMatch.MATCHED:
            return IdentityMatch.MATCHED
        if verdict is IdentityMatch.AMBIGUOUS:
            best = IdentityMatch.AMBIGUOUS
    return best


def _has_cue(text: str, cues: tuple[str, ...]) -> str | None:
    """Return the first cue present in `text`, or ``None``.

    Args:
        text: Already normalized document text.
        cues: Lower-cased cue phrases.

    Returns:
        The matched cue, or ``None``. Longer cues are tested first so
        "registration cancelled" wins over "cancelled".
    """
    for cue in sorted(cues, key=len, reverse=True):
        if cue and cue in text:
            return cue
    return None


def detect_stance(text: str, claim_type: ClaimType) -> tuple[str, str | None]:
    """Determine whether a document supports or contradicts a claim family.

    Contradiction cues are tested first: a sentence saying "the registration has
    been cancelled" contains the word "registered", and reading that as support
    is the most damaging mistake available to this stage.

    Args:
        text: Document text (title plus snippet).
        claim_type: The claim family being tested.

    Returns:
        ``(stance, matched_cue)`` where stance is one of `Stance.SUPPORTS`,
        `Stance.CONTRADICTS` or `Stance.NEUTRAL`.
    """
    normalized = normalize_for_match(text)
    if not normalized:
        return Stance.NEUTRAL, None

    cue = _has_cue(normalized, CONTRADICTION_CUES)
    if cue is not None:
        return Stance.CONTRADICTS, cue

    cue = _has_cue(normalized, SUPPORT_CUES.get(claim_type, DEFAULT_SUPPORT_CUES))
    if cue is not None:
        return Stance.SUPPORTS, cue

    return Stance.NEUTRAL, None


def assess_result(result: SearchResult, target: VerificationTarget) -> "AssessedSource":
    """Assess one retrieved document against one claim.

    Args:
        result: A Phase 3 `SearchResult`.
        target: The verification target.

    Returns:
        An `AssessedSource` recording identity, authority, relevance and stance.
        Stance is `NEUTRAL` unless the document is identity-matched,
        claim-relevant and authoritative — a document that fails any gate can
        never move a status.
    """
    canonical = result.canonical_url or result.url
    tier, authority = resolve_authority(result.source_domain or result.url)
    text = f"{result.title} {result.snippet or ''}"

    identity = match_identity_for_target(text, target)

    claim_relevant = (
        authority is not None
        and authority.is_relevant_to(target.claim_type)
        and tier in AUTHORITATIVE_TIERS
    )

    stance = Stance.NEUTRAL
    matched_cue: str | None = None
    if claim_relevant and identity is IdentityMatch.MATCHED:
        stance, matched_cue = detect_stance(text, target.claim_type)

    return AssessedSource(
        source_id=source_id_for(canonical),
        result_id=result_id_for(canonical),
        url=result.url,
        canonical_url=canonical,
        title=result.title,
        source_domain=result.source_domain,
        tier=tier,
        authority_key=authority.key if authority else None,
        authority_name=authority.name if authority else None,
        identity=identity,
        claim_relevant=claim_relevant,
        stance=stance,
        matched_cue=matched_cue,
        position=result.position,
    )


def assess_results(
    results: tuple[SearchResult, ...], target: VerificationTarget
) -> tuple["AssessedSource", ...]:
    """Assess every retrieved document for one claim.

    Args:
        results: Phase 3 results, already de-duplicated by canonical URL.
        target: The verification target.

    Returns:
        One `AssessedSource` per result, in the order given.
    """
    return tuple(assess_result(result, target) for result in results)


class AssessedSource:
    """What one retrieved document can say about one claim.

    Attributes:
        source_id: Stable ``src_`` id of the document.
        result_id: Stable ``res_`` id of the search result.
        url: Result URL as returned.
        canonical_url: Canonical URL used for de-duplication and id derivation.
        title: Result title.
        source_domain: Publisher hostname.
        tier: Authority tier of the publisher.
        authority_key: Registry key of the operating authority, or ``None``.
        authority_name: Human-readable authority name, or ``None``.
        identity: Whether the document is about the claimed entity.
        claim_relevant: Whether this authority's records can speak to this
            claim family.
        stance: `Stance.SUPPORTS`, `Stance.CONTRADICTS` or `Stance.NEUTRAL`.
        matched_cue: The cue that produced the stance, for auditability.
        position: Provider rank, used for deterministic tie-breaking.
    """

    __slots__ = (
        "source_id",
        "result_id",
        "url",
        "canonical_url",
        "title",
        "source_domain",
        "tier",
        "authority_key",
        "authority_name",
        "identity",
        "claim_relevant",
        "stance",
        "matched_cue",
        "position",
    )

    def __init__(
        self,
        source_id: str,
        result_id: str,
        url: str,
        canonical_url: str,
        title: str,
        source_domain: str,
        tier: SourceTier,
        authority_key: str | None,
        authority_name: str | None,
        identity: IdentityMatch,
        claim_relevant: bool,
        stance: str,
        matched_cue: str | None,
        position: int,
    ) -> None:
        self.source_id = source_id
        self.result_id = result_id
        self.url = url
        self.canonical_url = canonical_url
        self.title = title
        self.source_domain = source_domain
        self.tier = tier
        self.authority_key = authority_key
        self.authority_name = authority_name
        self.identity = identity
        self.claim_relevant = claim_relevant
        self.stance = stance
        self.matched_cue = matched_cue
        self.position = position

    @property
    def is_authoritative(self) -> bool:
        """Whether the publisher's tier may settle a claim."""
        return self.tier in AUTHORITATIVE_TIERS

    @property
    def rank(self) -> tuple[int, int]:
        """Sort key: authoritative tier first, then provider position."""
        return TIER_PRIORITY[self.tier], self.position

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"AssessedSource(source_id={self.source_id!r}, domain={self.source_domain!r}, "
            f"tier={self.tier.value!r}, identity={self.identity.value!r}, "
            f"relevant={self.claim_relevant!r}, stance={self.stance!r})"
        )


__all__ = [
    "CONTRADICTION_CUES",
    "DEFAULT_SUPPORT_CUES",
    "NAME_CONTINUATION_TOKENS",
    "SOURCE_ID_DIGEST_LENGTH",
    "SUPPORT_CUES",
    "AssessedSource",
    "Stance",
    "assess_result",
    "assess_results",
    "detect_stance",
    "match_identity",
    "match_identity_for_target",
    "normalize_for_match",
    "result_id_for",
    "source_id_for",
]