"""Dependency injection for the investigation graph (Phase 7).

Two objects live here, and the distinction matters:

- **`GraphDependencies`** holds the five Phase 2-6 services the graph calls.
  Nothing in the graph constructs a service at call time, so a test can inject
  fakes and drive the whole pipeline offline.
- **`GraphContext`** wraps those dependencies with the run's clock and is what
  LangGraph passes to each node as its runtime context.

`GraphDependencies` is a frozen dataclass rather than a container lookup so that a
missing dependency is a construction-time `TypeError` instead of an
`AttributeError` three stages into a run.

## Why the search recorder exists

Phase 4's `VerificationService` performs the searches and then **discards the
`SearchResponse` objects**, because nothing downstream ever asked for them.
Phase 5's `EvidenceService.build_all` needs exactly those responses to know which
query surfaced which document. This is the one seam where the two phases do not
line up.

The obvious fix — have the graph run the searches itself — is what this phase
explicitly must not do: it would duplicate every network call, and the second
copy could rank differently from the first, putting evidence in front of a
verification that never saw it (D-023).

`RecordingSearchService` is the alternative. It wraps the real `SearchService`,
delegates every call unchanged, and keeps the responses it saw. The searches are
still Phase 4's, still issued once, still the only ones that reach the evidence
stage. The graph adds no provider, builds no query, and makes no network call of
its own — it reads a record of calls it already owns.

Responses are mapped back to claims through `VerificationResult.queries`, which
Phase 4 populates from the responses it actually received. That mapping is the
only new inference in this module, and it is a lookup, not a decision: a query
Phase 4 never issued cannot appear in the result, so no response can be attached
to a claim that did not cause it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.config import Settings, get_settings
from app.schemas.search import SearchResponse, SearchResult
from app.schemas.verification import VerificationResult
from app.services.evidence import EvidenceService
from app.services.extraction_service import ExtractionService
from app.services.ocr_service import OCRService
from app.services.pdf_service import PDFService
from app.services.red_flag_engine import RedFlagEngine
from app.services.risk import RiskService
from app.services.search import SearchService
from app.services.url_fetch import URLFetchService
from app.services.verification import VerificationService
from app.services.website_extractor import WebsiteContentExtractor

__all__ = [
    "GraphContext",
    "GraphDependencies",
    "RecordingSearchService",
    "build_default_context",
    "utc_now",
]


def utc_now() -> datetime:
    """Return the current UTC time as an aware `datetime`.

    The graph's default clock. Injectable so tests can freeze time and compare
    two runs including their timestamps, rather than excluding metadata from the
    comparison and hoping the rest is deterministic.
    """
    return datetime.now(timezone.utc)


class RecordingSearchService:
    """A pass-through decorator that keeps the responses a search returned.

    Satisfies the same surface `VerificationService` uses from `SearchService`:
    `search`, `available` and `provider_name`. It is a decorator rather than a
    subclass because it must forward to an *existing* configured service, and
    building a second `SearchService` would re-resolve the provider and risk a
    second, differently-configured one.

    Recording is a side effect of delegation, not a separate step: there is no
    way to search through this object without the response being kept.

    Attributes:
        _inner: The service every call is delegated to.
        _responses: Responses seen so far, in call order.
    """

    def __init__(self, inner: SearchService) -> None:
        """Wrap a search service.

        Args:
            inner: The service to delegate to. Never `None`, so an investigation
                cannot silently gain an unrecorded search path.
        """
        self._inner = inner
        self._responses: list[SearchResponse] = []

    @property
    def inner(self) -> SearchService:
        """The wrapped service."""
        return self._inner

    @property
    def available(self) -> bool:
        """Whether the wrapped service can search right now."""
        return self._inner.available

    @property
    def provider_name(self) -> str:
        """Name of the wrapped service's provider."""
        return self._inner.provider_name

    @property
    def responses(self) -> tuple[SearchResponse, ...]:
        """Every response recorded, in call order."""
        return tuple(self._responses)

    def clear(self) -> None:
        """Discard recorded responses.

        Called before each verification stage so results from an earlier claim
        batch cannot leak into a later one.
        """
        self._responses.clear()

    def search(self, query: str | None, max_results: int | None = None) -> SearchResponse:
        """Delegate one search and record its response.

        Args:
            query: Raw query text, forwarded unchanged.
            max_results: Desired result count, forwarded unchanged.

        Returns:
            The wrapped service's `SearchResponse`, recorded and returned
            unchanged. This method never raises, matching the wrapped contract.
        """
        response = self._inner.search(query, max_results)
        self._responses.append(response)
        return response

    def results_by_claim(
        self, verification_results: tuple[VerificationResult, ...]
    ) -> dict[str, tuple[SearchResult, ...]]:
        """Group recorded results by the claim whose queries retrieved them.

        Phase 4 records the queries it issued for each claim, and each result
        carries the query that surfaced it. Pairing the two reconstructs the
        per-claim grouping Phase 5 expects.

        A query shared by two claims contributes its results to both, because
        both claims were genuinely assessed against those documents. That is a
        property of the search, not an inference this method makes.

        Results are de-duplicated within a claim by canonical URL, keeping the
        first occurrence, so a document found by two of a claim's queries appears
        once — the same treatment Phase 3 and Phase 5 already give it.

        Args:
            verification_results: Phase 4 results for the investigation.

        Returns:
            Claim id to the results its own queries retrieved. A claim with no
            queries, or whose responses were not recorded, maps to an empty
            tuple, which Phase 5 reports honestly rather than as an error.
        """
        by_query: dict[str, tuple[SearchResult, ...]] = {}
        for response in self._responses:
            query = response.query.strip()
            if not query or query in by_query:
                continue
            by_query[query] = tuple(response.results)

        grouped: dict[str, tuple[SearchResult, ...]] = {}
        for result in verification_results:
            collected: list[SearchResult] = []
            seen: set[str] = set()
            for query in result.queries:
                for item in by_query.get(query.strip(), ()):
                    key = item.canonical_url or item.url
                    if key in seen:
                        continue
                    seen.add(key)
                    collected.append(item)
            grouped[result.claim_id] = tuple(collected)
        return grouped


@dataclass(frozen=True)
class GraphDependencies:
    """The five services the investigation graph orchestrates.

    Frozen so a run cannot swap a service halfway through and produce a report
    assembled from two different engines. Each field is required, so a partially
    wired graph fails to build rather than failing mid-run.

    Attributes:
        extraction_service: Phase 2 `ExtractionService`.
        red_flag_engine: Phase 1 `RedFlagEngine`.
        verification_service: Phase 4 `VerificationService`. In the default
            context this wraps a `RecordingSearchService`, so Phase 5 can be
            given the searches Phase 4 actually performed.
        evidence_service: Phase 5 `EvidenceService`.
        risk_service: Phase 6 `RiskService`.
        search_recorder: The recorder attached to `verification_service`, or
            `None` when no recorder is installed. Absent recorder means the
            evidence stage cannot see which query surfaced which document, which
            the graph reports as a limitation rather than hiding.
        url_fetch_service: Phase 12 `URLFetchService`, or `None` when the graph
            was built without URL support. Optional rather than required so that
            a text-only deployment — and the existing text-only tests — need no
            URL wiring, and so its absence is a value the input stage can report
            rather than an `AttributeError` mid-run.
        website_extractor: Phase 12 `WebsiteContentExtractor`, or `None`. Paired
            with `url_fetch_service`: the two are only useful together, and the
            input stage treats either one being absent the same way.
        ocr_service: Phase 13 `OCRService`, or `None` when the graph was
            built without image support. Optional rather than required for
            the same reason as `url_fetch_service`: a text-only graph needs
            no OCR wiring, and its absence is a value the input stage
            reports rather than an `AttributeError` mid-run.
        pdf_service: Phase 14 `PDFService`, or `None` when the graph was
            built without PDF support. Optional for the same reason as
            `ocr_service`: its absence is a value the input stage reports
            rather than an `AttributeError` mid-run.
    """

    extraction_service: ExtractionService
    red_flag_engine: RedFlagEngine
    verification_service: VerificationService
    evidence_service: EvidenceService
    risk_service: RiskService
    search_recorder: RecordingSearchService | None = None
    url_fetch_service: URLFetchService | None = None
    website_extractor: WebsiteContentExtractor | None = None
    ocr_service: OCRService | None = None
    pdf_service: PDFService | None = None

    @property
    def supports_url(self) -> bool:
        """Whether this graph can analyse a URL submission.

        Both services must be present: fetching without extracting would hand the
        pipeline raw bytes, and extracting without fetching has nothing to work
        on. Reporting one combined capability keeps the input stage from
        branching on two fields that must always agree.
        """
        return self.url_fetch_service is not None and self.website_extractor is not None

    @property
    def supports_image(self) -> bool:
        """Whether this graph can analyse an image submission.

        Unlike `supports_url`, this is a single service: recognising a
        screenshot's text is one concern, so one dependency decides the
        capability. Its absence means the input stage refuses the submission
        with a typed reason rather than reaching for a service that is not
        there.
        """
        return self.ocr_service is not None

    @property
    def supports_pdf(self) -> bool:
        """Whether this graph can analyse a PDF submission.

        Like `supports_image`, this is a single service: extracting a
        document's text is one concern, so one dependency decides the
        capability. Its absence means the input stage refuses the submission
        with a typed reason rather than reaching for a service that is not
        there.
        """
        return self.pdf_service is not None

    def clear_search_recording(self) -> None:
        """Discard recorded search responses, if a recorder is installed."""
        if self.search_recorder is not None:
            self.search_recorder.clear()


@dataclass(frozen=True)
class GraphContext:
    """What every node receives as its runtime context.

    LangGraph passes this as the node's second argument. Keeping dependencies and
    the clock here means the state carries only investigation data and never a
    live service, which is what keeps the state serialisable and the nodes
    testable in isolation.

    Attributes:
        dependencies: The services to call.
        clock: Returns the current time. Injectable so a test can make two runs
            byte-identical, timestamps included.
    """

    dependencies: GraphDependencies
    clock: Callable[[], datetime] = field(default=utc_now)


def build_default_context(
    settings: Settings | None = None,
    *,
    install_recorder: bool = True,
) -> GraphContext:
    """Build a context wired to the real Phase 2-6 services.

    This is the production entry point. Tests normally build a `GraphContext` by
    hand with fakes instead, which is what keeps the suite offline.

    Args:
        settings: Configuration for every service. Defaults to the application
            settings.
        install_recorder: Whether to wrap search in a `RecordingSearchService` so
            Phase 5 can be given the responses Phase 4 retrieved. Turning it off
            produces a graph that runs but cannot cite the query behind a
            document; it exists for callers that already track that themselves.

    Returns:
        A `GraphContext` whose verification service observes its own searches.
    """
    resolved = settings or get_settings()

    verification_service: VerificationService
    recorder: RecordingSearchService | None = None

    if install_recorder:
        recorder = RecordingSearchService(SearchService(settings=resolved))
        verification_service = VerificationService(
            settings=resolved, search_service=recorder
        )
    else:
        verification_service = VerificationService(settings=resolved)

    return GraphContext(
        dependencies=GraphDependencies(
            extraction_service=ExtractionService(settings=resolved),
            red_flag_engine=RedFlagEngine(settings=resolved),
            verification_service=verification_service,
            evidence_service=EvidenceService(),
            risk_service=RiskService(settings=resolved),
            search_recorder=recorder,
            url_fetch_service=URLFetchService(settings=resolved),
            website_extractor=WebsiteContentExtractor(settings=resolved),
            ocr_service=OCRService(settings=resolved),
            pdf_service=PDFService(settings=resolved),
        )
    )