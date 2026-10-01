"""Evidence de-duplication and ordering (Phase 5, §14/§15).

Two evidence items are the same item when they describe the same finding:

    claim + source + result + excerpt origin + excerpt + relationship

Everything else that differs is a genuinely distinct finding and is kept. In
particular this module never collapses items because they share a domain, share
a similar title, or are attached to similar claims. Two pages on one regulator's
site saying different things are two findings, and merging them would erase the
very distinction a reader is being asked to check.

De-duplication is also the safety net against double-counting. A URL reachable
from three of the five queries built for a claim would otherwise contribute three
identical "supporting" items, and a report that says "confirmed by 3 sources"
when there is one is wrong in the direction that matters most.

Ordering is deterministic and uses only existing priority data:

1. source authority tier (Phase 4 `TIER_PRIORITY`) — the most authoritative
   publisher first;
2. relevance — most directly bearing on the claim first;
3. relationship — support, then conflict, then identification, then context;
4. provider position, then the stable evidence id, so ties never depend on
   dict or set iteration order.

No new credibility score is introduced. The sort keys are Phase 3's source
priority and Phase 4's tier, reused as-is (D-012, D-023).
"""

from __future__ import annotations

import re

from app.schemas.evidence import (
    RELATION_RANK,
    RELEVANCE_RANK,
    EvidenceItem,
    EvidenceSource,
)
from app.schemas.verification import TIER_PRIORITY

_WHITESPACE_RUN = re.compile(r"\s+")


def excerpt_key(excerpt: str) -> str:
    """Return the comparison key for an excerpt.

    Folds case and whitespace runs and nothing else. Two excerpts that differ
    only in spacing are the same quoted text; two that differ by a single word
    are two different findings, and are kept apart.

    Args:
        excerpt: A verbatim excerpt.

    Returns:
        The normalized key.
    """
    return _WHITESPACE_RUN.sub(" ", (excerpt or "").strip()).casefold()


def evidence_key(item: EvidenceItem) -> tuple[str, ...]:
    """Return the de-duplication key for an evidence item.

    Args:
        item: The evidence item.

    Returns:
        A hashable key covering claim, source, result, excerpt origin, excerpt
        and relationship.
    """
    return (
        item.claim_id,
        item.source.canonical_url,
        item.source.result_id,
        item.excerpt_origin,
        excerpt_key(item.excerpt),
        item.relationship.value,
    )


def dedupe_evidence(items: tuple[EvidenceItem, ...]) -> tuple[EvidenceItem, ...]:
    """Collapse items that describe the same finding, keeping the first.

    The first item wins so the surviving item is the one built from the earliest
    result position — a stable, explainable choice rather than an arbitrary one.

    Args:
        items: Candidate items in any order.

    Returns:
        Distinct items, in first-seen order.
    """
    seen: set[tuple[str, ...]] = set()
    unique: list[EvidenceItem] = []
    for item in items:
        key = evidence_key(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return tuple(unique)


def order_key(item: EvidenceItem) -> tuple[int, int, int, int, str]:
    """Return the deterministic sort key for an evidence item.

    Args:
        item: The evidence item.

    Returns:
        ``(tier priority, relevance, relationship, position, id)``.
    """
    return (
        TIER_PRIORITY[item.source.source_tier],
        RELEVANCE_RANK[item.relevance],
        RELATION_RANK[item.relationship],
        item.source.position,
        item.id,
    )


def order_evidence(items: tuple[EvidenceItem, ...]) -> tuple[EvidenceItem, ...]:
    """Sort evidence deterministically.

    Args:
        items: Items in any order.

    Returns:
        Items ordered by authority tier, relevance, relationship, provider
        position and finally stable id.
    """
    return tuple(sorted(items, key=order_key))


def distinct_sources(items: tuple[EvidenceItem, ...]) -> tuple[EvidenceSource, ...]:
    """Collect the distinct sources behind a set of evidence items.

    Args:
        items: Evidence items.

    Returns:
        Sources in first-seen order within the given (already ordered) items, so
        the source list reads in the same order as the evidence citing it.
    """
    seen: set[str] = set()
    sources: list[EvidenceSource] = []
    for item in items:
        source = item.source
        if source.source_id in seen:
            continue
        seen.add(source.source_id)
        sources.append(source)
    return tuple(sources)


__all__ = [
    "dedupe_evidence",
    "distinct_sources",
    "evidence_key",
    "excerpt_key",
    "order_evidence",
    "order_key",
]
