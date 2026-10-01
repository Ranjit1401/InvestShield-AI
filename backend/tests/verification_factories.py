"""Object factories shared by the Phase 4 verification tests.

Verification consumes Phase 2 `Claim`/`Entity` objects and Phase 3
`SearchResult`/`SearchResponse` objects. Building those by hand in every test
would bury the behaviour under setup, so the constructors live here and each
test says only what it is about.

No network, no credentials, no LLM: every fixture is in-memory.
"""

from __future__ import annotations

from app.schemas.claims import Claim, ClaimType
from app.schemas.common import EvidenceSpan
from app.schemas.entities import Entity, EntityType, normalize_entity_name
from app.schemas.search import (
    SEARCH_NOT_CONFIGURED,
    SEARCH_SERVICE_ERROR,
    SearchResponse,
    SearchResult,
    SearchStatus,
)


def make_claim(
    text: str,
    claim_type: ClaimType = ClaimType.REGULATORY_STATUS,
    *,
    claim_id: str = "claim_001",
    entity_ids: tuple[str, ...] = (),
    metadata: dict[str, str] | None = None,
    confidence: float = 0.9,
) -> Claim:
    """Build a `Claim` whose span indexes a synthetic original string.

    Args:
        text: Verbatim claim text.
        claim_type: Claim family.
        claim_id: Id to assign.
        entity_ids: Linked entity ids.
        metadata: Values extracted from the claim text.
        confidence: Extraction confidence.

    Returns:
        A `Claim` whose `text` equals its span text.
    """
    return Claim(
        id=claim_id,
        text=text,
        claim_type=claim_type,
        confidence=confidence,
        evidence_span=EvidenceSpan(start=0, end=len(text), text=text),
        entity_ids=entity_ids,
        metadata=dict(metadata or {}),
    )


def make_entity(
    name: str,
    entity_type: EntityType = EntityType.COMPANY,
    *,
    entity_id: str = "entity_001",
    normalized: str | None = None,
    metadata: dict[str, str | int | float | bool] | None = None,
) -> Entity:
    """Build an `Entity` with a consistent normalized name.

    Args:
        name: Entity name as written.
        entity_type: Entity family.
        entity_id: Id to assign.
        normalized: Override for the normalized name.
        metadata: Extra attributes.

    Returns:
        An `Entity` whose `name` equals its span text.
    """
    return Entity(
        id=entity_id,
        name=name,
        entity_type=entity_type,
        normalized_name=normalized or normalize_entity_name(name),
        confidence=0.9,
        evidence_span=EvidenceSpan(start=0, end=len(name), text=name),
        metadata=dict(metadata or {}),
    )


def make_result(
    title: str,
    url: str,
    *,
    snippet: str | None = None,
    position: int = 1,
) -> SearchResult:
    """Build a Phase 3 `SearchResult` with classification left to the service."""
    return SearchResult(title=title, url=url, snippet=snippet, position=position)


def ok_response(
    query: str = "test query",
    results: tuple[SearchResult, ...] = (),
    *,
    warnings: tuple[str, ...] = (),
) -> SearchResponse:
    """Build a successful `SearchResponse`, optionally with no results."""
    return SearchResponse(
        query=query,
        results=results,
        provider="fake",
        status=SearchStatus.OK,
        warnings=warnings,
        provider_query_sent=True,
    )


def error_response(
    query: str = "test query",
    *,
    code: str = SEARCH_SERVICE_ERROR,
    warnings: tuple[str, ...] = ("The search provider could not be reached.",),
) -> SearchResponse:
    """Build a failed `SearchResponse`."""
    return SearchResponse(
        query=query,
        results=(),
        provider="fake",
        status=SearchStatus.ERROR,
        warnings=warnings,
        error_code=code,
        provider_query_sent=True,
    )


def unavailable_response(
    query: str = "test query",
    *,
    warnings: tuple[str, ...] = ("External web search is not configured.",),
) -> SearchResponse:
    """Build a never-attempted `SearchResponse`."""
    return SearchResponse(
        query=query,
        results=(),
        provider="null",
        status=SearchStatus.UNAVAILABLE,
        warnings=warnings,
        error_code=SEARCH_NOT_CONFIGURED,
        provider_query_sent=False,
    )


__all__ = [
    "error_response",
    "make_claim",
    "make_entity",
    "make_result",
    "ok_response",
    "unavailable_response",
]