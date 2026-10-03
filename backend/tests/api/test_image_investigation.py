"""Screenshot investigation endpoint tests (Phase 13).

These assert the contract a client of ``POST /api/investigations/image``
depends on: a multipart upload is validated before it is decoded, a
readable screenshot is investigated through the *existing* pipeline, an
absent engine is a stated limitation rather than a failure, and a stored
screenshot round-trips through ``GET /api/investigations/{id}``.

The cases that need a real engine are skipped where Tesseract is not
installed, so the suite passes in a deployment that has no OCR. The
degradation and validation cases do not need an engine at all: the size
and declared-type gates run first, and an engine can be forced absent
with a Tesseract path that does not exist.
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
from app.services.ocr_service import OCRService
from tests.graph.graph_factories import offline_settings, real_dependencies
from tests.image_factories import gif_bytes, png_bytes

#: Text rendered into the test screenshot. Short, high-contrast and
#: distinctive, so the engine reads it reliably where it is installed
#: and the pipeline has something concrete to extract from it.
SCREENSHOT_TEXT = "Acme Capital Advisors"


def _image_context(ocr_settings: Settings) -> GraphContext:
    """Build a graph context that can analyse screenshots.

    The Phase 1-6 services are always built from keyless offline
    settings, so nothing here can reach the network; only the OCR
    service is built from ``ocr_settings``, which lets a test place a
    working engine or a deliberately absent one behind the same wiring.

    Args:
        ocr_settings: Settings for the OCR service, including the
            Tesseract location to resolve.

    Returns:
        A context wired to the real Phase 1-6 services and an OCR
        service built from ``ocr_settings``.
    """
    dependencies: GraphDependencies
    dependencies, _ = real_dependencies(offline_settings())
    with_ocr = replace(dependencies, ocr_service=OCRService(settings=ocr_settings))
    return GraphContext(dependencies=with_ocr)


#: Skips the cases that need a real engine, so the suite passes in a
#: deployment that has no Tesseract installed.
requires_ocr = pytest.mark.skipif(
    not OCRService(settings=offline_settings()).available,
    reason="the Tesseract engine is not installed",
)


@pytest.fixture
def image_client(api_settings: Settings) -> Iterator[TestClient]:
    """A client whose graph can recognise screenshots."""
    app = create_app(api_settings)
    app.dependency_overrides[get_graph_context_dep] = lambda: _image_context(
        offline_settings()
    )
    app.state.database.create_all()
    with TestClient(app) as client:
        yield client


@pytest.fixture
def image_without_engine_client(api_settings: Settings) -> Iterator[TestClient]:
    """A client wired for images but built without a working engine.

    The OCR service resolves a Tesseract path that does not exist, so it
    reports itself unavailable whatever is installed. That is the
    deployment the degradation tests need: image support is wired, but
    recognition cannot run.
    """
    absent = offline_settings().model_copy(update={"tesseract_cmd": "/no/such/tesseract"})
    app = create_app(api_settings)
    app.dependency_overrides[get_graph_context_dep] = lambda: _image_context(absent)
    app.state.database.create_all()
    with TestClient(app) as client:
        yield client


def _post_image(
    client: TestClient,
    content: bytes,
    *,
    filename: str = "shot.png",
    content_type: str = "image/png",
) -> Any:
    """Submit one image as multipart form data.

    Args:
        client: The test client.
        content: The image bytes.
        filename: The filename the client claims.
        content_type: The media type the client declares.

    Returns:
        The response, for the caller to assert against.
    """
    return client.post(
        "/api/investigations/image",
        files={"file": (filename, content, content_type)},
    )


# -- discovery ---------------------------------------------------------


def test_the_limits_endpoint_reports_the_image_limits(api_client: TestClient) -> None:
    """A client can discover the upload constraint before building one."""
    body = api_client.get("/api/investigations/limits").json()

    assert "IMAGE" in body["supported_input_types"]
    assert body["max_upload_bytes"] == offline_settings().max_upload_bytes
    assert set(body["allowed_image_types"]) == {"image/png", "image/jpeg", "image/webp"}
    assert body["ocr_languages"] == offline_settings().ocr_languages


# -- capability --------------------------------------------------------


def test_an_image_submission_without_ocr_support_is_unavailable(
    api_client: TestClient,
) -> None:
    """A graph built without an OCR service refuses screenshots with 503.

    The default offline context wires no OCR service, so image support is
    a configuration the deployment does not offer. That is the caller's
    answer to "can you do this", not a defect, so it is `503` — the same
    status a URL submission gets from a graph without URL support.
    """
    response = _post_image(api_client, png_bytes(text=SCREENSHOT_TEXT))

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "IMAGE_INPUT_UNAVAILABLE"
    # A capability refusal names no submitted type to correct, so it
    # carries no list of alternatives — the limits endpoint does that.
    assert error["detail"]["supported_input_types"] == []


# -- validation --------------------------------------------------------


def test_an_oversized_image_is_refused(image_client: TestClient) -> None:
    """An upload past the byte limit is refused before it is decoded."""
    settings = offline_settings()
    oversized = b"x" * (settings.max_upload_bytes + 1)

    response = _post_image(image_client, oversized)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "OCR_IMAGE_TOO_LARGE"
    assert str(settings.max_upload_bytes) in error["message"]


def test_a_declared_non_image_type_is_refused(image_client: TestClient) -> None:
    """A submission that is plainly not an image is refused at the gate."""
    response = _post_image(
        image_client,
        b"just some text",
        content_type="text/plain",
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "OCR_IMAGE_TYPE_UNSUPPORTED"


def test_a_gif_is_refused_at_the_declared_type_gate(
    image_client: TestClient,
) -> None:
    """A format this version does not read is refused by its declared type.

    The declared type is untrusted, but it is still the cheapest first
    filter: a GIF announces itself, and is refused before any decoding.
    """
    response = _post_image(image_client, gif_bytes(), content_type="image/gif")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "OCR_IMAGE_TYPE_UNSUPPORTED"


@requires_ocr
def test_bytes_that_do_not_decode_are_refused(image_client: TestClient) -> None:
    """Bytes that claim to be an image but do not decode are refused.

    Reaching this refusal needs a working engine, because the decode runs
    only after the service has confirmed recognition is possible.
    """
    response = _post_image(image_client, b"not an image at all")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "OCR_IMAGE_UNREADABLE"


@requires_ocr
def test_a_format_the_version_does_not_read_is_refused_at_decode(
    image_client: TestClient,
) -> None:
    """A GIF wearing a PNG label is refused by what it decodes to.

    The declared type alone would let this through; the image library's
    decode is the authority, and a GIF is not a format this version reads.
    """
    response = _post_image(image_client, gif_bytes(), content_type="image/png")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "OCR_IMAGE_TYPE_UNSUPPORTED"


# -- degradation -------------------------------------------------------


def test_an_absent_engine_is_a_degraded_result(
    image_without_engine_client: TestClient,
) -> None:
    """A wired graph with no engine reports the limitation, not a failure.

    The request was well-formed and the image was accepted, so the run
    continues past the missing engine and answers `200` with `PARTIAL`.
    No text is fabricated: `text_recovered` is false and the decode-dependent
    facts are left blank, because the image was never read.
    """
    response = _post_image(image_without_engine_client, png_bytes(text=SCREENSHOT_TEXT))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PARTIAL"
    assert body["input_type"] == "IMAGE"
    assert "OCR_UNAVAILABLE" in body["limitations"]

    source = body["image_source"]
    assert source is not None
    assert source["text_recovered"] is False
    assert source["detected_content_type"] is None
    assert source["width"] is None

    # The absent engine's path is a deployment detail, never a response.
    assert "/no/such/tesseract" not in response.text


def test_a_degraded_screenshot_is_persisted(
    image_without_engine_client: TestClient,
) -> None:
    """A run that could not read its image is still stored and retrievable.

    A partial result is a real result. The stored run keeps the image's
    provenance — including that no text was recovered — so it can be
    looked up again exactly as it was returned.
    """
    created = _post_image(image_without_engine_client, png_bytes(text=SCREENSHOT_TEXT))
    assert created.status_code == 200
    investigation_id = created.json()["investigation_id"]

    retrieved = image_without_engine_client.get(
        f"/api/investigations/{investigation_id}"
    )

    assert retrieved.status_code == 200
    assert retrieved.json()["image_source"] == created.json()["image_source"]
    assert "OCR_UNAVAILABLE" in retrieved.json()["limitations"]


# -- regression --------------------------------------------------------


def test_text_investigations_still_work(api_client: TestClient) -> None:
    """Adding the image modality leaves text investigations untouched."""
    response = api_client.post("/api/investigations/text", json={"text": "Hello."})

    assert response.status_code == 200
    assert response.json()["input_type"] == "TEXT"
    assert response.json()["image_source"] is None


def test_url_investigations_still_report_their_capability(
    api_client: TestClient,
) -> None:
    """The URL modality keeps its own typed answer from the offline graph.

    The offline context wires no URL service, so a URL submission is the
    documented `503` capability refusal — the same answer it gave before
    screenshots existed, which is the regression this guards.
    """
    response = api_client.post(
        "/api/investigations/url", json={"url": "https://example.com"}
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "URL_FETCH_UNAVAILABLE"


# -- the real engine ---------------------------------------------------


@requires_ocr
def test_a_screenshot_is_investigated_end_to_end(
    image_client: TestClient,
) -> None:
    """A readable screenshot is accepted, read and investigated."""
    response = _post_image(image_client, png_bytes(text=SCREENSHOT_TEXT))

    assert response.status_code == 200
    body = response.json()
    assert body["input_type"] == "IMAGE"
    assert body["status"] in {"COMPLETED", "PARTIAL"}

    source = body["image_source"]
    assert source is not None
    assert source["filename"] == "shot.png"
    assert source["content_type"] == "image/png"
    assert source["detected_content_type"] == "image/png"
    assert source["format"] == "PNG"
    assert source["text_recovered"] is True
    assert source["width"] is not None and source["width"] > 0
    assert source["height"] is not None and source["height"] > 0


@requires_ocr
def test_the_recovered_text_reaches_the_pipeline(
    image_client: TestClient,
) -> None:
    """The text the engine recovered is what the pipeline analysed.

    The whole point of the modality: the screenshot's text becomes
    the input to the existing Phase 1-6 machinery. A screenshot of
    a guaranteed-return promise is therefore investigated as the
    promise it contains — the red-flag stage detects it — rather
    than as an image with no readable content. The red-flag engine
    is a deterministic pattern matcher, so this does not depend on
    an LLM being configured.
    """
    response = _post_image(
        image_client, png_bytes(text="Guaranteed returns")
    )

    assert response.status_code == 200
    body = response.json()

    assert body["image_source"]["text_recovered"] is True
    codes = [flag["code"] for flag in body["red_flags"]]
    assert "GUARANTEED_RETURN" in codes


@requires_ocr
def test_a_persisted_screenshot_round_trips(image_client: TestClient) -> None:
    """A stored screenshot reloads with its provenance intact.

    The retrieval is built by the same adapter from the same state, so
    the image's own facts — what it decoded to and what the engine read —
    survive the round trip, and the input type stays `IMAGE`.
    """
    created = _post_image(image_client, png_bytes(text=SCREENSHOT_TEXT))
    assert created.status_code == 200
    investigation_id = created.json()["investigation_id"]

    retrieved = image_client.get(f"/api/investigations/{investigation_id}")

    assert retrieved.status_code == 200
    assert retrieved.json() == created.json()
