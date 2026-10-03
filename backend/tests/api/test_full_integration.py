"""The consolidated end-to-end integration test (Phase 16).

Phase 16 exists to prove the product works as one pipeline, not as
four disconnected phases. This module submits every input the API
accepts — TEXT, SCREENSHOT and PDF — through the whole chain in one
sweep: HTTP input, the real graph, every stage, persistence, and
retrieval, and then retrieves each stored investigation in every
language this version renders. The URL input is exercised through
its documented offline refusal, because a live URL needs the network
this suite is not allowed to touch; the SSRF guard that protects it
is covered by `tests/test_url_guards.py`.

The graph runs the genuine Phase 1-6 services with both upload
engines wired for real: Tesseract reads the screenshot and PyMuPDF
reads the PDF, exactly as in production. Where an engine is not
installed on the machine, that leg is skipped rather than faked — a
skipped leg states a deployment fact, it does not pretend.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_graph_context_dep
from app.core.config import Settings
from app.graph.context import GraphContext, GraphDependencies
from app.main import create_app
from app.services.ocr_service import OCRService
from app.services.pdf_service import PDFService
from tests.graph.graph_factories import offline_settings, real_dependencies
from tests.image_factories import png_bytes
from tests.pdf_factories import pdf_bytes
from tests.persistence_factories import REGULATORY_CONTENT

TEXT_URL = "/api/investigations/text"
IMAGE_URL = "/api/investigations/image"
PDF_URL = "/api/investigations/pdf"
URL_URL = "/api/investigations/url"
LIST_URL = "/api/investigations"

#: The presentation-layer keys that are allowed to differ by language.
LOCALIZED_KEYS = {"language", "report"}

#: Every language this version renders, for the retrieval sweep.
LANGUAGES = ("en", "hi", "mr")

#: Text rendered into the test screenshot and PDF. Short,
#: high-contrast and distinctive, so the engines read it reliably.
SCREENSHOT_TEXT = "Acme Capital Advisors"
PDF_TEXT = "Acme Capital Advisors"

#: Skips the legs that need a real engine, so the suite passes in a
#: deployment that has no Tesseract or PyMuPDF installed.
requires_ocr = pytest.mark.skipif(
    not OCRService(settings=offline_settings()).available,
    reason="the Tesseract engine is not installed",
)
requires_pdf = pytest.mark.skipif(
    not PDFService(settings=offline_settings()).available,
    reason="the PyMuPDF library is not installed",
)


def _full_context() -> GraphContext:
    """Build the offline graph with both upload engines working.

    The Phase 1-6 services are always built from keyless offline
    settings, so nothing here can reach the network; the OCR and PDF
    services are built from the same settings so the real engines —
    where they are installed — read the uploads.

    Returns:
        A context wired to the real services and both real engines.
    """
    dependencies: GraphDependencies
    dependencies, _ = real_dependencies(offline_settings())
    return GraphContext(
        dependencies=replace(
            dependencies,
            ocr_service=OCRService(settings=offline_settings()),
            pdf_service=PDFService(settings=offline_settings()),
        )
    )


@pytest.fixture
def full_client(api_settings: Settings) -> Iterator[TestClient]:
    """A client whose graph runs the real pipeline on every input type."""
    app = create_app(api_settings)
    app.dependency_overrides[get_graph_context_dep] = lambda: _full_context()
    app.state.database.create_all()
    with TestClient(app) as client:
        yield client


def _post_text(client: TestClient, language: str = "en") -> dict:
    """Submit the fixture content as a text investigation."""
    response = client.post(
        TEXT_URL, json={"text": REGULATORY_CONTENT, "language": language}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _post_image(client: TestClient) -> dict:
    """Submit a generated screenshot as a multipart investigation."""
    content = png_bytes(text=SCREENSHOT_TEXT)
    response = client.post(
        IMAGE_URL, files={"file": ("shot.png", content, "image/png")}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _post_pdf(client: TestClient) -> dict:
    """Submit a generated PDF as a multipart investigation."""
    content = pdf_bytes(text=PDF_TEXT)
    response = client.post(
        PDF_URL, files={"file": ("document.pdf", content, "application/pdf")}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _canonical(body: dict) -> dict:
    """The language-neutral part of a response body."""
    return {key: value for key, value in body.items() if key not in LOCALIZED_KEYS}


# -- the whole chain, per input type --------------------------------


def test_text_input_runs_the_full_pipeline(full_client: TestClient) -> None:
    """A text submission travels the chain and is stored."""
    body = _post_text(full_client)

    assert body["status"] in {"COMPLETED", "PARTIAL"}
    assert body["input_type"] == "TEXT"
    assert body["errors"] == []
    assert body["timeline"]
    assert isinstance(body["claims"], list)
    assert isinstance(body["entities"], list)
    assert isinstance(body["red_flags"], list)
    assert isinstance(body["verification_results"], list)
    assert isinstance(body["evidence"], list)
    assert body["risk_assessment"] is not None
    assert body["report"] is not None


@requires_ocr
def test_screenshot_input_runs_the_full_pipeline(full_client: TestClient) -> None:
    """A screenshot submission is decoded, read and stored."""
    body = _post_image(full_client)

    assert body["status"] in {"COMPLETED", "PARTIAL"}
    assert body["input_type"] == "IMAGE"
    assert body["errors"] == []
    source = body["image_source"]
    assert source is not None
    assert source["format"] == "PNG"
    assert source["width"] is not None
    assert source["height"] is not None
    assert source["text_recovered"] is True


@requires_pdf
def test_pdf_input_runs_the_full_pipeline(full_client: TestClient) -> None:
    """A PDF submission is parsed, read and stored."""
    body = _post_pdf(full_client)

    assert body["status"] in {"COMPLETED", "PARTIAL"}
    assert body["input_type"] == "PDF"
    assert body["errors"] == []
    source = body["pdf_source"]
    assert source is not None
    assert source["format"] == "PDF"
    assert source["page_count"] == 1
    assert source["pages_processed"] == 1
    assert source["text_recovered"] is True


def test_url_input_is_an_honest_offline_refusal(full_client: TestClient) -> None:
    """A URL submission is refused with its documented capability answer.

    The offline graph wires no fetch service, so the honest answer is
    the `503` capability refusal — not a silent success and not a
    network call. A live URL leg would need the network, which this
    suite is forbidden to touch, and the SSRF guard that would
    reject a non-public address is covered by `test_url_guards.py`.
    """
    response = full_client.post(URL_URL, json={"url": "https://example.com"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "URL_FETCH_UNAVAILABLE"


# -- persistence and multilingual retrieval --------------------------


def test_every_input_is_stored_and_renders_in_every_language(
    full_client: TestClient,
) -> None:
    """Every input survives the chain and renders identically in every language.

    Each submitted input is retrieved in English, Hindi and Marathi.
    The canonical fields — every fact the investigation produced —
    must be value-for-value identical across the three retrievals,
    because a localized report that changed a claim, a flag or a
    score would be a different investigation, not a different
    rendering. Only the presentation layer may differ.
    """
    submitted: list[tuple[str, str]] = [("TEXT", _post_text(full_client)["investigation_id"])]
    if OCRService(settings=offline_settings()).available:
        submitted.append(("IMAGE", _post_image(full_client)["investigation_id"]))
    if PDFService(settings=offline_settings()).available:
        submitted.append(("PDF", _post_pdf(full_client)["investigation_id"]))

    for input_type, investigation_id in submitted:
        baseline: dict | None = None
        for language in LANGUAGES:
            response = full_client.get(
                f"/api/investigations/{investigation_id}",
                params={"language": language},
            )
            assert response.status_code == 200, response.text
            body = response.json()

            assert body["investigation_id"] == investigation_id
            assert body["input_type"] == input_type
            assert body["language"] == language
            report = body["report"]
            assert report is not None
            assert report["language"] == language
            assert report["investigation_id"] == investigation_id
            assert report["sections"]
            assert report["summary"]
            assert report["safety_guidance"]
            assert report["risk_caveat"]
            assert report["disclaimer"]

            canonical = _canonical(body)
            if baseline is None:
                baseline = canonical
            else:
                assert canonical == baseline

        assert baseline is not None
        english = full_client.get(f"/api/investigations/{investigation_id}").json()
        hindi = full_client.get(
            f"/api/investigations/{investigation_id}", params={"language": "hi"}
        ).json()
        assert (
            english["report"]["sections"]["risk_assessment"]
            != hindi["report"]["sections"]["risk_assessment"]
        )

    listed = full_client.get(LIST_URL).json()
    stored = {item["investigation_id"] for item in listed["investigations"]}
    for _, investigation_id in submitted:
        assert investigation_id in stored
