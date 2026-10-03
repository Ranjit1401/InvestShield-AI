"""Fakes and builders for the investigation graph tests (Phase 7).

Two kinds of helper live here, and the distinction is the point of the suite:

- **`Recording*` fakes** stand in for a service and remember what they were
  called with. They exist for the *call contract* tests: that each node passes
  the right objects to the right service, and passes nothing it should not.
- **`real_*` builders** wire the genuine Phase 1-6 services with a fixture
  search provider and no API keys. They exist for the *behaviour* tests, so that
  what the graph is asserted to produce is what the real pipeline produces rather
  than what a hand-written stub was told to return.

Every fake's `raises` attribute is a deliberate escape hatch: each test that
needs a service to fail names the failure it is simulating, so an unexpected
exception in the suite is a bug rather than a silently absorbed condition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.config import Settings
from app.graph.context import GraphContext, GraphDependencies, RecordingSearchService
from app.schemas.claims import Claim, ClaimType
from app.schemas.common import EvidenceSpan
from app.schemas.entities import Entity, EntityType, normalize_entity_name
from app.schemas.evidence import EvidenceBundleResponse
from app.schemas.extraction import ExtractionMode, ExtractionResult
from app.schemas.ocr import ImageDocument, ImageSource, ImageUpload
from app.schemas.pdf import PdfDocument, PdfSource, PdfUpload
from app.schemas.red_flags import RedFlag, RedFlagCode
from app.schemas.risk import RiskAssessment, RiskLevel
from app.schemas.search import SearchResponse, SearchResult, SearchStatus
from app.schemas.verification import VerificationResponse, VerificationResult, VerificationStatus
from app.services.evidence import EvidenceService
from app.services.extraction_service import ExtractionService
from app.services.red_flag_engine import RedFlagEngine
from app.services.risk import RiskService
from app.services.search import SearchProvider, SearchService
from app.services.verification import VerificationService

#: A fixed instant, so a frozen clock makes two runs byte-identical.
FIXED_INSTANT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def fixed_clock() -> datetime:
    """Return the same instant on every call.

    Lets a determinism test compare two whole states, timestamps included,
    instead of comparing everything except the metadata.
    """
    return FIXED_INSTANT


class RecordingSearchProvider(SearchProvider):
    """A search provider returning fixed results for every query.

    Args:
        results: Results returned for any query.
        available: What `is_available` reports.
        status: The status of every response.
    """

    name = "recording"

    def __init__(
        self,
        results: tuple[SearchResult, ...] = (),
        *,
        available: bool = True,
        status: SearchStatus = SearchStatus.OK,
    ) -> None:
        self._results = results
        self._available = available
        self._status = status
        self.queries: list[str] = []

    def is_available(self) -> bool:
        """Whether this provider claims to be usable."""
        return self._available

    def search(self, query: str, max_results: int) -> SearchResponse:
        """Record the query and return the fixed results."""
        self.queries.append(query)
        return SearchResponse(
            query=query,
            results=self._results[:max_results],
            provider=self.name,
            status=self._status,
            provider_query_sent=True,
        )


# -- recording fakes -----------------------------------------------------


@dataclass
class RecordingExtractionService:
    """Stands in for Phase 2 and remembers every call.

    Attributes:
        result: What `extract` returns.
        raises: Exception to raise instead, simulating a contract violation.
        calls: Every `raw_text` the node passed, in order.
    """

    result: ExtractionResult = field(default_factory=ExtractionResult)
    raises: Exception | None = None
    calls: list[str | None] = field(default_factory=list)

    def extract(self, raw_text: str | None) -> ExtractionResult:
        """Record the argument and return the configured result."""
        self.calls.append(raw_text)
        if self.raises is not None:
            raise self.raises
        return self.result


@dataclass
class RecordingRedFlagEngine:
    """Stands in for Phase 1 and remembers every call.

    Attributes:
        flags: What `detect` returns.
        raises: Exception to raise instead.
        calls: Every text the node passed, in order.
    """

    flags: tuple[RedFlag, ...] = ()
    raises: Exception | None = None
    calls: list[str | None] = field(default_factory=list)

    def detect(self, text: str | None) -> list[RedFlag]:
        """Record the argument and return the configured flags."""
        self.calls.append(text)
        if self.raises is not None:
            raise self.raises
        return list(self.flags)


@dataclass
class RecordingVerificationService:
    """Stands in for Phase 4 and remembers every call.

    Attributes:
        response: What `verify_claims` returns.
        raises: Exception to raise instead.
        calls: `(claims, entities)` for each call, in order.
        available: What the `available` property reports.
    """

    response: VerificationResponse = field(default_factory=VerificationResponse)
    raises: Exception | None = None
    calls: list[tuple[tuple[Claim, ...], tuple[Entity, ...]]] = field(
        default_factory=list
    )
    available: bool = True

    def verify_claims(
        self, claims: tuple[Claim, ...], entities: tuple[Entity, ...] = ()
    ) -> VerificationResponse:
        """Record the arguments and return the configured response."""
        self.calls.append((tuple(claims), tuple(entities)))
        if self.raises is not None:
            raise self.raises
        return self.response


@dataclass
class RecordingEvidenceService:
    """Stands in for Phase 5 and remembers every call.

    Attributes:
        bundle: What `build_all` returns.
        raises: Exception to raise instead.
        calls: Every argument set, in order.
    """

    bundle: EvidenceBundleResponse = field(default_factory=EvidenceBundleResponse)
    raises: Exception | None = None
    calls: list[dict[str, object]] = field(default_factory=list)

    def build_all(
        self,
        claims: tuple[Claim, ...],
        verification: VerificationResponse,
        results_by_claim: dict[str, tuple[SearchResult, ...]] | None = None,
        entities: tuple[Entity, ...] = (),
        *,
        responses: tuple[object, ...] = (),
    ) -> EvidenceBundleResponse:
        """Record the arguments and return the configured bundle."""
        self.calls.append(
            {
                "claims": tuple(claims),
                "verification": verification,
                "results_by_claim": results_by_claim or {},
                "entities": tuple(entities),
                "responses": tuple(responses),
            }
        )
        if self.raises is not None:
            raise self.raises
        return self.bundle


@dataclass
class RecordingRiskService:
    """Stands in for Phase 6 and remembers every call.

    Attributes:
        assessment: What `assess_batch` returns.
        raises: Exception to raise instead.
        calls: Every argument set, in order.
    """

    assessment: RiskAssessment = field(
        default_factory=lambda: RiskService().assess()
    )
    raises: Exception | None = None
    calls: list[dict[str, object]] = field(default_factory=list)

    def assess_batch(
        self,
        claims: tuple[Claim, ...],
        verification_results: tuple[VerificationResult, ...],
        evidence: tuple[object, ...] = (),
        red_flags: tuple[RedFlag, ...] = (),
    ) -> RiskAssessment:
        """Record the arguments and return the configured assessment."""
        self.calls.append(
            {
                "claims": tuple(claims),
                "verification_results": tuple(verification_results),
                "evidence": tuple(evidence),
                "red_flags": tuple(red_flags),
            }
        )
        if self.raises is not None:
            raise self.raises
        return self.assessment


def ocr_document(
    text: str = "",
    *,
    limitation: str | None = None,
    error_type: str | None = None,
    truncated: bool = False,
    filename: str | None = "screenshot.png",
    content_type: str = "image/png",
) -> ImageDocument:
    """Build an `ImageDocument` a fake OCR service can return.

    The genuine service decodes the image and drives Tesseract, so a
    test that needs a particular recognition outcome — unavailable,
    failed, empty, truncated — would need an engine to cooperate. This
    builder produces the document directly, so each outcome is a
    deliberate input rather than a coincidence of the environment.

    Args:
        text: The recovered text. Empty by default, which the graph
            reads as "nothing readable was recovered".
        limitation: The degradation code, when recognition could not
            run at all (`OCR_UNAVAILABLE` or `OCR_FAILED`).
        error_type: The exception class, carried only with the
            `OCR_FAILED` limitation.
        truncated: Whether `text` was cut at the character budget.
        filename: The submitted filename, for the provenance record.
        content_type: The declared media type.

    Returns:
        An `ImageDocument` for a fake to return.
    """
    return ImageDocument(
        source=ImageSource(
            filename=filename,
            content_type=content_type,
            byte_size=0,
            ocr_language="eng",
            text_recovered=bool(text.strip()),
            processed_at=FIXED_INSTANT,
        ),
        text=text,
        truncated=truncated,
        limitation=limitation,
        error_type=error_type,
    )


@dataclass
class RecordingOcrService:
    """Stands in for Phase 13's OCR service and remembers every call.

    The genuine service owns decoding the image and driving the
    engine. This fake returns one fixed document, so a test can drive
    each recognition outcome the graph maps — unavailable, failed,
    empty, truncated — without an engine, and can assert that the
    input stage handed the service exactly the submitted bytes.

    Attributes:
        document: What `extract` returns.
        calls: Every `ImageUpload` the node passed, in order.
    """

    document: ImageDocument = field(default_factory=ocr_document)
    calls: list[ImageUpload] = field(default_factory=list)

    def extract(self, upload: ImageUpload) -> ImageDocument:
        """Record the argument and return the configured document."""
        self.calls.append(upload)
        return self.document


def pdf_document(
    text: str = "",
    *,
    limitation: str | None = None,
    error_type: str | None = None,
    truncated: bool = False,
    filename: str | None = "document.pdf",
    content_type: str = "application/pdf",
    page_count: int | None = 1,
    pages_processed: int | None = 1,
) -> PdfDocument:
    """Build a `PdfDocument` a fake PDF service can return.

    The genuine service parses the PDF and reads its text, so a
    test that needs a particular extraction outcome — unavailable,
    failed, empty, truncated, page-capped — would need a document
    to cooperate. This builder produces the document directly, so
    each outcome is a deliberate input rather than a coincidence of
    the environment.

    Args:
        text: The recovered text. Empty by default, which the graph
            reads as "nothing readable was recovered".
        limitation: The degradation code, when extraction could not
            run at all (`PDF_UNAVAILABLE` or `PDF_EXTRACTION_FAILED`).
        error_type: The exception class, carried only with the
            `PDF_EXTRACTION_FAILED` limitation.
        truncated: Whether `text` was cut at the character budget.
        filename: The submitted filename, for the provenance record.
        content_type: The declared media type.
        page_count: The number of pages in the document, for the
            provenance record.
        pages_processed: The number of pages text was read from, for
            the provenance record. A value below `page_count` is how
            the graph records that later pages were skipped.

    Returns:
        A `PdfDocument` for a fake to return.
    """
    return PdfDocument(
        source=PdfSource(
            filename=filename,
            content_type=content_type,
            detected_content_type="application/pdf",
            format="PDF",
            byte_size=0,
            page_count=page_count,
            pages_processed=pages_processed,
            text_recovered=bool(text.strip()),
            processed_at=FIXED_INSTANT,
        ),
        text=text,
        truncated=truncated,
        limitation=limitation,
        error_type=error_type,
    )


@dataclass
class RecordingPdfService:
    """Stands in for Phase 14's PDF service and remembers every call.

    The genuine service owns parsing the PDF and reading its text.
    This fake returns one fixed document, so a test can drive each
    extraction outcome the graph maps — unavailable, failed, empty,
    truncated, page-capped — without a PDF library, and can assert
    that the input stage handed the service exactly the submitted
    bytes.

    Attributes:
        document: What `extract` returns.
        calls: Every `PdfUpload` the node passed, in order.
    """

    document: PdfDocument = field(default_factory=pdf_document)
    calls: list[PdfUpload] = field(default_factory=list)

    def extract(self, upload: PdfUpload) -> PdfDocument:
        """Record the argument and return the configured document."""
        self.calls.append(upload)
        return self.document


# -- builders ------------------------------------------------------------


def build_entity(name: str = "Acme Capital Advisors", entity_id: str = "entity_001") -> Entity:
    """Build an entity standing in for the party making the claims."""
    return Entity(
        id=entity_id,
        name=name,
        entity_type=EntityType.COMPANY,
        normalized_name=normalize_entity_name(name),
        confidence=0.95,
        evidence_span=EvidenceSpan(start=0, end=len(name), text=name),
    )


def build_claim(
    text: str = "Acme Capital Advisors is SEBI registered.",
    claim_id: str = "claim_001",
    claim_type: ClaimType = ClaimType.REGULATORY_STATUS,
) -> Claim:
    """Build a claim with a span covering its own text."""
    return Claim(
        id=claim_id,
        text=text,
        claim_type=claim_type,
        confidence=0.95,
        evidence_span=EvidenceSpan(start=0, end=len(text), text=text),
    )


def build_flag(
    code: RedFlagCode = RedFlagCode.FAKE_REGULATORY_CLAIM,
    *,
    start: int = 0,
    end: int = 40,
    matched_text: str = "guaranteed returns",
) -> RedFlag:
    """Build a placed red flag with a span into the submitted text.

    Delegates to Phase 6's `make_flag` so the rule wording comes from Phase 1's
    catalogue rather than being restated here.
    """
    from tests.risk_factories import make_flag

    return make_flag(code, start=start, end=end, matched_text=matched_text)


def sebi_result(
    path: str = "/intermediaries/acme",
    title: str = "Acme Capital Advisors registration",
    snippet: str = "Acme Capital Advisors is registered as an investment adviser.",
    position: int = 1,
) -> SearchResult:
    """Build a result hosted on the regulator's domain."""
    return SearchResult(
        title=title,
        url=f"https://www.sebi.gov.in{path}",
        snippet=snippet,
        position=position,
    )


def offline_settings() -> Settings:
    """Settings with both API keys cleared, so nothing can reach the network.

    `_env_file=None` matters here: a developer's local `.env` would otherwise
    supply a real `SERPAPI_KEY` or `GROQ_API_KEY`, and a test that then made a
    network call would look like a passing test while quietly reaching the
    internet. Ignoring the file makes "fully offline" a property of the tests
    rather than of the machine they run on.
    """
    return Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite:///:memory:",
        groq_api_key="",
        serpapi_key="",
        log_level="WARNING",
    )


def fake_dependencies(
    *,
    extraction: RecordingExtractionService | None = None,
    red_flags: RecordingRedFlagEngine | None = None,
    verification: RecordingVerificationService | None = None,
    evidence: RecordingEvidenceService | None = None,
    risk: RecordingRiskService | None = None,
    recorder: RecordingSearchService | None = None,
) -> GraphDependencies:
    """Build dependencies whose every service records its calls.

    Args:
        extraction: Extraction fake. A default one is built if omitted.
        red_flags: Red flag fake. A default one is built if omitted.
        verification: Verification fake. A default one is built if omitted.
        evidence: Evidence fake. A default one is built if omitted.
        risk: Risk fake. A default one is built if omitted.
        recorder: Optional search recorder, for asserting that a graph built
            without recording says so.

    Returns:
        A `GraphDependencies` of fakes, safe to drive offline.
    """
    return GraphDependencies(
        extraction_service=extraction or RecordingExtractionService(),
        red_flag_engine=red_flags or RecordingRedFlagEngine(),
        verification_service=verification or RecordingVerificationService(),
        evidence_service=evidence or RecordingEvidenceService(),
        risk_service=risk or RecordingRiskService(),
        search_recorder=recorder,
    )


def real_dependencies(
    settings: Settings | None = None,
    *,
    provider: SearchProvider | None = None,
) -> tuple[GraphDependencies, RecordingSearchProvider]:
    """Build dependencies from the genuine Phase 1-6 services, offline.

    Extraction is the real `ExtractionService`, which falls back to deterministic
    patterns when no LLM key is configured, so it needs no network. Search is
    served by a fixture provider rather than SerpAPI.

    Args:
        settings: Settings to use. Defaults to keyless test settings.
        provider: Search provider override. Defaults to a fixture provider that
            returns one regulator-hosted result.

    Returns:
        The dependencies, and the provider so a test can inspect the queries
        Phase 4 actually issued.
    """
    resolved = settings or offline_settings()
    search_provider = provider or RecordingSearchProvider((sebi_result(),))
    recorder = RecordingSearchService(SearchService(settings=resolved, provider=search_provider))

    dependencies = GraphDependencies(
        extraction_service=ExtractionService(settings=resolved),
        red_flag_engine=RedFlagEngine(settings=resolved),
        verification_service=VerificationService(
            settings=resolved, search_service=recorder
        ),
        evidence_service=EvidenceService(),
        risk_service=RiskService(settings=resolved),
        search_recorder=recorder,
    )
    return dependencies, search_provider


def offline_context(
    dependencies: GraphDependencies | None = None,
    *,
    clock=fixed_clock,
) -> GraphContext:
    """Wrap dependencies in a context with a frozen clock.

    Args:
        dependencies: Dependencies to wrap. Real ones are built if omitted.
        clock: The clock callable.

    Returns:
        A `GraphContext` whose two runs are byte-identical.
    """
    resolved = dependencies
    if resolved is None:
        resolved, _ = real_dependencies()
    return GraphContext(dependencies=resolved, clock=clock)


#: Content that trips several Phase 1 rules and yields extractable claims.
MESSY_CONTENT = (
    "Acme Capital Advisors guarantees 30% monthly returns with no risk. "
    "Act now: pay the 50000 INR activation fee today only to reserve your slot."
)


def make_assessment(
    score: int = 0,
    level: RiskLevel = RiskLevel.LOW,
) -> RiskAssessment:
    """Build a minimal valid assessment for a fake risk service to return."""
    return RiskService().assess()


def verification_response(
    *results: VerificationResult,
    warnings: tuple[str, ...] = (),
) -> VerificationResponse:
    """Build a `VerificationResponse` from results, letting counts recompute."""
    return VerificationResponse(results=tuple(results), warnings=warnings)


def result_for(
    claim: Claim,
    status: VerificationStatus = VerificationStatus.UNVERIFIED,
    *,
    reason_code: str = "ZERO_RESULTS",
    queries: tuple[str, ...] = (),
    matched_result_ids: tuple[str, ...] = (),
    source_ids: tuple[str, ...] = (),
) -> VerificationResult:
    """Build a verification result anchored to `claim`.

    Args:
        claim: The claim the result belongs to.
        status: The verification status.
        reason_code: Phase 4's reason code.
        queries: Queries Phase 4 recorded as issued.
        matched_result_ids: Result ids Phase 4 recorded as examined.
        source_ids: Source ids the result cites. Required for `VERIFIED` and
            `CONTRADICTED`, which Phase 4 refuses to report without the sources
            they relied on.

    Returns:
        A `VerificationResult` for the claim.
    """
    return VerificationResult(
        claim_id=claim.id,
        claim_type=claim.claim_type,
        status=status,
        reason="Fixture reason for graph tests.",
        reason_code=reason_code,
        confidence=0.5,
        queries=queries,
        matched_result_ids=matched_result_ids,
        source_ids=source_ids,
    )


def extraction_with(
    claims: tuple[Claim, ...] = (),
    entities: tuple[Entity, ...] = (),
    *,
    mode: ExtractionMode = ExtractionMode.LLM,
    warnings: tuple[str, ...] = (),
    normalized_text: str = "",
) -> ExtractionResult:
    """Build an `ExtractionResult` for a fake extraction service to return."""
    return ExtractionResult(
        claims=claims,
        entities=entities,
        extraction_mode=mode,
        processing_warnings=warnings,
        normalized_text=normalized_text,
    )


__all__ = [
    "FIXED_INSTANT",
    "MESSY_CONTENT",
    "RecordingEvidenceService",
    "RecordingExtractionService",
    "RecordingOcrService",
    "RecordingPdfService",
    "RecordingRedFlagEngine",
    "RecordingRiskService",
    "RecordingSearchProvider",
    "RecordingVerificationService",
    "build_claim",
    "build_entity",
    "build_flag",
    "extraction_with",
    "fake_dependencies",
    "fixed_clock",
    "make_assessment",
    "ocr_document",
    "offline_context",
    "offline_settings",
    "pdf_document",
    "real_dependencies",
    "result_for",
    "sebi_result",
    "verification_response",
]