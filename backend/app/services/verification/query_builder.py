"""Targeted query construction (Phase 4, §3).

A generic query — even a well-formed one — asks the wrong question. Searching
`"35% monthly guaranteed returns"` verifies nothing: no registry lists a
promise like that, so the answer will be whatever marketing pages rank. What
verification needs is the query a *register* would answer: the claimed party's
name, plus the record being asserted, plus the body that keeps the record.

Two constraints make this safe to automate.

* **Deterministic and template-driven.** Queries are assembled from fixed
  templates per claim family, the subject as written in the input, and terms the
  registry body itself publishes in. Nothing is paraphrased, expanded or
  inferred, and no LLM is involved (D-019). A paraphrased query would verify a
  claim the user never made.
* **Bounded and de-duplicated.** `MAX_QUERIES_PER_CLAIM` caps how much quota one
  claim can spend, and de-duplication compares queries on a normalized key while
  emitting the original string, so identifiers, URLs, punctuation, amounts and
  percentages survive untouched.
"""

from __future__ import annotations

import re

from app.schemas.claims import ClaimType
from app.services.search.query import is_meaningful_query, normalize_query
from app.services.verification.target import VerificationTarget

#: Maximum targeted queries issued for a single claim. Bounds the cost of one
#: claim against a paid provider, and keeps the set a human can audit.
MAX_QUERIES_PER_CLAIM = 5

#: Authority-scoped queries are the most expensive and the most productive, so
#: they are built first and therefore survive the cap.
MAX_SITE_RESTRICTED_QUERIES = 3

#: Search terms appended per claim family. Each family gets the vocabulary the
#: relevant registries actually publish in — a registration claim is looked for
#: in a register, not in a news archive.
CLAIM_TOPICS: dict[ClaimType, tuple[str, ...]] = {
    ClaimType.REGULATORY_STATUS: ("registration", "registered entity"),
    ClaimType.CREDENTIAL_CLAIM: ("registration", "license"),
    ClaimType.COMPANY_CLAIM: ("company", "corporate record"),
    ClaimType.OWNERSHIP_CLAIM: ("directors", "shareholding"),
    ClaimType.AFFILIATION_CLAIM: ("group company", "affiliation"),
    ClaimType.PERFORMANCE_CLAIM: ("disclosure", "performance record"),
    ClaimType.WITHDRAWAL_CLAIM: ("withdrawal", "redemption"),
    ClaimType.RETURN_PROMISE: ("offering", "document"),
    ClaimType.PROFIT_PROMISE: ("offering", "document"),
    ClaimType.GUARANTEE_CLAIM: ("guarantee", "offering document"),
    ClaimType.PRODUCT_CLAIM: ("product", "scheme"),
}

#: Fallback topic for a claim family with no specific vocabulary.
DEFAULT_TOPIC = "official record"

#: Query-operator tokens, stripped from a subject before quoting so a stray
#: operator in the input cannot change what the query asks for.
_QUERY_OPERATORS = frozenset({"site", "filetype", "intitle", "inurl", "intext", "ext", "related"})

_WHITESPACE_RUN = re.compile(r"\s+")
_EDGE_QUOTES = re.compile(r'^["\']+|["\']+$')


def query_key(query: str) -> str:
    """Return the de-duplication key for a query.

    Only case and whitespace runs are folded. Nothing else is removed, so two
    queries that differ by a registration number, a percentage, a currency
    amount or a URL stay distinct — collapsing those would silently drop the
    exact-match lookup the claim depends on.

    Args:
        query: A built query.

    Returns:
        The normalized key.
    """
    return _WHITESPACE_RUN.sub(" ", query.strip()).casefold()


def _quote_subject(subject: str) -> str:
    """Quote a subject for a query, stripping operator tokens.

    A quoted phrase means "these words, together", which is what identity
    matching needs. Embedded quote characters are removed rather than escaped
    so a subject cannot break out of its own quoting.

    Args:
        subject: Raw subject text, as written in the input.

    Returns:
        The subject ready to interpolate into a query, or ``""`` when nothing
        usable remains.
    """
    cleaned = subject.strip()
    if not cleaned or not any(char.isalnum() for char in cleaned):
        return ""

    tokens = [
        token
        for token in cleaned.split()
        if token.split(":", 1)[0].strip(":").casefold() not in _QUERY_OPERATORS
    ]
    collapsed = _WHITESPACE_RUN.sub(" ", " ".join(tokens)).strip()
    collapsed = _EDGE_QUOTES.sub("", collapsed).strip()
    if not collapsed:
        return ""
    if any(char in collapsed for char in ('"', "'")):
        # A subject containing quotes is left unquoted rather than mangled.
        return collapsed
    return f'"{collapsed}"'


def build_queries(target: VerificationTarget) -> tuple[str, ...]:
    """Build the targeted queries for one claim.

    Order is deterministic and meaningful:

    1. the subject plus the claim family's primary topic, unconstrained;
    2. a site-restricted query against each of the most authoritative relevant
       hosts;
    3. the subject plus any verbatim identifier found in the claim;
    4. the remaining topics of the family;
    5. one authority's own vocabulary, as written by that body.

    Args:
        target: The resolved verification target.

    Returns:
        Up to `MAX_QUERIES_PER_CLAIM` unique, meaningful queries. Empty when
        the claim family is not applicable or no subject could be derived.
    """
    if not target.is_applicable:
        return ()

    subject = _quote_subject(target.subject)
    if not subject:
        return ()

    topics = CLAIM_TOPICS.get(target.claim_type, (DEFAULT_TOPIC,))
    primary_topic = topics[0]

    candidates: list[str] = [normalize_query(f"{subject} {primary_topic}")]

    # Authority-scoped queries come next so they always survive the cap: they are
    # the queries a register would actually answer.
    for authority in target.preferred_authorities[:MAX_SITE_RESTRICTED_QUERIES]:
        candidates.append(
            normalize_query(f"{subject} {primary_topic} site:{authority.domain}")
        )

    # An identifier the user wrote is worth an exact lookup of its own.
    for identifier in target.identifiers:
        quoted = _quote_subject(identifier)
        if quoted:
            candidates.append(normalize_query(f"{quoted} {primary_topic}"))

    for topic in topics[1:]:
        candidates.append(normalize_query(f"{subject} {topic}"))

    for authority in target.preferred_authorities[:1]:
        for topic in authority.topics:
            candidates.append(normalize_query(f"{subject} {topic} {authority.name}"))

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        cleaned = normalize_query(candidate)
        if not is_meaningful_query(cleaned):
            continue
        key = query_key(cleaned)
        if key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
        if len(unique) >= MAX_QUERIES_PER_CLAIM:
            break

    return tuple(unique)


__all__ = [
    "CLAIM_TOPICS",
    "DEFAULT_TOPIC",
    "MAX_QUERIES_PER_CLAIM",
    "MAX_SITE_RESTRICTED_QUERIES",
    "build_queries",
    "query_key",
]