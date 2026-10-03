"""PDF investigation endpoint tests (Phase 14).

These assert the contract a client of ``POST /api/investigations/pdf``
depends on: a multipart upload is validated before it is parsed, a
readable PDF is investigated through the *existing* pipeline, an
absent engine is a stated limitation rather than a failure, and a
stored PDF round-trips through ``GET /api/investigations/{id}``.

The cases that need a real engine are skipped where PyMuPDF is not
installed, so the suite passes in a deployment that has no PDF
library. The validation cases that run before parsing do not need
an engine at all: the size and declared-type gates run first, and
the absent-engine case is driven by a fake rather than the real
service, because the real one is only unavailable where the library
is not installed at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_graph_context_dep
from app.core.config import Settings
from app.graph.context import GraphContext, GraphDependencies
from app.main import create_app
from app.services.pdf_service import PDFService
from tests.graph.graph_factories import (
    offline_settings,
    pdf_document,
    real_dependencies,
    RecordingPdfService,
)
from tests.pdf_factories import garbage_bytes, pdf_bytes

#: Text rendered into the test PDF. Short and distinctive, so the
#: engine reads it reliably where it is installed and the pipeline
#: has something concrete to extract from it.
PDF_TEXT = "Acme Capital Advisors"


def _pdf_context(pdf_settings: Settings) -> GraphContext:
    """Build a graph context that can analyse PDFs.

    The Phase 1-6 services are always built from keyless offline
    settings, so nothing here can reach the network; only the PDF
    service is built from ``pdf_settings``, which lets a test place
    a working engine behind the same wiring.

    Args:
        pdf_settings: Settings for the PDF service.

    Returns:
        A context wired to the real Phase 1-6 services and a PDF
        service built from ``pdf_settings``.
    """
    dependencies: GraphDependencies
    dependencies, _ = real_dependencies(offline_settings())
    with_pdf = replace(dependencies, pdf_service=PDFService(settings=pdf_settings))
    return GraphContext(dependencies=with_pdf)


#: Skips the cases that need a real engine, so the suite passes in a
#: deployment that has no PDF library installed.
requires_pdf = pytest.mark.skipif(
    not PDFService(settings=offline_settings()).available,
    reason="the PyMuPDF library is not installed",
)


@pytest.fixture
def pdf_client(api_settings: Settings) -> Iterator[TestClient]:
    """A client whose graph can extract PDF text."""
    app = create_app(api_settings)
    app.dependency_overrides[get_graph_context_dep] = lambda: _pdf_context(
        offline_settings()
    )
    app.state.database.create_all()
    with TestClient(app) as client:
        yield client


@pytest.fixture
def pdf_without_engine_client(api_settings: Settings) -> Iterator[TestClient]:
    """A client wired for PDFs but built without a working engine.

    The PDF service is replaced by one that reports the library
    unavailable, whatever is installed. That is the deployment the
    degradation tests need: PDF support is wired, but extraction
    cannot run.
    """
    absent = RecordingPdfService(document=pdf_document(limitation="PDF_UNAVAILABLE"))
    dependencies, _ = real_dependencies(offline_settings())
    context = GraphContext(
        dependencies=replace(dependencies, pdf_service=absent)
    )
    app = create_app(api_settings)
    app.dependency_overrides[get_graph_context_dep] = lambda: context
    app.state.database.create_all()
    with TestClient(app) as client:
        yield client


def _post_pdf(
    client: TestClient,
    content: bytes,
    *,
    filename: str = "document.pdf",
    content_type: str = "application/pdf",
) -> Any:
    """Submit one PDF as multipart form data.

    Args:
        client: The test client.
        content: The PDF bytes.
        filename: The filename the client claims.
        content_type: The media type the client declares.

    Returns:
        The response, for the caller to assert against.
    """
    return client.post(
        "/api/investigations/pdf",
        files={"file": (filename, content, content_type)},
    )


# -- discovery ---------------------------------------------------------


def test_the_limits_endpoint_reports_the_pdf_limits(api_client: TestClient) -> None:
    """A client can discover the upload constraint before building one."""
    body = api_client.get("/api/investigations/limits").json()

    assert "PDF" in body["supported_input_types"]
    assert body["max_upload_bytes"] == offline_settings().max_upload_bytes
    assert set(body["allowed_pdf_types"]) == {"application/pdf"}
    assert body["pdf_max_pages"] == offline_settings().pdf_max_pages


# -- capability --------------------------------------------------------


def test_a_pdf_submission_without_pdf_support_is_unavailable(
    api_client: TestClient,
) -> None:
    """A graph built without a PDF service refuses PDFs with 503.

    The default offline context wires no PDF service, so PDF support
    is a configuration the deployment does not offer. That is the
    caller's answer to "can you do this", not a defect, so it is
    `503` — the same status a URL submission gets from a graph
    without URL support.
    """
    response = _post_pdf(api_client, pdf_bytes(text=PDF_TEXT))

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "PDF_INPUT_UNAVAILABLE"


# -- validation --------------------------------------------------------


def test_a_pdf_over_the_limit_is_refused(pdf_client: TestClient) -> None:
    """An oversized upload is the caller's fault, answered before parsing."""
    response = _post_pdf(
        pdf_client, b"x" * (offline_settings().max_upload_bytes + 1)
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PDF_FILE_TOO_LARGE"


def test_a_non_pdf_media_type_is_refused(pdf_client: TestClient) -> None:
    """A declared media type this version does not read is the caller's."""
    response = _post_pdf(pdf_client, b"never parsed", content_type="text/plain")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PDF_TYPE_UNSUPPORTED"


@requires_pdf
def test_bytes_that_do_not_parse_are_refused(pdf_client: TestClient) -> None:
    """Bytes that declare a PDF but do not parse are the caller's."""
    response = _post_pdf(pdf_client, garbage_bytes())

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PDF_UNREADABLE"


# -- degradation -------------------------------------------------------


def test_a_pdf_the_engine_cannot_read_is_a_limitation(
    pdf_without_engine_client: TestClient,
) -> None:
    """A wired graph with no engine is not a failure.

    The run continues and answers `200 PARTIAL` with a
    `PDF_UNAVAILABLE` limitation and no recovered text — no text is
    fabricated, and the deployment's missing library is never named
    in the response.
    """
    response = _post_pdf(pdf_without_engine_client, pdf_bytes(text=PDF_TEXT))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PARTIAL"
    assert body["input_type"] == "PDF"
    assert "PDF_UNAVAILABLE" in body["limitations"]

    source = body["pdf_source"]
    assert source is not None
    assert source["text_recovered"] is False

    # The absent engine's path is a deployment detail, never a response.
    assert "pymupdf" not in response.text.lower()


def test_a_degraded_pdf_is_persisted(
    pdf_without_engine_client: TestClient,
) -> None:
    """A run that could not read its PDF is still stored and retrievable.

    A partial result is a real result. The stored run keeps the
    document's provenance — including that no text was recovered — so
    it can be looked up again exactly as it was returned.
    """
    created = _post_pdf(pdf_without_engine_client, pdf_bytes(text=PDF_TEXT))
    assert created.status_code == 200
    investigation_id = created.json()["investigation_id"]

    retrieved = pdf_without_engine_client.get(
        f"/api/investigations/{investigation_id}"
    )

    assert retrieved.status_code == 200
    assert retrieved.json()["pdf_source"] == created.json()["pdf_source"]
    assert "PDF_UNAVAILABLE" in retrieved.json()["limitations"]


# -- regression --------------------------------------------------------


def test_text_investigations_still_work(api_client: TestClient) -> None:
    """Adding the PDF modality leaves text investigations untouched."""
    response = api_client.post("/api/investigations/text", json={"text": "Hello."})

    assert response.status_code == 200
    assert response.json()["input_type"] == "TEXT"
    assert response.json()["pdf_source"] is None


# -- the real engine ---------------------------------------------------


@requires_pdf
def test_a_pdf_is_investigated_end_to_end(pdf_client: TestClient) -> None:
    """A readable PDF is accepted, parsed and investigated."""
    response = _post_pdf(pdf_client, pdf_bytes(text=PDF_TEXT))

    assert response.status_code == 200
    body = response.json()
    assert body["input_type"] == "PDF"
    assert body["status"] in {"COMPLETED", "PARTIAL"}

    source = body["pdf_source"]
    assert source is not None
    assert source["filename"] == "document.pdf"
    assert source["content_type"] == "application/pdf"
    assert source["detected_content_type"] == "application/pdf"
    assert source["format"] == "PDF"
    assert source["text_recovered"] is True
    assert source["page_count"] == 1
    assert source["pages_processed"] == 1


@requires_pdf
def test_the_recovered_text_reaches_the_pipeline(pdf_client: TestClient) -> None:
    """The text the engine extracted is what the pipeline analysed.

    The whole point of the modality: the PDF's text becomes the
    input to the existing Phase 1-6 machinery. A PDF promising
    guaranteed returns is therefore investigated as the promise it
    contains — the red-flag stage detects it — rather than as a
    document with no readable content. The red-flag engine is a
    deterministic pattern matcher, so this does not depend on an LLM
    being configured.
    """
    response = _post_pdf(pdf_client, pdf_bytes(text="Guaranteed returns"))

    assert response.status_code == 200
    body = response.json()

    assert body["pdf_source"]["text_recovered"] is True
    codes = [flag["code"] for flag in body["red_flags"]]
    assert "GUARANTEED_RETURN" in codes


@requires_pdf
def test_a_persisted_pdf_round_trips(pdf_client: TestClient) -> None:
    """A stored PDF reloads with its provenance intact.

    The retrieval is built by the same adapter from the same state, so
    the document's own facts — what it parsed to and what the engine
    read — survive the round trip, and the input type stays `PDF`.
    """
    created = _post_pdf(pdf_client, pdf_bytes(text=PDF_TEXT))
    assert created.status_code == 200
    investigation_id = created.json()["investigation_id"]

    retrieved = pdf_client.get(f"/api/investigations/{investigation_id}")

    assert retrieved.status_code == 200
    assert retrieved.json() == created.json()
