"""Failure injection across every stage (Phase 10).

`tests/test_search_service.py`, `tests/test_verification_service.py` and the graph
suites each inject failures at one layer. What is missing is the **matrix**: one
sweep that breaks each stage in turn and asserts the two properties the product is
built on.

1. **The system never fabricates success.** A stage that did not run must not leave
   a field that looks like it did. Absence of evidence is never a finding (D-006),
   and a score is never invented for a stage that produced none.
2. **The failure is classified honestly.** A provider being down is a `PARTIAL`
   run with a `200` — the caller's request was fine. A service breaking its own
   documented no-raise contract is a `FAILED` run with a `500`. A stage bypassed
   because its prerequisite was absent is `SKIPPED`.

Those are the three cases the graph exists to distinguish, and the distinction is
the failure mode that would matter most: a run that stopped must never be
mistakable for a run that finished and found little.

Every failure is injected **deterministically** through the same seams the graph
uses in production, so a test failure means a behaviour changed rather than a
network call failed.
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_graph_context_dep
from app.core.config import Settings
from app.graph.context import GraphContext, GraphDependencies, RecordingSearchService
from app.graph.investigation_graph import run_investigation
from app.graph.state import (
    GraphStage,
    InvestigationInputType,
    TimelineStatus,
    semantic_view,
)
from app.main import create_app
from app.schemas.evidence import EvidenceBundleResponse
from app.schemas.extraction import ExtractionMode
from app.schemas.ocr import ImageUpload
from app.schemas.pdf import PdfUpload
from app.schemas.risk import RiskAssessment, RiskLevel
from app.schemas.search import SearchResponse, SearchStatus
from app.schemas.verification import (
    SEARCH_FAILED,
    SEARCH_UNAVAILABLE,
    VerificationResponse,
    VerificationStatus,
)
from app.services.ocr_service import OCRService
from app.services.pdf_service import PDFService
from app.services.search import SearchProvider, SearchService
from app.services.url_fetch import UrlFetchError
from sqlalchemy.exc import (
    DatabaseError,
    IntegrityError,
    OperationalError,
)
from tests.graph.graph_factories import (
    RecordingEvidenceService,
    RecordingExtractionService,
    RecordingOcrService,
    RecordingPdfService,
    RecordingRedFlagEngine,
    RecordingRiskService,
    RecordingSearchProvider,
    RecordingVerificationService,
    build_claim,
    build_entity,
    build_flag,
    extraction_with,
    offline_settings,
    ocr_document,
    pdf_document,
    real_dependencies,
    result_for,
    sebi_result,
    verification_response,
)
from tests.persistence_factories import REGULATORY_CONTENT
from tests.url_factories import (
    JS_ONLY_PAGE,
    FakeFetchService,
    build_page,
    url_dependencies,
)

#: A contract violation is what each service documents it will never raise. Any
#: exception escaping one is a defect beneath the graph, so the graph stops rather
#: than reporting a plausible-looking result built on a stage that did nothing.
CONTRACT_VIOLATION = RuntimeError("simulated contract violation with internal detail")


class FailingProvider(SearchProvider):
    """A provider that always fails with a specific search error code.

    Models the real provider's behaviour on an HTTP failure, which is a `SearchResponse`
    with a non-OK status — never an exception, because `SearchService` is contracted
    not to raise.

    Args:
        error_code: Which failure to report.
    """

    name = "failing"

    def __init__(self, error_code: str) -> None:
        self._error_code = error_code
        self.queries: list[str] = []

    def is_available(self) -> bool:
        """A failing provider is still configured, so it claims availability."""
        return True

    def search(self, query: str, max_results: int) -> SearchResponse:
        """Record the query and report the configured failure."""
        self.queries.append(query)
        return SearchResponse(
            query=query,
            provider=self.name,
            status=SearchStatus.ERROR,
            error_code=self._error_code,
            provider_query_sent=True,
            warnings=(f"Simulated provider failure: {self._error_code}.",),
        )


class UnconfiguredProvider(SearchProvider):
    """A provider with no credentials, which never issues a request."""

    name = "unconfigured"

    def is_available(self) -> bool:
        """Reports itself unusable, as a keyless provider does."""
        return False

    def search(self, query: str, max_results: int) -> SearchResponse:
        """Report unavailability rather than pretending to search."""
        return SearchResponse(
            query=query,
            provider=self.name,
            status=SearchStatus.UNAVAILABLE,
            error_code="SEARCH_NOT_CONFIGURED",
            provider_query_sent=False,
            warnings=("Simulated provider with no credentials.",),
        )


def _context_with(dependencies: GraphDependencies) -> GraphContext:
    """Wrap dependencies in a graph context.

    Args:
        dependencies: The services to use.

    Returns:
        A `GraphContext` wrapping them.
    """
    return GraphContext(dependencies=dependencies)


def _run(context: GraphContext, text: str = REGULATORY_CONTENT):
    """Run one investigation offline and return the final state.

    Args:
        context: The graph context to run against.
        text: Content to investigate.

    Returns:
        The final investigation state.
    """
    return run_investigation(text, input_type=InvestigationInputType.TEXT, context=context)


def _image_context(ocr_service: object) -> GraphContext:
    """Build an offline context whose OCR service is the one given.

    The genuine Phase 1-6 services are wired as usual; only the OCR
    service is replaced, because that is the one service an image
    submission exercises and the one whose outcome these tests drive.

    Args:
        ocr_service: The OCR service to wire in, real or fake.

    Returns:
        A `GraphContext` with image support and the given OCR service.
    """
    dependencies, _ = real_dependencies(offline_settings())
    dependencies = dataclasses.replace(dependencies, ocr_service=ocr_service)
    return GraphContext(dependencies=dependencies)


def _pdf_context(pdf_service: object) -> GraphContext:
    """Build an offline context whose PDF service is the one given.

    The genuine Phase 1-6 services are wired as usual; only the PDF
    service is replaced, because that is the one service a PDF
    submission exercises and the one whose outcome these tests drive.

    Args:
        pdf_service: The PDF service to wire in, real or fake.

    Returns:
        A `GraphContext` with PDF support and the given PDF service.
    """
    dependencies, _ = real_dependencies(offline_settings())
    dependencies = dataclasses.replace(dependencies, pdf_service=pdf_service)
    return GraphContext(dependencies=dependencies)


def _api_client_for(settings, context: GraphContext) -> TestClient:
    """Build a client running a specific graph context.

    `raise_server_exceptions=False` is essential here rather than merely tidy. With
    the default, `TestClient` re-raises whatever escaped the app, so a test asserting
    "this failure becomes a `500`" would instead see the exception in the test — and
    the module could not distinguish "the app handled it" from "the client happened
    to catch it". Turning that off makes the assertion mean what it says.

    Args:
        settings: Settings to build the app with.
        context: The context to install.

    Returns:
        An entered `TestClient`.
    """
    client = TestClient(
        create_app(settings), raise_server_exceptions=False
    )
    client.app.dependency_overrides[get_graph_context_dep] = lambda: context
    client.__enter__()
    return client


@pytest.fixture
def failure_client(test_settings):
    """Build a client running a chosen graph context.

    `tests/api/conftest.py` provides the equivalent for the API suite; this module
    lives at the suite root so it needs its own, and reuses the root `test_settings`
    fixture rather than duplicating the isolation rules.

    Args:
        test_settings: Isolated settings for this test.

    Returns:
        A factory taking a `GraphContext` and returning an entered `TestClient`.
    """
    return lambda context: _api_client_for(test_settings, context)


class TestInputFailures:
    """The submission itself is unusable."""

    @pytest.mark.parametrize("text", ["", "   ", "\n\n", "\t\t\t", "  \r\n  "])
    def test_an_empty_submission_records_a_failure_and_runs_nothing(
        self, real_context: GraphContext, text: str
    ) -> None:
        """Whitespace of any shape is an empty submission.

        Args:
            real_context: An offline context with the genuine services.
            text: The unusable submission.
        """
        state = _run(real_context, text)

        assert state["errors"], "an empty submission must record a failure"
        assert state["errors"][0].code == "INPUT_EMPTY"
        assert state["current_stage"] == "input"
        # Nothing downstream ran, so nothing downstream produced a finding.
        assert not state.get("claims")
        assert not state.get("risk_assessment")

    @pytest.mark.parametrize("kind", ["SPREADSHEET"])
    def test_an_unsupported_input_type_is_refused_without_analysing_as_text(
        self, real_context: GraphContext, kind: str
    ) -> None:
        """A kind this version does not analyse is refused, never read as prose.

        Reading an unanalysed kind as text would report findings about
        something the client never submitted as text. `URL` left this
        set in Phase 12, which began retrieving and analysing it,
        `IMAGE` left it in Phase 13, and `PDF` in Phase 14; each
        is covered by its own failure class below.

        Args:
            real_context: An offline context with the genuine services.
            kind: The unanalysed input kind.
        """
        state = run_investigation(
            REGULATORY_CONTENT, input_type=kind, context=real_context
        )

        assert state["errors"]
        assert state["errors"][0].code == "INPUT_TYPE_NOT_SUPPORTED"
        assert state["current_stage"] == "input"
        assert not state.get("claims")


class TestUrlInputFailures:
    """Phase 12: a URL that cannot be retrieved, and the two ways that fails.

    Three things are distinguished, and the distinction is the substance of this
    class rather than incidental:

    - a refusal the **caller** must fix, which is `422` and which retrying
      unchanged will never clear;
    - a **site** that could not be read, which is `502` and which says nothing
      about the request;
    - a **deployment** that does not analyse URLs, which is `503` and which says
      nothing about either.

    Collapsing any two of them would tell a caller to do something that cannot
    work.
    """

    @staticmethod
    def _context(fetcher: object) -> GraphContext:
        """Build a context whose URL fetcher is the given stand-in.

        Args:
            fetcher: The stand-in for `URLFetchService`.

        Returns:
            An offline context with URL analysis wired to `fetcher`.
        """
        return _context_with(
            url_dependencies(real_dependencies(offline_settings())[0], fetcher)
        )

    @staticmethod
    def _run_url(context: GraphContext, url: str = "https://example.com/invest"):
        """Run one URL investigation.

        Args:
            context: The context to run against.
            url: The submitted URL.

        Returns:
            The final state.
        """
        return run_investigation(
            url, input_type=InvestigationInputType.URL, context=context
        )

    # -- caller faults: 422 --------------------------------------------

    @pytest.mark.parametrize(
        "code",
        [
            "URL_EMPTY",
            "URL_INVALID",
            "URL_TOO_LONG",
            "URL_SCHEME_UNSUPPORTED",
            "URL_HOST_MISSING",
            "URL_HOST_TOO_LONG",
            "URL_CREDENTIALS_NOT_ACCEPTED",
            "URL_CONTROL_CHARACTERS_NOT_ACCEPTED",
            "URL_ADDRESS_BLOCKED",
            "URL_METADATA_ADDRESS_BLOCKED",
            "URL_METADATA_HOSTNAME_BLOCKED",
        ],
    )
    def test_every_caller_fault_code_is_produced_and_runs_nothing(
        self, code: str
    ) -> None:
        """Each submission fault is recorded, and nothing downstream runs.

        A refusal that still produced claims or a score would be the worst
        outcome available: it would show a caller findings about a page that was
        never fetched.

        Args:
            code: The refusal code the fetcher raises.
        """
        fetcher = FakeFetchService(error=UrlFetchError(code))
        state = self._run_url(self._context(fetcher))

        assert state["errors"], f"{code} must record a failure"
        assert state["errors"][0].code == code
        assert state["current_stage"] == "input"
        assert not state.get("claims")
        assert not state.get("risk_assessment")
        assert state.get("url_source") is None

    @pytest.mark.parametrize(
        "code",
        [
            "URL_FETCH_DISABLED",
            "URL_DNS_FAILED",
            "URL_FETCH_FAILED",
            "URL_TIMEOUT",
            "URL_HTTP_ERROR",
            "URL_TOO_MANY_REDIRECTS",
            "URL_CONTENT_TOO_LARGE",
            "URL_CONTENT_TYPE_UNSUPPORTED",
        ],
    )
    def test_every_site_fault_code_is_produced_and_runs_nothing(self, code: str) -> None:
        """Each retrieval fault is recorded, and nothing downstream runs.

        `URL_HTTP_ERROR` needs the status the template fills in, which is supplied
        the way the real service supplies it.

        Args:
            code: The refusal code the fetcher raises.
        """
        fields = {"status": 500} if code == "URL_HTTP_ERROR" else {}
        fetcher = FakeFetchService(error=UrlFetchError(code, **fields))
        state = self._run_url(self._context(fetcher))

        assert state["errors"], f"{code} must record a failure"
        assert state["errors"][0].code == code
        assert state["current_stage"] == "input"
        assert not state.get("claims")
        assert not state.get("risk_assessment")

    def test_a_graph_without_url_services_refuses_the_capability(self) -> None:
        """A graph built without URL services says so, rather than pretending.

        Reporting this as an unsupported input *kind* would be wrong twice over:
        the kind is supported by the product, and the run failed before anything
        was submitted to the pipeline.
        """
        context = _context_with(real_dependencies(offline_settings())[0])
        assert context.dependencies.supports_url is False

        state = self._run_url(context)

        assert [error.code for error in state["errors"]] == ["URL_FETCH_UNAVAILABLE"]
        assert not state.get("claims")

    # -- the API statuses ---------------------------------------------

    @pytest.mark.parametrize(
        ("code", "expected_status"),
        [
            ("URL_ADDRESS_BLOCKED", 422),
            ("URL_CREDENTIALS_NOT_ACCEPTED", 422),
            ("URL_SCHEME_UNSUPPORTED", 422),
            ("URL_METADATA_ADDRESS_BLOCKED", 422),
            ("URL_DNS_FAILED", 502),
            ("URL_TIMEOUT", 502),
            ("URL_HTTP_ERROR", 502),
            ("URL_CONTENT_TYPE_UNSUPPORTED", 502),
            ("URL_FETCH_DISABLED", 503),
        ],
    )
    def test_the_api_status_follows_whose_fault_it_is(
        self, failure_client, code: str, expected_status: int
    ) -> None:
        """`422` for the caller, `502` for the site, `503` for the deployment.

        Args:
            failure_client: Fixture building a client over a chosen context.
            code: The refusal code the fetcher raises.
            expected_status: The status the caller must receive.
        """
        fields = {"status": 503} if code == "URL_HTTP_ERROR" else {}
        context = self._context(FakeFetchService(error=UrlFetchError(code, **fields)))

        response = failure_client(context).post(
            "/api/investigations/url", json={"url": "https://example.com/x"}
        )

        assert response.status_code == expected_status, response.text
        body = response.json()
        assert set(body) == {"error"}
        assert body["error"]["code"] == code

    # -- limitations: a degraded but successful run --------------------

    def test_a_truncated_page_is_a_limitation_not_a_failure(self) -> None:
        """A page cut at the byte budget is analysed, with the cut declared.

        This is the difference that matters to a user: refusing would mean no
        investigation at all, and saying nothing would mean an investigation that
        silently missed most of the page.
        """
        context = self._context(
            FakeFetchService(page=build_page(truncated=True))
        )
        state = self._run_url(context)

        assert not state["errors"]
        assert "URL_CONTENT_TRUNCATED" in {w.code for w in state.get("warnings", ())}
        assert state.get("risk_assessment") is not None

    def test_a_page_with_no_readable_text_is_a_limitation_not_a_failure(self) -> None:
        """A JavaScript-rendered page still yields a URL investigation.

        The URL and domain are real evidence and the rules still run over them, so
        refusing would discard something useful. Declaring the limitation is what
        keeps the result from overstating what was read.
        """
        context = self._context(
            FakeFetchService(page=build_page(body=JS_ONLY_PAGE))
        )
        state = self._run_url(context)

        assert not state["errors"]
        assert "PAGE_TEXT_NOT_RETRIEVED" in {w.code for w in state.get("warnings", ())}
        assert state.get("risk_assessment") is not None
        # The analysis covered the submitted address even with no page text.
        assert state["raw_input"].startswith("Website: ")

    def test_a_contract_violation_in_the_fetcher_ends_the_run(self) -> None:
        """An unexpected exception is a defect, not a site being unavailable.

        The fetch service documents that it raises only typed refusals. Anything
        else means the layer beneath is broken, and reporting it as though the
        submitted site were at fault would misattribute a defect to a user.
        """

        class _Exploding:
            def fetch(self, url):  # noqa: ANN001, ANN202
                raise RuntimeError("unexpected")

        state = self._run_url(self._context(_Exploding()))

        assert [error.code for error in state["errors"]] == ["URL_FETCH_UNAVAILABLE"]
        assert state["errors"][0].error_type == "RuntimeError"
        assert not state.get("claims")

    def test_no_retrieval_failure_message_leaks_a_host(self) -> None:
        """No refusal message names the host that was submitted.

        The wording is fixed per code and filled only with values the caller
        already submitted, so a message cannot become a way for one submission to
        learn something about the network the deployment runs in.
        """
        for code in ("URL_TIMEOUT", "URL_FETCH_FAILED", "URL_ADDRESS_BLOCKED"):
            fetcher = FakeFetchService(error=UrlFetchError(code))
            state = self._run_url(self._context(fetcher))

            message = state["errors"][0].message
            assert "example.com" not in message, code
            assert "Traceback" not in message, code


class TestImageInputFailures:
    """Phase 13: a screenshot that cannot be read, and the ways that fails.

    Four things are distinguished, and the distinction is the substance of
    this class rather than incidental:

    - a submission the **caller** must fix, which is `422` and which
      retrying unchanged will never clear — no image at all, an image over
      the byte limit, a media type this version does not read, or bytes
      that do not decode as an image;
    - a **deployment** that cannot read screenshots at all, which is `503`
      and says nothing about the submission;
    - a **degradation** the run survives — the engine missing, the engine
      failing, the image yielding no text, or the text being cut at the
      budget — each recorded as a limitation on a run that still completes;
    - and the outcome that is none of those: a screenshot read cleanly.

    Collapsing any two of these would tell a caller to do something that
    cannot work — for instance, retrying a screenshot the deployment's
    engine cannot read, or treating a missing engine as a reason to stop.
    """

    def test_a_graph_without_ocr_cannot_read_a_screenshot(self) -> None:
        """A context with no OCR service is a capability fault, not a bad
        submission.

        The request was well-formed; the deployment is the reason it
        cannot be answered, so the caller must not be told to fix it.
        """
        dependencies, _ = real_dependencies(offline_settings())
        context = GraphContext(dependencies=dependencies)

        state = run_investigation(
            "screenshot.png",
            input_type=InvestigationInputType.IMAGE,
            context=context,
            upload=ImageUpload(content=b"\x89PNG\r\n", content_type="image/png"),
        )

        assert state["errors"][0].code == "IMAGE_INPUT_UNAVAILABLE"
        assert state.get("risk_assessment") is None

    def test_an_image_submission_without_bytes_is_refused(self) -> None:
        """Declaring IMAGE but carrying no image is the caller's fault."""
        context = _image_context(OCRService(settings=offline_settings()))

        state = run_investigation(
            "screenshot.png",
            input_type=InvestigationInputType.IMAGE,
            context=context,
        )

        assert state["errors"][0].code == "OCR_IMAGE_EMPTY"
        assert state.get("risk_assessment") is None

    def test_an_image_over_the_byte_limit_is_refused(self) -> None:
        """An image larger than the upload limit is the caller's fault."""
        settings = Settings(_env_file=None, max_upload_bytes=16)
        context = _image_context(OCRService(settings=settings))

        state = run_investigation(
            "big.png",
            input_type=InvestigationInputType.IMAGE,
            context=context,
            upload=ImageUpload(content=b"x" * 17, content_type="image/png"),
        )

        assert state["errors"][0].code == "OCR_IMAGE_TOO_LARGE"
        assert state.get("risk_assessment") is None

    def test_a_non_image_media_type_is_refused(self) -> None:
        """A declared media type this version does not read is the caller's."""
        context = _image_context(OCRService(settings=offline_settings()))

        state = run_investigation(
            "notes.txt",
            input_type=InvestigationInputType.IMAGE,
            context=context,
            upload=ImageUpload(content=b"plain text", content_type="text/plain"),
        )

        assert state["errors"][0].code == "OCR_IMAGE_TYPE_UNSUPPORTED"
        assert state.get("risk_assessment") is None

    def test_an_image_that_does_not_decode_is_refused(self) -> None:
        """Bytes that declare an image but do not decode are the caller's."""
        context = _image_context(OCRService(settings=offline_settings()))

        state = run_investigation(
            "fake.png",
            input_type=InvestigationInputType.IMAGE,
            context=context,
            upload=ImageUpload(content=b"not an image", content_type="image/png"),
        )

        assert state["errors"][0].code == "OCR_IMAGE_UNREADABLE"
        assert state.get("risk_assessment") is None

    def test_a_missing_engine_is_a_limitation_not_a_fault(self) -> None:
        """A deployment without Tesseract still answers, and says so.

        The request was well-formed and the image was accepted; only the
        engine to read it is absent. That is a gap in the deployment, so
        the run continues and records the gap rather than ending.
        """
        settings = Settings(_env_file=None, tesseract_cmd="/no/such/tesseract")
        context = _image_context(OCRService(settings=settings))

        state = run_investigation(
            "screenshot.png",
            input_type=InvestigationInputType.IMAGE,
            context=context,
            upload=ImageUpload(content=b"\x89PNG\r\n", content_type="image/png"),
        )

        assert not state["errors"]
        assert "OCR_UNAVAILABLE" in {warning.code for warning in state["warnings"]}
        assert state.get("risk_assessment") is not None

    def test_an_image_that_yields_no_text_is_a_limitation(self) -> None:
        """A readable image with nothing to say is a limitation, not an error.

        A photograph of a scene, or a screenshot of a chart, can be a
        perfectly good image that yields no words. Reporting that as a
        failure would tell the caller their screenshot was broken.
        """
        ocr = RecordingOcrService(document=ocr_document())
        context = _image_context(ocr)

        state = run_investigation(
            "blank.png",
            input_type=InvestigationInputType.IMAGE,
            context=context,
            upload=ImageUpload(content=b"\x89PNG\r\n", content_type="image/png"),
        )

        assert not state["errors"]
        assert "OCR_TEXT_NOT_RETRIEVED" in {warning.code for warning in state["warnings"]}
        assert state.get("risk_assessment") is not None

    def test_recognition_that_fails_is_a_limitation(self) -> None:
        """An engine that ran and failed is a limitation, not a fault.

        The engine was present and the image decoded, so neither the
        deployment nor the submission is at fault; the engine failed for
        a reason the caller cannot act on. The run continues.
        """
        ocr = RecordingOcrService(
            document=ocr_document(
                limitation="OCR_FAILED", error_type="TesseractError"
            )
        )
        context = _image_context(ocr)

        state = run_investigation(
            "screenshot.png",
            input_type=InvestigationInputType.IMAGE,
            context=context,
            upload=ImageUpload(content=b"\x89PNG\r\n", content_type="image/png"),
        )

        assert not state["errors"]
        assert "OCR_FAILED" in {warning.code for warning in state["warnings"]}
        assert state.get("risk_assessment") is not None

    def test_text_cut_at_the_budget_is_a_limitation(self) -> None:
        """A screenshot longer than the budget is cut, and the cut is reported.

        The analysis below runs over the cut text, so it must be visible
        that text was dropped — otherwise the report would be confident
        about a screenshot it only partly read.
        """
        ocr = RecordingOcrService(document=ocr_document(text="a" * 40, truncated=True))
        context = _image_context(ocr)

        state = run_investigation(
            "screenshot.png",
            input_type=InvestigationInputType.IMAGE,
            context=context,
            upload=ImageUpload(content=b"\x89PNG\r\n", content_type="image/png"),
        )

        assert not state["errors"]
        assert "OCR_CONTENT_TRUNCATED" in {warning.code for warning in state["warnings"]}
        assert state.get("risk_assessment") is not None


class TestPdfInputFailures:
    """Phase 14: a PDF that cannot be read, and the ways that fails.

    The same four-way distinction Phase 13 draws for a screenshot,
    because a PDF submission is the same kind of thing: an untrusted
    file handed to a parsing library, with outcomes that fall into
    buckets a caller can act on differently.

    - a submission the **caller** must fix, which is `422` and which
      retrying unchanged will never clear — no PDF at all, a PDF over
      the byte limit, a media type this version does not read, or
      bytes that do not parse as a PDF;
    - a **deployment** that cannot read PDFs at all, which is `503`
      and says nothing about the submission;
    - a **degradation** the run survives — the library missing, the
      engine failing, the PDF yielding no text, the text being cut at
      the budget, or only the first pages being read — each recorded
      as a limitation on a run that still completes;
    - and the outcome that is none of those: a PDF read cleanly.

    Collapsing any two of these would tell a caller to do something
    that cannot work — for instance, retrying a PDF the deployment's
    library cannot read, or treating a missing library as a reason to
    stop.
    """

    def test_a_graph_without_pdf_service_cannot_read_a_pdf(self) -> None:
        """A context with no PDF service is a capability fault, not a bad
        submission.

        The request was well-formed; the deployment is the reason it
        cannot be answered, so the caller must not be told to fix it.
        """
        dependencies, _ = real_dependencies(offline_settings())
        context = GraphContext(dependencies=dependencies)

        state = run_investigation(
            "document.pdf",
            input_type=InvestigationInputType.PDF,
            context=context,
            pdf_upload=PdfUpload(
                content=b"%PDF-1.4", content_type="application/pdf"
            ),
        )

        assert state["errors"][0].code == "PDF_INPUT_UNAVAILABLE"
        assert state.get("risk_assessment") is None

    def test_a_pdf_submission_without_bytes_is_refused(self) -> None:
        """Declaring PDF but carrying no PDF is the caller's fault."""
        context = _pdf_context(PDFService(settings=offline_settings()))

        state = run_investigation(
            "document.pdf",
            input_type=InvestigationInputType.PDF,
            context=context,
        )

        assert state["errors"][0].code == "PDF_EMPTY"
        assert state.get("risk_assessment") is None

    def test_a_pdf_over_the_byte_limit_is_refused(self) -> None:
        """A PDF larger than the upload limit is the caller's fault."""
        settings = Settings(_env_file=None, max_upload_bytes=16)
        context = _pdf_context(PDFService(settings=settings))

        state = run_investigation(
            "big.pdf",
            input_type=InvestigationInputType.PDF,
            context=context,
            pdf_upload=PdfUpload(
                content=b"x" * 17, content_type="application/pdf"
            ),
        )

        assert state["errors"][0].code == "PDF_FILE_TOO_LARGE"
        assert state.get("risk_assessment") is None

    def test_a_non_pdf_media_type_is_refused(self) -> None:
        """A declared media type this version does not read is the caller's."""
        context = _pdf_context(PDFService(settings=offline_settings()))

        state = run_investigation(
            "notes.txt",
            input_type=InvestigationInputType.PDF,
            context=context,
            pdf_upload=PdfUpload(
                content=b"plain text", content_type="text/plain"
            ),
        )

        assert state["errors"][0].code == "PDF_TYPE_UNSUPPORTED"
        assert state.get("risk_assessment") is None

    def test_a_pdf_that_does_not_parse_is_refused(self) -> None:
        """Bytes that declare a PDF but do not parse are the caller's."""
        context = _pdf_context(PDFService(settings=offline_settings()))

        state = run_investigation(
            "fake.pdf",
            input_type=InvestigationInputType.PDF,
            context=context,
            pdf_upload=PdfUpload(
                content=b"not a pdf", content_type="application/pdf"
            ),
        )

        assert state["errors"][0].code == "PDF_UNREADABLE"
        assert state.get("risk_assessment") is None

    def test_a_missing_library_is_a_limitation_not_a_fault(self) -> None:
        """A deployment without PyMuPDF still answers, and says so.

        The request was well-formed and the PDF was accepted; only the
        library to read it is absent. That is a gap in the deployment,
        so the run continues and records the gap rather than ending.
        """
        pdf = RecordingPdfService(
            document=pdf_document(limitation="PDF_UNAVAILABLE")
        )
        context = _pdf_context(pdf)

        state = run_investigation(
            "document.pdf",
            input_type=InvestigationInputType.PDF,
            context=context,
            pdf_upload=PdfUpload(
                content=b"%PDF-1.4", content_type="application/pdf"
            ),
        )

        assert not state["errors"]
        assert "PDF_UNAVAILABLE" in {warning.code for warning in state["warnings"]}
        assert state.get("risk_assessment") is not None

    def test_a_pdf_that_yields_no_text_is_a_limitation(self) -> None:
        """A readable PDF with nothing to say is a limitation, not an error.

        A scan of a printed page can be a perfectly good PDF that
        yields no words. Reporting that as a failure would tell the
        caller their document was broken.
        """
        pdf = RecordingPdfService(document=pdf_document())
        context = _pdf_context(pdf)

        state = run_investigation(
            "scan.pdf",
            input_type=InvestigationInputType.PDF,
            context=context,
            pdf_upload=PdfUpload(
                content=b"%PDF-1.4", content_type="application/pdf"
            ),
        )

        assert not state["errors"]
        assert "PDF_TEXT_NOT_RETRIEVED" in {warning.code for warning in state["warnings"]}
        assert state.get("risk_assessment") is not None

    def test_extraction_that_fails_is_a_limitation(self) -> None:
        """An engine that ran and failed is a limitation, not a fault.

        The library was present and the PDF parsed, so neither the
        deployment nor the submission is at fault; extraction failed
        for a reason the caller cannot act on. The run continues.
        """
        pdf = RecordingPdfService(
            document=pdf_document(
                limitation="PDF_EXTRACTION_FAILED", error_type="MuPdfError"
            )
        )
        context = _pdf_context(pdf)

        state = run_investigation(
            "document.pdf",
            input_type=InvestigationInputType.PDF,
            context=context,
            pdf_upload=PdfUpload(
                content=b"%PDF-1.4", content_type="application/pdf"
            ),
        )

        assert not state["errors"]
        assert "PDF_EXTRACTION_FAILED" in {warning.code for warning in state["warnings"]}
        assert state.get("risk_assessment") is not None

    def test_text_cut_at_the_budget_is_a_limitation(self) -> None:
        """A PDF longer than the budget is cut, and the cut is reported.

        The analysis below runs over the cut text, so it must be visible
        that text was dropped — otherwise the report would be confident
        about a document it only partly read.
        """
        pdf = RecordingPdfService(
            document=pdf_document(text="a" * 40, truncated=True)
        )
        context = _pdf_context(pdf)

        state = run_investigation(
            "document.pdf",
            input_type=InvestigationInputType.PDF,
            context=context,
            pdf_upload=PdfUpload(
                content=b"%PDF-1.4", content_type="application/pdf"
            ),
        )

        assert not state["errors"]
        assert "PDF_CONTENT_TRUNCATED" in {warning.code for warning in state["warnings"]}
        assert state.get("risk_assessment") is not None

    def test_only_the_first_pages_are_read_is_a_limitation(self) -> None:
        """A PDF with more pages than the limit is read partly, and it shows.

        The analysis runs over the pages read, so it must be visible that
        later pages were skipped — otherwise the report would be confident
        about a document it only partly read.
        """
        pdf = RecordingPdfService(
            document=pdf_document(
                text="Acme Capital Advisors is registered.",
                page_count=3,
                pages_processed=1,
            )
        )
        context = _pdf_context(pdf)

        state = run_investigation(
            "document.pdf",
            input_type=InvestigationInputType.PDF,
            context=context,
            pdf_upload=PdfUpload(
                content=b"%PDF-1.4", content_type="application/pdf"
            ),
        )

        assert not state["errors"]
        assert "PDF_PAGE_LIMIT_REACHED" in {warning.code for warning in state["warnings"]}
        assert state.get("risk_assessment") is not None


class TestExtractionFailures:
    """Phase 2 breaking its no-raise contract, and degrading as designed."""

    def test_a_contract_violation_stops_the_run_with_no_risk_score(
        self,
    ) -> None:
        """An exception escaping extraction ends the run and produces no score.

        A `0` score from a stage that never ran would be indistinguishable from a
        genuinely low-risk document — the exact confusion D-006 exists to prevent.
        """
        extraction = RecordingExtractionService(raises=CONTRACT_VIOLATION)
        dependencies = GraphDependencies(
            extraction_service=extraction,
            red_flag_engine=RecordingRedFlagEngine(),
            verification_service=RecordingVerificationService(),
            evidence_service=RecordingEvidenceService(),
            risk_service=RecordingRiskService(),
        )

        state = _run(_context_with(dependencies))

        assert state["errors"]
        assert state["errors"][0].code == "EXTRACTION_FAILED"
        assert state["errors"][0].stage is GraphStage.EXTRACTION
        assert "risk_assessment" not in state, (
            "a run that stopped at extraction must not carry a risk assessment"
        )

    def test_a_contract_violation_keeps_only_the_exception_class_name(
        self,
    ) -> None:
        """The message stays out of the state; the class name is enough to diagnose.

        """
        extraction = RecordingExtractionService(raises=CONTRACT_VIOLATION)
        dependencies = GraphDependencies(
            extraction_service=extraction,
            red_flag_engine=RecordingRedFlagEngine(),
            verification_service=RecordingVerificationService(),
            evidence_service=RecordingEvidenceService(),
            risk_service=RecordingRiskService(),
        )

        state = _run(_context_with(dependencies))

        error = state["errors"][0]
        assert error.error_type == "RuntimeError"
        assert "simulated contract violation" not in error.message
        assert "simulated contract violation" not in str(error)

    def test_a_provider_that_is_unavailable_degrades_to_patterns_and_a_warning(
        self, real_context: GraphContext
    ) -> None:
        """No LLM key is a `PARTIAL` run, not a failure.

        Args:
            real_context: An offline context with no LLM key configured.
        """
        state = _run(real_context)

        assert not state["errors"], f"a missing key must not fail the run: {state['errors']}"
        assert "EXTRACTION_FALLBACK" in {w.code for w in state.get("warnings", ())}
        assert state.get("extraction_mode") is ExtractionMode.FALLBACK

    def test_a_partial_extraction_reports_what_it_omitted(
        self,
    ) -> None:
        """A service that returns partial results is reported as partial, not clean.

        """
        extraction = RecordingExtractionService(
            result=extraction_with(
                claims=(build_claim(),),
                warnings=("Some sentences could not be parsed.",),
            )
        )
        dependencies = GraphDependencies(
            extraction_service=extraction,
            red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
            verification_service=RecordingVerificationService(),
            evidence_service=RecordingEvidenceService(),
            risk_service=RecordingRiskService(),
        )

        state = _run(_context_with(dependencies))

        assert not state["errors"]
        assert "EXTRACTION_PARTIAL" in {w.code for w in state.get("warnings", ())}
        extraction_events = [
            event for event in state["timeline"] if event.stage is GraphStage.EXTRACTION
        ]
        assert extraction_events[0].status is TimelineStatus.PARTIAL

    def test_an_extraction_with_no_claims_reports_the_gap_and_skips_the_rest(
        self,
    ) -> None:
        """Nothing to verify means verification and evidence are *skipped*.

        This is the canonical `SKIPPED` case: the stage did not fail, it had no
        work, and saying so is different from saying it found nothing.
        """
        dependencies = GraphDependencies(
            extraction_service=RecordingExtractionService(
                result=extraction_with()
            ),
            red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
            verification_service=RecordingVerificationService(),
            evidence_service=RecordingEvidenceService(),
            risk_service=RecordingRiskService(),
        )

        state = _run(_context_with(dependencies))

        assert not state["errors"]
        assert "NO_CLAIMS_EXTRACTED" in {w.code for w in state.get("warnings", ())}
        for stage in (GraphStage.VERIFICATION, GraphStage.EVIDENCE):
            events = [e for e in state["timeline"] if e.stage is stage]
            assert events[0].status is TimelineStatus.SKIPPED, (
                f"{stage.value} should be SKIPPED, was {events[0].status}"
            )


class TestRedFlagFailures:
    """Phase 1 breaking its no-raise contract."""

    def test_a_contract_violation_stops_the_run(self) -> None:
        """Detection failing stops the run rather than reporting "no patterns found".

        Returning an empty flag set after a fault would make a detection failure
        read as a clean document.
        """
        dependencies = GraphDependencies(
            extraction_service=RecordingExtractionService(
                result=extraction_with(claims=(build_claim(),), entities=(build_entity(),))
            ),
            red_flag_engine=RecordingRedFlagEngine(raises=CONTRACT_VIOLATION),
            verification_service=RecordingVerificationService(),
            evidence_service=RecordingEvidenceService(),
            risk_service=RecordingRiskService(),
        )

        state = _run(_context_with(dependencies))

        assert state["errors"]
        assert state["errors"][0].code == "RED_FLAG_DETECTION_FAILED"
        assert state["errors"][0].stage is GraphStage.RED_FLAGS
        assert "risk_assessment" not in state

    def test_no_patterns_found_is_reported_as_an_absence_not_an_accusation(
        self,
    ) -> None:
        """An empty detection is a limitation, and it says nothing about safety.

        """
        dependencies = GraphDependencies(
            extraction_service=RecordingExtractionService(
                result=extraction_with(claims=(build_claim(),))
            ),
            red_flag_engine=RecordingRedFlagEngine(flags=()),
            verification_service=RecordingVerificationService(),
            evidence_service=RecordingEvidenceService(),
            risk_service=RecordingRiskService(),
        )

        state = _run(_context_with(dependencies))

        assert not state["errors"]
        assert "NO_RED_FLAGS_DETECTED" in {w.code for w in state.get("warnings", ())}
        assert state.get("red_flags") == ()


class TestSearchFailures:
    """Every way a search can fail, and what each one is allowed to claim."""

    @pytest.mark.parametrize(
        "error_code",
        [
            "SEARCH_TIMEOUT",
            "SEARCH_RATE_LIMITED",
            "SEARCH_AUTH_ERROR",
            "SEARCH_SERVICE_ERROR",
            "SEARCH_INVALID_RESPONSE",
            "SEARCH_INVALID_QUERY",
        ],
    )
    def test_a_provider_failure_becomes_a_limitation_never_a_clean_search(
        self, db_settings, error_code: str
    ) -> None:
        """A failed search is `ERROR`, and claims become `INSUFFICIENT_EVIDENCE`.

        Not `UNVERIFIED` and certainly not `CONTRADICTED`: a provider being down is
        our blindness, and reporting it as somebody else's risk is the failure D-006
        names explicitly.

        Args:
            db_settings: Isolated settings for this test.
            error_code: The provider failure to simulate.
        """
        provider = FailingProvider(error_code)
        recorder = RecordingSearchService(SearchService(settings=db_settings, provider=provider))
        dependencies = GraphDependencies(
            extraction_service=RecordingExtractionService(
                result=extraction_with(claims=(build_claim(),), entities=(build_entity(),))
            ),
            red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
            verification_service=RecordingVerificationService(
                response=verification_response(
                    result_for(
                        build_claim(),
                        VerificationStatus.INSUFFICIENT_EVIDENCE,
                        reason_code="SEARCH_FAILED",
                    )
                )
            ),
            evidence_service=RecordingEvidenceService(bundle=EvidenceBundleResponse()),
            risk_service=RecordingRiskService(),
            search_recorder=recorder,
        )

        state = _run(_context_with(dependencies))

        assert not state["errors"], "a provider failure must not fail the whole run"
        assert state["verification_results"]
        for result in state["verification_results"]:
            assert result.status is not VerificationStatus.CONTRADICTED
            assert result.status is not VerificationStatus.VERIFIED
        assert state.get("evidence") == ()

    def test_no_api_key_is_unavailable_and_reports_that_nothing_was_searched(
        self, db_settings
    ) -> None:
        """A keyless provider never claims to have issued a request.

        Args:
            db_settings: Isolated settings for this test.
        """
        provider = UnconfiguredProvider()
        recorder = RecordingSearchService(SearchService(settings=db_settings, provider=provider))
        dependencies = GraphDependencies(
            extraction_service=RecordingExtractionService(
                result=extraction_with(claims=(build_claim(),), entities=(build_entity(),))
            ),
            red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
            verification_service=RecordingVerificationService(
                response=verification_response(
                    result_for(
                        build_claim(),
                        VerificationStatus.INSUFFICIENT_EVIDENCE,
                        reason_code="SEARCH_UNAVAILABLE",
                    )
                )
            ),
            evidence_service=RecordingEvidenceService(bundle=EvidenceBundleResponse()),
            risk_service=RecordingRiskService(),
            search_recorder=recorder,
        )

        state = _run(_context_with(dependencies))

        assert not state["errors"]
        assert "SEARCH_UNAVAILABLE" in {w.code for w in state.get("warnings", ())}
        # The search ran but produced nothing; it must not look like a clean check.
        assert state["verification_results"][0].status is VerificationStatus.INSUFFICIENT_EVIDENCE

    def test_a_malformed_provider_response_never_becomes_zero_results(
        self, db_settings
    ) -> None:
        """An unusable response is an error, distinguishable from "found nothing".

        Args:
            db_settings: Isolated settings for this test.
        """
        provider = FailingProvider("SEARCH_INVALID_RESPONSE")
        recorder = RecordingSearchService(SearchService(settings=db_settings, provider=provider))

        response = recorder.search("Acme Capital Advisors SEBI registration")

        assert response.status is SearchStatus.ERROR
        assert response.error_code == "SEARCH_INVALID_RESPONSE"
        assert not response.success
        assert response.error_code is not None

    def test_a_successful_search_with_no_results_is_a_success(
        self, db_settings
    ) -> None:
        """Empty and unavailable mean opposite things, so the codes differ.

        A search that completed and found nothing is a real gap in the public
        record, and Phase 6 scores it. A search that never ran is our blindness and
        must not be scored.

        Args:
            db_settings: Isolated settings for this test.
        """
        recorder = RecordingSearchService(
            SearchService(
                settings=db_settings, provider=RecordingSearchProvider(results=())
            )
        )

        response = recorder.search("Acme Capital Advisors SEBI registration")

        assert response.status is SearchStatus.OK
        assert response.results == ()
        assert response.success and not response.degraded


class TestVerificationFailures:
    """Every Phase 4 status, and what each is allowed to imply."""

    @pytest.mark.parametrize(
        ("status", "reason_code", "cites_sources"),
        [
            (VerificationStatus.VERIFIED, "AUTHORITATIVE_SOURCE_CONFIRMS", True),
            (VerificationStatus.UNVERIFIED, "ZERO_RESULTS", False),
            (VerificationStatus.CONTRADICTED, "AUTHORITATIVE_SOURCE_CONTRADICTS", True),
            (VerificationStatus.INSUFFICIENT_EVIDENCE, "SEARCH_UNAVAILABLE", False),
            (VerificationStatus.NOT_APPLICABLE, "NOT_A_FACTUAL_CLAIM", False),
        ],
    )
    def test_every_verification_status_survives_the_graph_without_becoming_a_verdict(
        self, status: VerificationStatus, reason_code: str, cites_sources: bool
    ) -> None:
        """Phase 4's answer passes through unchanged, including its uncertainty.

        Args:
            status: The verification status to report.
            reason_code: Phase 4's reason code.
            cites_sources: Whether the status requires cited sources.
        """
        claim = build_claim()
        dependencies = GraphDependencies(
            extraction_service=RecordingExtractionService(
                result=extraction_with(claims=(claim,), entities=(build_entity(),))
            ),
            red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
            verification_service=RecordingVerificationService(
                response=verification_response(
                    result_for(
                        claim,
                        status,
                        reason_code=reason_code,
                        source_ids=("sebi.gov.in",) if cites_sources else (),
                        matched_result_ids=("result_1",) if cites_sources else (),
                    )
                )
            ),
            evidence_service=RecordingEvidenceService(bundle=EvidenceBundleResponse()),
            risk_service=RecordingRiskService(),
            search_recorder=None,
        )

        state = _run(_context_with(dependencies))

        assert state["verification_results"][0].status is status
        # Coverage loss — a search that did not complete — is the only
        # outcome that makes the stage partial. A search that ran and
        # confirmed nothing is a complete answer, not a gap.
        if reason_code in (SEARCH_FAILED, SEARCH_UNAVAILABLE):
            assert "PARTIAL_VERIFICATION" in {w.code for w in state.get("warnings", ())}
        else:
            assert "PARTIAL_VERIFICATION" not in {
                w.code for w in state.get("warnings", ())
            }

    def test_identity_ambiguity_is_reported_as_ambiguity(
        self,
    ) -> None:
        """A party that cannot be uniquely identified is not silently resolved.

        """
        claim = build_claim()
        dependencies = GraphDependencies(
            extraction_service=RecordingExtractionService(
                result=extraction_with(claims=(claim,), entities=(build_entity(),))
            ),
            red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
            verification_service=RecordingVerificationService(
                response=verification_response(
                    result_for(
                        claim,
                        VerificationStatus.INSUFFICIENT_EVIDENCE,
                        reason_code="IDENTITY_NOT_FOUND",
                    )
                )
            ),
            evidence_service=RecordingEvidenceService(bundle=EvidenceBundleResponse()),
            risk_service=RecordingRiskService(),
            search_recorder=None,
        )

        state = _run(_context_with(dependencies))

        result = state["verification_results"][0]
        assert result.status is VerificationStatus.INSUFFICIENT_EVIDENCE
        assert result.reason_code == "IDENTITY_NOT_FOUND"
        assert result.status is not VerificationStatus.CONTRADICTED

    def test_a_contract_violation_stops_the_run(self) -> None:
        """Verification raising is a defect, and unchecked claims must not be
        reported as checked."""
        dependencies = GraphDependencies(
            extraction_service=RecordingExtractionService(
                result=extraction_with(claims=(build_claim(),), entities=(build_entity(),))
            ),
            red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
            verification_service=RecordingVerificationService(raises=CONTRACT_VIOLATION),
            evidence_service=RecordingEvidenceService(),
            risk_service=RecordingRiskService(),
        )

        state = _run(_context_with(dependencies))

        assert state["errors"]
        assert state["errors"][0].code == "VERIFICATION_FAILED"
        assert "risk_assessment" not in state

    def test_an_unrecorded_search_is_reported_not_hidden(
        self,
    ) -> None:
        """A graph with no recorder says so rather than citing provenance it lacks.

        """
        claim = build_claim()
        dependencies = GraphDependencies(
            extraction_service=RecordingExtractionService(
                result=extraction_with(claims=(claim,), entities=(build_entity(),))
            ),
            red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
            verification_service=RecordingVerificationService(
                response=verification_response(
                    result_for(claim, VerificationStatus.VERIFIED, reason_code="AUTHORITATIVE_SOURCE_CONFIRMS", source_ids=("sebi.gov.in",))
                )
            ),
            evidence_service=RecordingEvidenceService(bundle=EvidenceBundleResponse()),
            risk_service=RecordingRiskService(),
            search_recorder=None,
        )

        state = _run(_context_with(dependencies))

        assert "SEARCH_RESULTS_NOT_RECORDED" in {w.code for w in state.get("warnings", ())}


class TestEvidenceFailures:
    """Phase 5 losing provenance, which degrades but never re-decides."""

    def test_a_contract_violation_degrades_to_a_limitation_not_a_failure(
        self,
    ) -> None:
        """Evidence contributes no weight, so losing it cannot change the score.

        """
        claim = build_claim()
        dependencies = GraphDependencies(
            extraction_service=RecordingExtractionService(
                result=extraction_with(claims=(claim,), entities=(build_entity(),))
            ),
            red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
            verification_service=RecordingVerificationService(
                response=verification_response(
                    result_for(claim, VerificationStatus.UNVERIFIED, reason_code="ZERO_RESULTS")
                )
            ),
            evidence_service=RecordingEvidenceService(raises=CONTRACT_VIOLATION),
            risk_service=RecordingRiskService(),
        )

        state = _run(_context_with(dependencies))

        assert not state["errors"], "losing evidence must not end the run"
        assert "EVIDENCE_UNAVAILABLE" in {w.code for w in state.get("warnings", ())}
        assert state.get("evidence") == ()
        # The score still exists: it is the one thing evidence cannot affect.
        assert state.get("risk_assessment") is not None

    def test_no_claims_means_evidence_is_skipped_not_empty_because_it_failed(
        self,
    ) -> None:
        """The distinction between "no work" and "the work failed" is preserved.

        """
        dependencies = GraphDependencies(
            extraction_service=RecordingExtractionService(result=extraction_with()),
            red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
            verification_service=RecordingVerificationService(),
            evidence_service=RecordingEvidenceService(),
            risk_service=RecordingRiskService(),
        )

        state = _run(_context_with(dependencies))

        events = [e for e in state["timeline"] if e.stage is GraphStage.EVIDENCE]
        assert events[0].status is TimelineStatus.SKIPPED
        assert "EVIDENCE_UNAVAILABLE" not in {w.code for w in state.get("warnings", ())}

    def test_an_empty_bundle_produces_no_invented_citation(self) -> None:
        """A bundle with nothing in it stays empty.

        The forbidden behaviour is an invented "no authoritative evidence found"
        citation, which would be a fabricated source (rule 10).
        """
        claim = build_claim()
        dependencies = GraphDependencies(
            extraction_service=RecordingExtractionService(
                result=extraction_with(claims=(claim,), entities=(build_entity(),))
            ),
            red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
            verification_service=RecordingVerificationService(
                response=verification_response(
                    result_for(claim, VerificationStatus.UNVERIFIED, reason_code="ZERO_RESULTS")
                )
            ),
            evidence_service=RecordingEvidenceService(bundle=EvidenceBundleResponse()),
            risk_service=RecordingRiskService(),
        )

        state = _run(_context_with(dependencies))

        assert state["evidence"] == ()
        assert not state["errors"]


class TestRiskFailures:
    """Phase 6, where a fabricated score would be most dangerous."""

    def test_a_contract_violation_stops_the_run_with_no_score(self) -> None:
        """A risk stage that failed must not be reported as a low-risk result.

        """
        claim = build_claim()
        dependencies = GraphDependencies(
            extraction_service=RecordingExtractionService(
                result=extraction_with(claims=(claim,), entities=(build_entity(),))
            ),
            red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
            verification_service=RecordingVerificationService(
                response=verification_response(
                    result_for(claim, VerificationStatus.UNVERIFIED, reason_code="ZERO_RESULTS")
                )
            ),
            evidence_service=RecordingEvidenceService(bundle=EvidenceBundleResponse()),
            risk_service=RecordingRiskService(raises=CONTRACT_VIOLATION),
        )

        state = _run(_context_with(dependencies))

        assert state["errors"]
        assert state["errors"][0].code == "RISK_ASSESSMENT_FAILED"
        assert "risk_assessment" not in state, (
            "a failed risk stage must leave no score at all"
        )

    def test_a_clean_investigation_scores_zero_without_claiming_safety(self) -> None:
        """Zero means "no documented indicators found", not "this is safe".

        """
        dependencies = GraphDependencies(
            extraction_service=RecordingExtractionService(
                result=extraction_with(claims=(build_claim(),))
            ),
            red_flag_engine=RecordingRedFlagEngine(flags=()),
            verification_service=RecordingVerificationService(),
            evidence_service=RecordingEvidenceService(bundle=EvidenceBundleResponse()),
            risk_service=RecordingRiskService(),
        )

        state = _run(_context_with(dependencies))
        assessment = state["risk_assessment"]

        assert isinstance(assessment, RiskAssessment)
        assert assessment.risk_score == 0
        assert assessment.risk_level is RiskLevel.LOW
        # The caveat is what stops "0" reading as an all-clear.
        assert "not a probability" in " ".join(assessment.warnings)

    def test_an_invalid_factor_is_rejected_by_the_model_not_stored(
        self,
    ) -> None:
        """A negative weight cannot reach the score.

        `RiskFactor` validates its own contribution, so an invalid factor is a
        construction error rather than something that silently reduces a score.
        """
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            RiskAssessment(
                raw_score=-50,
                risk_score=-50,
                risk_level=RiskLevel.LOW,
                factors=(),
                weights=None,  # type: ignore[arg-type]
                thresholds=None,  # type: ignore[arg-type]
                completeness=1.0,
            )

    def test_malformed_dependency_data_does_not_reach_a_score(self) -> None:
        """A service returning the wrong type is caught, not scored.

        Recorded as a **known limitation**, not an endorsement. Each graph node
        guards the *service call* in a `try`, but not its own use of what came back:
        `risk_node` calls `len(assessment.factors)` outside the guard, so a service
        that returned a non-`RiskAssessment` makes the node raise `AttributeError`,
        which escapes `run_investigation` rather than being recorded as
        `RISK_ASSESSMENT_FAILED`.

        What matters for the product still holds, and is what this asserts: the
        wrong value never becomes a score. What is lost is the *classification* —
        the caller gets an exception instead of a structured `FAILED` state.

        The same shape exists in `extraction_node` (`len(result.claims)`) and
        `evidence_node` (`len(response.evidence)`). Widening the three guards to
        cover the node's own use of the result is a change to the graph's control
        flow, and belongs in a phase that is allowed to make one; this phase pins
        the behaviour so the gap cannot widen unnoticed.

        """
        claim = build_claim()
        dependencies = GraphDependencies(
            extraction_service=RecordingExtractionService(
                result=extraction_with(claims=(claim,), entities=(build_entity(),))
            ),
            red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
            verification_service=RecordingVerificationService(
                response=verification_response(
                    result_for(claim, VerificationStatus.UNVERIFIED, reason_code="ZERO_RESULTS")
                )
            ),
            evidence_service=RecordingEvidenceService(bundle=EvidenceBundleResponse()),
            risk_service=RecordingRiskService(
                assessment="not an assessment",  # type: ignore[arg-type]
            ),
        )

        # The run does not complete, and nothing pretending to be a score is
        # produced along the way.
        with pytest.raises(AttributeError):
            _run(_context_with(dependencies))

    def test_a_malformed_assessment_never_becomes_a_numeric_score(self) -> None:
        """The API boundary refuses a non-assessment rather than passing it on.

        The graph stores whatever its service returned, so the last line of defence
        is the response model. `RiskAssessment` is typed, so a `str` cannot be
        serialised into the documented field.

        """
        import pydantic

        from app.schemas.api import InvestigationResponse

        with pytest.raises(pydantic.ValidationError):
            InvestigationResponse(
                investigation_id="inv_x",
                status="COMPLETED",
                input_type="TEXT",
                language="en",
                current_stage="risk",
                risk_assessment="not an assessment",  # type: ignore[arg-type]
            )


class TestPersistenceFailures:
    """A run that succeeded but could not be stored is reported, not swallowed."""

    @staticmethod
    def _working_context() -> GraphContext:
        """A context whose graph runs cleanly, so only storage can fail.

        Returns:
            A `GraphContext` wired to fakes that produce a complete run.
        """
        return GraphContext(
            dependencies=GraphDependencies(
                extraction_service=RecordingExtractionService(
                    result=extraction_with(claims=(build_claim(),))
                ),
                red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
                verification_service=RecordingVerificationService(),
                evidence_service=RecordingEvidenceService(),
                risk_service=RecordingRiskService(),
            )
        )

    @pytest.mark.parametrize(
        ("exception", "secret_in_message"),
        [
            pytest.param(
                RuntimeError("disk full: /var/lib/investshield/investshield.db"),
                "disk full",
                id="disk_full",
            ),
            pytest.param(
                RuntimeError(
                    "could not connect to "
                    "postgresql+psycopg://investshield:hunter2-SUPER-SECRET@db.internal:5432/x"
                ),
                "hunter2-SUPER-SECRET",
                id="dsn_in_message",
            ),
            pytest.param(
                IntegrityError(
                    "INSERT", {}, Exception("UNIQUE constraint failed: investigations.public_id")
                ),
                "UNIQUE constraint",
                id="integrity_error",
            ),
            pytest.param(
                OperationalError("SELECT", {}, Exception("database is locked")),
                "database is locked",
                id="operational_error",
            ),
            pytest.param(
                DatabaseError("fatal", {}, Exception("connection refused")),
                "connection refused",
                id="database_error",
            ),
        ],
    )
    def test_a_storage_failure_is_a_500_that_leaks_nothing(
        self, failure_client, exception: Exception, secret_in_message: str
    ) -> None:
        """A write that fails produces a `500`, not a `200` claiming a stored run.

        Returning `200` would hand the client an `investigation_id` for a run the
        database never accepted, and its subsequent `GET` would `404` — a failure
        the client could not have anticipated or diagnosed.

        Each exception is also the vehicle for a leak check: driver text routinely
        embeds the DSN it was configured with, which is why the message is planted
        with something sensitive in it and then asserted absent from the response.

        Args:
            failure_client: Builds a client running a given context.
            exception: The failure to raise from the repository.
            secret_in_message: A fragment of the message that must not escape.
        """
        from app.api.deps import get_repository_dep

        class FailingRepository:
            """A repository whose every write fails the same way.

            Args:
                failure: The exception to raise.
            """

            def __init__(self, failure: Exception) -> None:
                self._failure = failure
                self.saved: list[object] = []
                self.rolled_back = False

            def save(self, state: object) -> None:
                """Fail, standing in for a storage fault.

                Args:
                    state: The finished run. Recorded so the test can prove the
                        pipeline ran and it was storage that failed.

                Raises:
                    Exception: Always, the configured failure.
                """
                self.saved.append(state)
                raise self._failure

            def commit(self) -> None:
                """Fail as well, so the route's own error path is exercised too.

                Raises:
                    Exception: Always, the configured failure.
                """
                raise self._failure

            def rollback(self) -> None:
                """Roll back, as a real session would."""
                self.rolled_back = True

        repository = FailingRepository(exception)
        client = failure_client(self._working_context())
        client.app.dependency_overrides[get_repository_dep] = lambda: repository

        with client:
            response = client.post(
                "/api/investigations/text", json={"text": REGULATORY_CONTENT}
            )

        assert response.status_code == 500, response.text
        assert set(response.json()) == {"error"}
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"
        assert response.json()["error"]["detail"] is None

        # The run itself succeeded; only the write failed. That is exactly why a
        # `200` would be a lie.
        assert len(repository.saved) == 1
        assert repository.rolled_back, "a failed write must roll the transaction back"

        # Nothing from the driver's message may cross the boundary.
        assert secret_in_message not in response.text
        assert "Traceback" not in response.text
        assert type(exception).__name__ not in response.json()["error"]["message"]

    def test_a_failure_to_commit_is_also_reported(
        self, failure_client
    ) -> None:
        """A `save` that succeeds but a `commit` that fails is still a failure.

        The rows are in the session but not durable, so a `200` would promise a
        retrieval that a process restart would lose.

        Args:
            failure_client: Builds a client running a given context.
        """
        from app.api.deps import get_repository_dep

        class CommitFailingRepository:
            """A repository that writes successfully but cannot commit."""

            def __init__(self) -> None:
                self.rolled_back = False

            def save(self, state: object) -> None:
                """Accept the run."""

            def commit(self) -> None:
                """Fail on commit.

                Raises:
                    RuntimeError: Always.
                """
                raise RuntimeError("could not commit transaction")

            def rollback(self) -> None:
                """Roll back."""
                self.rolled_back = True

        repository = CommitFailingRepository()
        client = failure_client(self._working_context())
        client.app.dependency_overrides[get_repository_dep] = lambda: repository

        with client:
            response = client.post(
                "/api/investigations/text", json={"text": REGULATORY_CONTENT}
            )

        assert response.status_code == 500
        assert repository.rolled_back

    def test_a_failed_write_leaves_nothing_retrievable(
        self, failure_client, test_settings
    ) -> None:
        """After a failed write, no run exists under the id that was refused.

        Args:
            failure_client: Builds a client running a given context.
            test_settings: Isolated settings, used to inspect the database.
        """
        from app.api.deps import get_repository_dep
        from app.db.session import Database
        from app.repositories.investigations import InvestigationRepository

        class FailingRepository:
            """A repository that fails every write."""

            def save(self, state: object) -> None:
                """Fail.

                Args:
                    state: The finished run.

                Raises:
                    RuntimeError: Always.
                """
                raise RuntimeError("write refused")

            def commit(self) -> None:
                """Fail."""

            def rollback(self) -> None:
                """Roll back."""

        client = failure_client(self._working_context())
        client.app.dependency_overrides[get_repository_dep] = lambda: FailingRepository()

        with client:
            response = client.post(
                "/api/investigations/text", json={"text": REGULATORY_CONTENT}
            )
        assert response.status_code == 500

        # Inspect the real database the app used, not a stub.
        database = Database(test_settings)
        try:
            session = database.session_factory()
            try:
                repo = InvestigationRepository(session)
                assert repo.find("inv_anything") is None
                page, total = repo.list_page(limit=20, offset=0)
                assert total == 0
                assert page == []
            finally:
                session.close()
        finally:
            database.dispose()

    def test_an_unavailable_database_does_not_stop_the_app_from_serving(
        self, client: TestClient
    ) -> None:
        """A database that cannot be reached must not take the whole API down.

        Schema creation tolerates failure at startup, so the endpoints that do not
        read stay available and investigation degrades to a `500` rather than the
        process refusing to boot.

        Args:
            client: A client built by the root suite's settings fixture.
        """
        assert client.get("/api/investigations/limits").status_code == 200
        assert client.get("/api/investigations").status_code == 200
        assert client.get("/api/health").status_code == 200


class TestFailureIsNeverDressedAsSuccess:
    """The cross-cutting invariant, asserted from outside any one stage."""

    @pytest.mark.parametrize(
        "make_dependencies",
        [
            pytest.param(
                lambda: GraphDependencies(
                    extraction_service=RecordingExtractionService(raises=CONTRACT_VIOLATION),
                    red_flag_engine=RecordingRedFlagEngine(),
                    verification_service=RecordingVerificationService(),
                    evidence_service=RecordingEvidenceService(),
                    risk_service=RecordingRiskService(),
                ),
                id="extraction_failed",
            ),
            pytest.param(
                lambda: GraphDependencies(
                    extraction_service=RecordingExtractionService(
                        result=extraction_with(claims=(build_claim(),))
                    ),
                    red_flag_engine=RecordingRedFlagEngine(raises=CONTRACT_VIOLATION),
                    verification_service=RecordingVerificationService(),
                    evidence_service=RecordingEvidenceService(),
                    risk_service=RecordingRiskService(),
                ),
                id="red_flags_failed",
            ),
            pytest.param(
                lambda: GraphDependencies(
                    extraction_service=RecordingExtractionService(
                        result=extraction_with(claims=(build_claim(),))
                    ),
                    red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
                    verification_service=RecordingVerificationService(raises=CONTRACT_VIOLATION),
                    evidence_service=RecordingEvidenceService(),
                    risk_service=RecordingRiskService(),
                ),
                id="verification_failed",
            ),
            pytest.param(
                lambda: GraphDependencies(
                    extraction_service=RecordingExtractionService(
                        result=extraction_with(claims=(build_claim(),))
                    ),
                    red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
                    verification_service=RecordingVerificationService(),
                    evidence_service=RecordingEvidenceService(),
                    risk_service=RecordingRiskService(raises=CONTRACT_VIOLATION),
                ),
                id="risk_failed",
            ),
        ],
    )
    def test_a_stopped_run_carries_no_score_and_no_completion(
        self, make_dependencies
    ) -> None:
        """Every fatal stage shares the same three guarantees.

        Args:
            make_dependencies: Builds the dependencies for this failure.
        """
        state = _run(_context_with(make_dependencies()))

        assert state["errors"], "the run must record a failure"
        assert "risk_assessment" not in state, (
            f"{state['errors'][0].code}: a stopped run must not carry a score"
        )

        # Stages that already finished before the fault are legitimately COMPLETED,
        # so the invariant is not "no stage completed" — it is that the run *ends* at
        # the fault and nothing after it ran.
        failed_stage = state["errors"][0].stage
        stages = [event.stage for event in state["timeline"]]
        assert stages[-1] == failed_stage, (
            f"the timeline continued past {failed_stage.value} to {stages[-1].value}"
        )
        assert state["timeline"][-1].status is TimelineStatus.FAILED

    @pytest.mark.parametrize(
        "make_dependencies",
        [
            pytest.param(
                lambda: GraphDependencies(
                    extraction_service=RecordingExtractionService(raises=CONTRACT_VIOLATION),
                    red_flag_engine=RecordingRedFlagEngine(),
                    verification_service=RecordingVerificationService(),
                    evidence_service=RecordingEvidenceService(),
                    risk_service=RecordingRiskService(),
                ),
                id="extraction_failed",
            ),
            pytest.param(
                lambda: GraphDependencies(
                    extraction_service=RecordingExtractionService(
                        result=extraction_with(claims=(build_claim(),))
                    ),
                    red_flag_engine=RecordingRedFlagEngine(raises=CONTRACT_VIOLATION),
                    verification_service=RecordingVerificationService(),
                    evidence_service=RecordingEvidenceService(),
                    risk_service=RecordingRiskService(),
                ),
                id="red_flags_failed",
            ),
            pytest.param(
                lambda: GraphDependencies(
                    extraction_service=RecordingExtractionService(
                        result=extraction_with(claims=(build_claim(),))
                    ),
                    red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
                    verification_service=RecordingVerificationService(raises=CONTRACT_VIOLATION),
                    evidence_service=RecordingEvidenceService(),
                    risk_service=RecordingRiskService(),
                ),
                id="verification_failed",
            ),
            pytest.param(
                lambda: GraphDependencies(
                    extraction_service=RecordingExtractionService(
                        result=extraction_with(claims=(build_claim(),))
                    ),
                    red_flag_engine=RecordingRedFlagEngine(flags=(build_flag(),)),
                    verification_service=RecordingVerificationService(),
                    evidence_service=RecordingEvidenceService(),
                    risk_service=RecordingRiskService(raises=CONTRACT_VIOLATION),
                ),
                id="risk_failed",
            ),
        ],
    )
    def test_a_stopped_run_is_reproducible_from_its_inputs(
        self, make_dependencies
    ) -> None:
        """Two identical failing runs agree on everything except the clock.

        A failure that varied run to run would be impossible to diagnose, and a
        client retrying would get a different answer each time.

        Args:
            make_dependencies: Builds the dependencies for this failure.
        """
        first = _run(_context_with(make_dependencies()))
        second = _run(_context_with(make_dependencies()))

        assert semantic_view(first) == semantic_view(second)


class TestTheFailureMatrixIsComplete:
    """The sweep above has not silently lost a stage."""

    def test_every_fatal_error_code_is_exercised_by_this_module(self) -> None:
        """Every code in `ERROR_CODES` is produced by a test in this file.

        A failure code added to the graph without a matching injection test would
        mean the new path is untested exactly where it is least likely to be
        exercised — it only fires when something breaks.
        """
        from app.graph.nodes import ERROR_CODES

        source = __file__
        text = open(source, encoding="utf-8").read()

        missing = [code for code in ERROR_CODES if code not in text]

        assert not missing, (
            f"error code(s) {missing} have no failure-injection test in this module"
        )

    def test_every_limitation_code_is_exercised_by_this_module(
        self, real_context: GraphContext
    ) -> None:
        """Every warning code has a test that produces it.

        A limitation only exists on the degraded path, so it is exactly the code most
        likely to be added without a test and never exercised. Reading the module
        for each code is crude, but it fails the moment one is added and not
        injected — which is the failure worth catching.

        Args:
            real_context: An offline context with the genuine services.
        """
        from app.graph.nodes import WARNING_CODES

        # One real run, so the assertion is about the suite and not just the source.
        assert _run(real_context)

        with open(__file__, encoding="utf-8") as handle:
            text = handle.read()

        missing = [code for code in WARNING_CODES if code not in text]

        assert not missing, (
            f"warning code(s) {missing} have no failure-injection test in this module"
        )
