"""Manual smoke test for Phase 4 claim verification.

Not part of the pytest suite. Run it directly to see what the verification layer
does in this environment:

    cd backend
    python -m scripts.manual_verification

Two passes:

1. **Offline, with a built-in fixture provider.** Always runs, needs no key and
   no network. It exercises the paths that matter: a regulator record confirming
   a claim, one conflicting with it, a lookalike domain that confirms nothing,
   and a claim family no register can settle.
2. **Live.** Runs the real `VerificationService`. With no `SERPAPI_KEY` it must
   report `INSUFFICIENT_EVIDENCE` / `SEARCH_UNAVAILABLE` and say so — never
   `CONTRADICTED`, and never a source that was never retrieved.

The script prints what was looked up and what was concluded. It never adds a
judgement of its own.
"""

from __future__ import annotations

import sys

from app.core.config import get_settings
from app.schemas.claims import Claim, ClaimType
from app.schemas.common import EvidenceSpan
from app.schemas.entities import Entity, EntityType, normalize_entity_name
from app.schemas.search import SearchResponse, SearchResult, SearchStatus
from app.services.search import SearchProvider, SearchService
from app.services.verification import VerificationService, build_verification_service

ENTITY_NAME = "Acme Capital Advisors"
CLAIM_TEXT = f"{ENTITY_NAME} is SEBI registered."


def build_claim_and_entity() -> tuple[Claim, Entity]:
    """Build the synthetic claim and entity this script verifies."""
    entity = Entity(
        id="entity_001",
        name=ENTITY_NAME,
        entity_type=EntityType.COMPANY,
        normalized_name=normalize_entity_name(ENTITY_NAME),
        confidence=0.95,
        evidence_span=EvidenceSpan(start=0, end=len(ENTITY_NAME), text=ENTITY_NAME),
    )
    claim = Claim(
        id="claim_001",
        text=CLAIM_TEXT,
        claim_type=ClaimType.REGULATORY_STATUS,
        confidence=0.95,
        evidence_span=EvidenceSpan(start=0, end=len(CLAIM_TEXT), text=CLAIM_TEXT),
        entity_ids=(entity.id,),
    )
    return claim, entity


class FixtureProvider(SearchProvider):
    """A recorded fixture set, so the interesting paths run offline.

    Args:
        responses: Responses to return, one per query, in order.
    """

    name = "fixture"

    def __init__(self, responses: tuple[SearchResponse, ...]) -> None:
        self._responses = list(responses)

    def is_available(self) -> bool:
        """Always True: a fixture needs nothing."""
        return True

    def search(self, query: str, max_results: int) -> SearchResponse:
        """Return the next scripted response, or an empty successful one."""
        if self._responses:
            return self._responses.pop(0)
        return SearchResponse(
            query=query,
            results=(),
            provider=self.name,
            status=SearchStatus.OK,
            provider_query_sent=True,
        )


def fixture_response(query: str, results: tuple[SearchResult, ...]) -> SearchResponse:
    """Build a successful fixture response."""
    return SearchResponse(
        query=query,
        results=results,
        provider="fixture",
        status=SearchStatus.OK,
        provider_query_sent=True,
    )


def sebi_page(path: str, title: str, snippet: str) -> SearchResult:
    """Build a SEBI-hosted fixture result."""
    return SearchResult(title=title, url=f"https://www.sebi.gov.in{path}", snippet=snippet)


SCENARIOS: tuple[tuple[str, tuple[SearchResult, ...]], ...] = (
    (
        "A SEBI record confirms the registration",
        (
            sebi_page(
                "/intermediaries/acme",
                f"{ENTITY_NAME} registration",
                f"{ENTITY_NAME} is registered as an investment adviser.",
            ),
        ),
    ),
    (
        "A SEBI order conflicts with the registration",
        (
            sebi_page(
                "/orders/acme",
                f"Order against {ENTITY_NAME}",
                f"The registration of {ENTITY_NAME} was cancelled.",
            ),
        ),
    ),
    (
        "A lookalike domain confirms nothing",
        (
            SearchResult(
                title=f"{ENTITY_NAME} registration",
                url="https://fake-sebi-example.com/acme",
                snippet=f"{ENTITY_NAME} is registered with SEBI.",
            ),
        ),
    ),
    (
        "No record mentions the entity",
        (
            sebi_page(
                "/others",
                "Another registered entity",
                "Another entity is registered as an investment adviser.",
            ),
        ),
    ),
    ("Nothing was found at all", ()),
)


def print_result(label: str, result: object) -> None:
    """Print one verification outcome in a readable form."""
    print(f"scenario        : {label}")
    print(f"  status        : {result.status.value}")
    print(f"  reason code   : {result.reason_code}")
    print(f"  confidence    : {result.confidence:.2f} (confidence in this status)")
    print(f"  reason        : {result.reason}")
    print(f"  sources cited : {', '.join(result.source_ids) or '-'}")
    print(f"  queries       : {len(result.queries)}")
    for warning in result.warnings:
        print(f"  warning       : {warning}")


def run_offline() -> None:
    """Run the fixture scenarios, which need no key and no network."""
    claim, entity = build_claim_and_entity()
    settings = get_settings()

    print("=" * 72)
    print("OFFLINE FIXTURE PASS — deterministic, no credentials required")
    print("=" * 72)

    for label, results in SCENARIOS:
        service = VerificationService(
            settings=settings,
            search_service=SearchService(
                settings=settings,
                provider=FixtureProvider((fixture_response("q", results),)),
            ),
        )
        print_result(label, service.verify_claim(claim, (entity,)))
        print("-" * 72)


def run_live() -> int:
    """Run the real service and print what this environment supports."""
    settings = get_settings()
    service = build_verification_service(settings)
    claim, entity = build_claim_and_entity()

    print("=" * 72)
    print("LIVE PASS — real SearchService, this machine's configuration")
    print("=" * 72)
    print(f"provider        : {service.search_service.provider_name}")
    print(f"search available: {service.available}")
    print("-" * 72)

    result = service.verify_claim(claim, (entity,))
    print_result("Live verification", result)

    print("-" * 72)
    if not service.available:
        print(
            "External search is unavailable. Every claim in this investigation "
            "stays unassessed, and that limitation is reported as such."
        )
        return 0
    return 0


def main() -> int:
    """Run both passes. Returns a process exit code."""
    run_offline()
    run_live()
    print("=" * 72)
    print(
        "Verification reports whether an external record supports, contradicts or "
        "does not address a claim. It is not a risk assessment."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())