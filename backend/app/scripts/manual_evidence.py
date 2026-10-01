"""Manual smoke test for the Phase 5 evidence engine.

Not part of the pytest suite. Run it directly to see what the evidence layer does
in this environment:

    cd backend
    python -m app.scripts.manual_evidence

Two passes:

1. **Offline, with a built-in fixture provider.** Always runs, needs no key and
   no network. It walks the paths that matter: a regulator record confirming a
   claim, one conflicting with it, two official records disagreeing, a page
   about a *similarly named* company, a lookalike domain, and a claim family
   that is never externally verifiable.
2. **Live.** Runs the real `VerificationService` and feeds its results to
   `EvidenceService`. With no `SERPAPI_KEY` the honest output is zero evidence
   plus a warning saying why — never a fabricated item.

Every excerpt printed below is copied verbatim from the fixture snippet. The
script prints provenance for each item so the chain

    claim -> evidence -> result -> source

is visible end to end.
"""

from __future__ import annotations

import sys

from app.core.config import get_settings
from app.schemas.claims import Claim, ClaimType
from app.schemas.common import EvidenceSpan
from app.schemas.entities import Entity, EntityType, normalize_entity_name
from app.schemas.search import (
    SearchResponse,
    SearchResult,
    SearchStatus,
)
from app.services.evidence import EvidenceService
from app.services.search import SearchProvider, SearchService
from app.services.verification import VerificationService
from app.services.verification.target import build_target

ENTITY_NAME = "Acme Capital Advisors"
ENTITY_ID = "entity_001"
CLAIM_ID = "claim_001"


def build_entity() -> Entity:
    """Build the synthetic party this script investigates."""
    return Entity(
        id=ENTITY_ID,
        name=ENTITY_NAME,
        entity_type=EntityType.COMPANY,
        normalized_name=normalize_entity_name(ENTITY_NAME),
        confidence=0.95,
        evidence_span=EvidenceSpan(start=0, end=len(ENTITY_NAME), text=ENTITY_NAME),
    )


def build_claim(claim_type: ClaimType, text: str | None = None) -> Claim:
    """Build a synthetic claim linked to the entity above."""
    body = text or f"{ENTITY_NAME} is SEBI registered."
    return Claim(
        id=CLAIM_ID,
        text=body,
        claim_type=claim_type,
        confidence=0.95,
        evidence_span=EvidenceSpan(start=0, end=len(body), text=body),
        entity_ids=(ENTITY_ID,),
    )


class FixtureProvider(SearchProvider):
    """A recorded fixture set, so the interesting paths run offline.

    Args:
        results: Results returned for every query.
    """

    name = "fixture"

    def __init__(self, results: tuple[SearchResult, ...]) -> None:
        self._results = results

    def is_available(self) -> bool:
        """Always True: a fixture needs nothing."""
        return True

    def search(self, query: str, max_results: int) -> SearchResponse:
        """Return the fixture results for `query`."""
        return SearchResponse(
            query=query,
            results=self._results,
            provider=self.name,
            status=SearchStatus.OK,
            provider_query_sent=True,
        )


def sebi_page(path: str, title: str, snippet: str, position: int = 1) -> SearchResult:
    """Build a SEBI-hosted fixture result."""
    return SearchResult(
        title=title,
        url=f"https://www.sebi.gov.in{path}",
        snippet=snippet,
        position=position,
    )


SCENARIOS: tuple[tuple[str, ClaimType, tuple[SearchResult, ...]], ...] = (
    (
        "A SEBI record confirms the registration",
        ClaimType.REGULATORY_STATUS,
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
        ClaimType.REGULATORY_STATUS,
        (
            sebi_page(
                "/orders/acme",
                f"Order against {ENTITY_NAME}",
                f"The registration of {ENTITY_NAME} was cancelled.",
            ),
        ),
    ),
    (
        "Two official records disagree",
        ClaimType.REGULATORY_STATUS,
        (
            sebi_page(
                "/intermediaries/acme",
                f"{ENTITY_NAME} registration",
                f"{ENTITY_NAME} is registered as an investment adviser.",
                position=1,
            ),
            sebi_page(
                "/orders/acme",
                f"Order against {ENTITY_NAME}",
                f"The registration of {ENTITY_NAME} was cancelled.",
                position=2,
            ),
        ),
    ),
    (
        "The regulator has a page about a similarly named company",
        ClaimType.REGULATORY_STATUS,
        (
            sebi_page(
                "/intermediaries/acme-holdings",
                "Acme Capital Holdings registration",
                "Acme Capital Holdings is registered as an investment adviser.",
            ),
        ),
    ),
    (
        "Only a lookalike website was found",
        ClaimType.REGULATORY_STATUS,
        (
            SearchResult(
                title=f"{ENTITY_NAME} registration",
                url="https://fake-sebi-example.com/acme",
                snippet=f"{ENTITY_NAME} is registered with SEBI. Send funds to "
                "acmefunds@okaxis for the activation fee.",
                position=1,
            ),
        ),
    ),
    (
        "A promised return has no register to check it against",
        ClaimType.RETURN_PROMISE,
        (
            sebi_page(
                "/circulars/returns",
                "Notice on misleading return claims",
                "SEBI cautions the public against guaranteed monthly returns.",
            ),
        ),
    ),
)


def print_bundle(label: str, response: object) -> None:
    """Print one claim's verification and its evidence, with provenance."""
    print(f"scenario        : {label}")
    print(f"  status        : {response.verification_status.value}")
    print(f"  reason code   : {response.verification_reason_code}")
    print(
        f"  evidence      : {response.evidence_count} item(s), "
        f"{response.proof_count} of them probative"
    )
    for item in response.evidence:
        print(f"    relationship: {item.relationship.value}")
        print(f"    relevance   : {item.relevance.value}")
        print(f"    record type : {item.evidence_type.value}")
        print(f"    excerpt     : [{item.excerpt_origin}] {item.excerpt}")
        print(f"    publisher   : {item.source.domain} ({item.source.source_type.value})")
        print(f"    authority   : {item.source.source_tier.value}")
        print(f"    url         : {item.source.url}")
        print(f"    trace       : {item.trace_path}")
        if item.matched_cue:
            print(f"    cue         : {item.matched_cue}")
    for warning in response.warnings:
        print(f"  warning       : {warning}")


def run_offline() -> None:
    """Run the fixture scenarios, which need no key and no network."""
    settings = get_settings()
    entity = build_entity()
    evidence_service = EvidenceService()

    print("=" * 74)
    print("OFFLINE FIXTURE PASS - deterministic, no credentials required")
    print("=" * 74)

    for label, claim_type, fixture_results in SCENARIOS:
        claim = build_claim(claim_type)
        search_service = SearchService(
            settings=settings, provider=FixtureProvider(fixture_results)
        )

        # Retrieve exactly as the pipeline does, so the results below are the
        # classified, de-duplicated ones Phase 3 actually returned.
        target = build_target(claim, (entity,))
        responses = tuple(
            search_service.search(query, 5) for query in target.search_queries
        )
        retrieved: list[SearchResult] = []
        for response in responses:
            retrieved.extend(response.results)

        verification = VerificationService(
            settings=settings, search_service=search_service
        ).verify_claim(claim, (entity,))
        bundle = evidence_service.build_claim(
            claim, verification, tuple(retrieved), (entity,), responses=responses
        )
        print_bundle(label, bundle)
        print("-" * 74)


def run_live() -> int:
    """Run the real pipeline and feed its results to the evidence stage."""
    settings = get_settings()
    entity = build_entity()
    claim = build_claim(ClaimType.REGULATORY_STATUS)

    print("=" * 74)
    print("LIVE PASS - real SearchService and VerificationService")
    print("=" * 74)

    verification_service = VerificationService(settings=settings)
    verification = verification_service.verify_claim(claim, (entity,))
    print(f"search available: {verification_service.available}")
    print(f"  status        : {verification.status.value}")
    print(f"  reason code   : {verification.reason_code}")
    print("-" * 74)

    bundle = EvidenceService().build_claim(claim, verification, (), (entity,))
    print_bundle("Live investigation", bundle)

    print("-" * 74)
    if not bundle.evidence:
        print(
            "No evidence is shown because no source was retrieved. That is the "
            "correct output: an empty evidence set with a stated reason, never "
            "an invented one."
        )
    return 0


def main() -> int:
    """Run both passes. Returns a process exit code."""
    run_offline()
    run_live()
    print("=" * 74)
    print(
        "Evidence shows what was actually retrieved and where it came from. It "
        "does not judge the claim, score the risk, or advise anyone."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
