"""Manual smoke test for the Phase 7 investigation graph.

Not part of the pytest suite. Run it directly to watch the orchestrator drive the
real Phase 1-6 services:

    cd backend
    python -m app.scripts.manual_graph

It runs three passes, all offline and needing no credentials:

1. **The full pipeline**, with the real services and a fixture search provider,
   so the searches Phase 4 issues are the only ones that happen and the evidence
   it produces cites them.
2. **A degraded run**, with search unavailable, showing that the investigation
   still completes and reports the limitation rather than accusing anyone.
3. **A rejection**, showing that an unusable submission ends the run immediately
   instead of producing an empty result that reads like a clean one.

Every pass prints the timeline, because that is the record a future report will
render. It prints the score with its own caveat attached, never as a percentage.
"""

from __future__ import annotations

import sys

from app.core.config import get_settings
from app.graph import (
    GraphContext,
    GraphDependencies,
    RecordingSearchService,
    investigation_summary,
    run_investigation,
)
from app.schemas.search import SearchResponse, SearchResult, SearchStatus
from app.services.evidence import EvidenceService
from app.services.extraction_service import ExtractionService
from app.services.red_flag_engine import RedFlagEngine
from app.services.risk import RiskService
from app.services.search import SearchProvider, SearchService
from app.services.verification import VerificationService

#: Content chosen to trip several Phase 1 rules and yield claims worth checking.
CONTENT = (
    "Acme Capital Advisors guarantees 30% monthly returns with zero risk. "
    "Act now: pay the 50000 INR activation fee today only to reserve your slot. "
    "Download the app from https://tinyurl.com/acme-app to get started."
)


class FixtureProvider(SearchProvider):
    """Returns one regulator-hosted result for any query, and records queries.

    Stands in for SerpAPI so the pass runs offline. Because Phase 4 drives the
    searches, the recorded queries are exactly the ones verification issued.
    """

    name = "fixture"

    def __init__(self, results: tuple[SearchResult, ...], available: bool = True) -> None:
        self._results = results
        self._available = available
        self.queries: list[str] = []

    def is_available(self) -> bool:
        """Whether this provider claims to be usable."""
        return self._available

    def search(self, query: str, max_results: int) -> SearchResponse:
        """Record the query and return the fixture results."""
        self.queries.append(query)
        if not self._available:
            return SearchResponse(
                query=query,
                results=(),
                provider=self.name,
                status=SearchStatus.UNAVAILABLE,
                error_code="SEARCH_NOT_CONFIGURED",
                provider_query_sent=False,
                warnings=("External search is unavailable in this pass.",),
            )
        return SearchResponse(
            query=query,
            results=self._results[:max_results],
            provider=self.name,
            status=SearchStatus.OK,
            provider_query_sent=True,
        )


def regulator_page() -> SearchResult:
    """A result hosted on the regulator's domain."""
    return SearchResult(
        title="Acme Capital Advisors - intermediary record",
        url="https://www.sebi.gov.in/intermediaries/acme-capital-advisors",
        snippet="Acme Capital Advisors is registered as an investment adviser.",
        position=1,
    )


def context_with(provider: SearchProvider) -> GraphContext:
    """Build a real context whose search is served by `provider`.

    Every service is the real one. Only the search provider is swapped, and a
    `RecordingSearchService` wraps it so the evidence stage can cite the query
    behind each document without the graph issuing a search of its own.
    """
    settings = get_settings()
    recorder = RecordingSearchService(
        SearchService(settings=settings, provider=provider)
    )
    return GraphContext(
        dependencies=GraphDependencies(
            extraction_service=ExtractionService(settings=settings),
            red_flag_engine=RedFlagEngine(settings=settings),
            verification_service=VerificationService(
                settings=settings, search_service=recorder
            ),
            evidence_service=EvidenceService(),
            risk_service=RiskService(settings=settings),
            search_recorder=recorder,
        )
    )


def print_state(label: str, state: dict) -> None:
    """Print one run's timeline, limitations and assessment."""
    print("=" * 76)
    print(label)
    print("=" * 76)

    summary = investigation_summary(state)
    print(f"investigation id : {summary['investigation_id']}")
    print(f"last stage       : {summary['current_stage']}")
    print(
        f"found            : {summary['claims']} claim(s), "
        f"{summary['entities']} entit(ies), {summary['red_flags']} pattern(s)"
    )
    print(
        f"records          : {summary['verification_results']} verification(s), "
        f"{summary['evidence_items']} evidence item(s)"
    )
    print()

    print("timeline:")
    for event in state.get("timeline") or ():
        print(f"  {event.stage.value:13} {event.status.value:9} {event.message}")

    if state.get("warnings"):
        print()
        print("limitations:")
        for warning in state["warnings"]:
            print(f"  [{warning.code}] {warning.message}")

    if state.get("errors"):
        print()
        print("failures:")
        for error in state["errors"]:
            print(f"  [{error.code}] {error.message}")

    assessment = state.get("risk_assessment")
    if assessment is not None:
        print()
        print(f"risk score       : {assessment.risk_score} "
              f"(from {assessment.raw_score}), level {assessment.risk_level.value}")
        for factor in assessment.factors:
            marker = " " if factor.is_scoring else "-"
            print(f"  {marker} {factor.contribution:>3}  {factor.label}")
        for caveat in assessment.warnings:
            print(f"  note: {caveat}")
    else:
        print()
        print("risk score       : not reported for this run")


def main() -> int:
    """Run the three passes. Returns a process exit code."""
    provider = FixtureProvider((regulator_page(),), available=True)
    context = context_with(provider)
    print_state("PASS 1 - full pipeline, offline, real services", run_investigation(CONTENT, context=context))
    print(f"\nsearch queries issued by verification: {len(provider.queries)}")
    for query in provider.queries:
        print(f"  - {query}")

    degraded = context_with(FixtureProvider((), available=False))
    print()
    print_state(
        "PASS 2 - search unavailable, investigation still completes",
        run_investigation(CONTENT, context=degraded),
    )

    print()
    print_state(
        "PASS 3 - empty submission is rejected, not analysed",
        run_investigation("   ", context=context),
    )

    print("=" * 76)
    print(
        "The graph decided nothing. Every finding above came from Phase 1, Phase 4 "
        "or Phase 6; the graph only decided the order and recorded what happened."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())