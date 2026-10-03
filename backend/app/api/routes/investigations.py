"""Investigation endpoints (Phase 8, extended by later phases).

Seven endpoints, deliberately thin. Each validates the submission, calls
`run_investigation`, hands the final state to the adapter, and — for the
write endpoints — stores it. No threshold, score, verdict or wording is decided here.

- `POST /api/investigations/text` — the documented convenience form. `input_type`
  is fixed to `TEXT`, so a client cannot accidentally submit a URL and have it
  analysed as prose.
- `POST /api/investigations/url` — the same convenience for a website. `input_type`
  is fixed to `URL`, so a client cannot accidentally submit prose as an address.
- `POST /api/investigations/image` — the same convenience for a screenshot, as
  `multipart/form-data`. `input_type` is fixed to `IMAGE`; the bytes travel in
  the file part, because a JSON body cannot carry them.
- `POST /api/investigations/pdf` — the same convenience for a PDF document,
  as `multipart/form-data`. `input_type` is fixed to `PDF`; the bytes travel
  in the file part, because a JSON body cannot carry them.
- `POST /api/investigations` — the typed form, where `input_type` is explicit.
- `GET /api/investigations/{id}` — a stored run, rebuilt and passed to the **same**
  adapter the write path used.
- `GET /api/investigations` — run history, newest first.
- `GET /api/investigations/limits` — what this version accepts.

Two things are worth stating plainly, because both were decisions rather than
accidents.

**Retrieval reuses the write path's adapter.** The repository returns an
`InvestigationState`, not a bespoke response type, and this module feeds it to
`serialize_investigation` exactly as it does for a live run. There is therefore
no second shaping path that could render a stored investigation differently from
a fresh one. A `GET` that disagreed with the `POST` that produced the run would
be worse than no retrieval at all.

**Persistence does not change the write contract.** The run is still synchronous
and still returns the whole result. Storing it is a side effect of a successful
run, not a separate operation: a client that ignores the new `GET` endpoints
cannot tell the difference, and a storage failure is reported as a `500` rather
than being swallowed — a run that succeeded but could not be stored would
otherwise be lost without anyone being told.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from app.api.adapters import serialize_investigation
from app.api.deps import (
    get_graph_context_dep,
    get_repository_dep,
    get_settings_dep,
)
from app.api.errors import (
    SUPPORTED_INPUT_TYPES,
    ApiError,
    InvestigationNotFound,
    raise_for_graph_errors,
)
from app.core.config import Settings
from app.core.logging import get_logger
from app.graph.context import GraphContext
from app.graph.investigation_graph import run_investigation
from app.graph.state import InvestigationInputType, InvestigationState
from app.repositories.investigations import (
    InvestigationRepository,
    InvestigationSummary,
    NotFound,
)
from app.schemas.api import (
    MAX_TEXT_LENGTH,
    ErrorEnvelope,
    InvestigationCreateRequest,
    InvestigationListResponse,
    InvestigationResponse,
    InvestigationStatus,
    InvestigationSummaryResponse,
    Language,
    TextInvestigationRequest,
    UrlInvestigationRequest,
)
from app.schemas.ocr import ImageUpload
from app.schemas.pdf import PdfUpload
from app.schemas.url import MAX_URL_LENGTH
from app.services.ocr_guards import OCR_IMAGE_FORMATS
from app.services.pdf_guards import PDF_CONTENT_TYPES

logger = get_logger(__name__)

router = APIRouter(tags=["investigations"])

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    422: {"model": ErrorEnvelope, "description": "The submission could not be accepted."},
    500: {"model": ErrorEnvelope, "description": "A stage broke its documented contract."},
}

#: Same as `_ERROR_RESPONSES` plus the site-fault case, so a caller reading the
#: OpenAPI document can see that a URL investigation has one more failure mode
#: than a text one, and which status it produces.
_URL_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    **_ERROR_RESPONSES,
    502: {
        "model": ErrorEnvelope,
        "description": "The submitted site could not be retrieved.",
    },
    503: {
        "model": ErrorEnvelope,
        "description": "Website investigation is not available in this deployment.",
    },
}

#: Same as `_ERROR_RESPONSES` plus the deployment-fault case, so a caller
#: reading the OpenAPI document can see that a screenshot investigation has
#: one more failure mode than a text one, and which status it produces.
#: There is no `502`: an image has no upstream site to have failed.
_IMAGE_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    **_ERROR_RESPONSES,
    503: {
        "model": ErrorEnvelope,
        "description": "Screenshot investigation is not available in this deployment.",
    },
}

#: Same again for the PDF modality. There is no `502`: a PDF has
#: no upstream site to have failed.
_PDF_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    **_ERROR_RESPONSES,
    503: {
        "model": ErrorEnvelope,
        "description": "PDF investigation is not available in this deployment.",
    },
}

_RETRIEVE_RESPONSES: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorEnvelope, "description": "No stored investigation carries that id."},
    500: {"model": ErrorEnvelope, "description": "The stored run could not be rebuilt."},
}


def _store(repo: InvestigationRepository, state: InvestigationState) -> str:
    """Persist a finished run and return its public id.

    A storage failure is deliberately **not** swallowed. The run itself succeeded,
    so returning `200` while discarding the result would tell the client its
    investigation is safe to look up later when it is not. Letting the exception
    reach the handler produces a `500` the client can retry, which is the honest
    outcome.

    Args:
        repo: The request's repository.
        state: The final state returned by `run_investigation`.

    Returns:
        The Phase 7 public id the run is stored under.
    """
    try:
        repo.save(state)
        repo.commit()
    except Exception:
        repo.rollback()
        logger.exception(
            "Failed to persist investigation", extra={"investigation_id": state.get("investigation_id")}
        )
        raise

    return str(state.get("investigation_id") or "")


def _investigate(
    text: str,
    input_type: InvestigationInputType,
    language: Language,
    context: GraphContext,
    repo: InvestigationRepository,
) -> InvestigationResponse:
    """Run one investigation, store it, and shape the result.

    The run is stored **before** the response is built. A run that completed with
    limitations is still stored: a partial result is a real result, and refusing
    to keep it would make "we could not check this" the one outcome the product
    cannot show you again.

    Args:
        text: The submitted content.
        input_type: The declared input kind.
        language: The requested language, echoed into the response.
        context: The `GraphContext` the run executes against.
        repo: The request's repository.

    Returns:
        The complete investigation response.

    Raises:
        SubmissionRejected: The submission was empty or its type unsupported.
        GraphContractError: A stage broke its documented contract.
    """
    state = run_investigation(text, input_type=input_type, context=context)
    raise_for_graph_errors(
        tuple(state.get("errors") or ()),
        submitted_input_type=input_type.value,
    )
    _store(repo, state)
    return serialize_investigation(state, language=language)


def _investigate_image(
    raw_input: str,
    upload: ImageUpload,
    language: Language,
    context: GraphContext,
    repo: InvestigationRepository,
) -> InvestigationResponse:
    """Run one screenshot investigation, store it, and shape the result.

    Mirrors :func:`_investigate` for the image modality. The submitted
    bytes travel in `upload` rather than in `raw_input`, because a
    screenshot is identified by its content, not by the text the caller
    happened to name it; `raw_input` is only a descriptive reference
    for the run's own logging. Everything else — store before shaping,
    keep a partial run, reuse the write path's adapter — is the same
    contract, so a screenshot result is indistinguishable in kind from
    a text or URL one.

    Args:
        raw_input: A descriptive reference for the submission (the
            filename, when one was supplied).
        upload: The submitted image, bounded by the upload limit.
        language: The requested language, echoed into the response.
        context: The `GraphContext` the run executes against.
        repo: The request's repository.

    Returns:
        The complete investigation response.

    Raises:
        SubmissionRejected: The image was too large, not an image
            type this version reads, or not a readable image.
        CapabilityUnavailable: This deployment does not analyse
            screenshots.
        GraphContractError: A stage broke its documented contract.
    """
    state = run_investigation(
        raw_input,
        input_type=InvestigationInputType.IMAGE,
        context=context,
        upload=upload,
    )
    raise_for_graph_errors(
        tuple(state.get("errors") or ()),
        submitted_input_type=InvestigationInputType.IMAGE.value,
    )
    _store(repo, state)
    return serialize_investigation(state, language=language)


def _investigate_pdf(
    raw_input: str,
    upload: PdfUpload,
    language: Language,
    context: GraphContext,
    repo: InvestigationRepository,
) -> InvestigationResponse:
    """Run one PDF investigation, store it, and shape the result.

    Mirrors :func:`_investigate_image` for the PDF modality. The
    submitted bytes travel in `upload` rather than in `raw_input`,
    because a PDF is identified by its content, not by the text the
    caller happened to name it; `raw_input` is only a descriptive
    reference for the run's own logging. Everything else — store
    before shaping, keep a partial run, reuse the write path's
    adapter — is the same contract, so a PDF result is
    indistinguishable in kind from a text, URL or image one.

    Args:
        raw_input: A descriptive reference for the submission (the
            filename, when one was supplied).
        upload: The submitted PDF, bounded by the upload limit.
        language: The requested language, echoed into the response.
        context: The `GraphContext` the run executes against.
        repo: The request's repository.

    Returns:
        The complete investigation response.

    Raises:
        SubmissionRejected: The PDF was too large, not a media
            type this version reads, or not a readable PDF.
        CapabilityUnavailable: This deployment does not analyse
            PDFs.
        GraphContractError: A stage broke its documented contract.
    """
    state = run_investigation(
        raw_input,
        input_type=InvestigationInputType.PDF,
        context=context,
        pdf_upload=upload,
    )
    raise_for_graph_errors(
        tuple(state.get("errors") or ()),
        submitted_input_type=InvestigationInputType.PDF.value,
    )
    _store(repo, state)
    return serialize_investigation(state, language=language)


@router.post(
    "/investigations/text",
    response_model=InvestigationResponse,
    status_code=status.HTTP_200_OK,
    summary="Investigate submitted text",
    responses=_ERROR_RESPONSES,
)
def investigate_text(
    payload: TextInvestigationRequest,
    context: Annotated[GraphContext, Depends(get_graph_context_dep)],
    repo: Annotated[InvestigationRepository, Depends(get_repository_dep)],
) -> InvestigationResponse:
    """Investigate a block of text and return the complete result.

    Returns `200` even when the run was only partial. The body carries `status`
    (`COMPLETED`, `PARTIAL` or `FAILED`), the `limitations` that explain a
    partial run, and the risk assessment exactly as the risk engine produced it.

    The run is also stored, so the same `investigation_id` can afterwards be
    passed to `GET /api/investigations/{investigation_id}`.
    """
    return _investigate(
        payload.text,
        InvestigationInputType.TEXT,
        payload.language,
        context,
        repo,
    )


@router.post(
    "/investigations",
    response_model=InvestigationResponse,
    status_code=status.HTTP_200_OK,
    summary="Investigate a typed input",
    responses=_ERROR_RESPONSES,
)
def create_investigation(
    payload: InvestigationCreateRequest,
    context: Annotated[GraphContext, Depends(get_graph_context_dep)],
    repo: Annotated[InvestigationRepository, Depends(get_repository_dep)],
) -> InvestigationResponse:
    """Investigate a submission whose input kind is declared explicitly.

    `TEXT` and `URL` are analysed by this version; for `URL` the `text` field
    carries the address. `IMAGE` is analysed too, but through the multipart
    `/investigations/image` endpoint, because a JSON body cannot carry the
    image bytes — an `IMAGE` submission here is recognised and refused with a
    `422` stating that no image was submitted, rather than being analysed as
    prose or as an empty screenshot. `PDF` is recognised and refused with a
    `422` naming the kinds that do work: the product commits to all four
    inputs, but PDF ingestion is later work, and quietly treating a PDF as
    prose would report an analysis of something the client never sent.

    A refused submission is **not** stored. There is no investigation to keep,
    and writing a row for a request that was rejected would fill the history with
    entries describing runs that never happened.
    """
    return _investigate(
        payload.text,
        payload.input_type,
        payload.language,
        context,
        repo,
    )


@router.post(
    "/investigations/url",
    response_model=InvestigationResponse,
    status_code=status.HTTP_200_OK,
    summary="Investigate a submitted website",
    responses=_URL_ERROR_RESPONSES,
)
def investigate_url(
    payload: UrlInvestigationRequest,
    context: Annotated[GraphContext, Depends(get_graph_context_dep)],
    repo: Annotated[InvestigationRepository, Depends(get_repository_dep)],
) -> InvestigationResponse:
    """Retrieve the submitted page and return the investigation of its content.

    The response is the **same shape** as a text investigation, and that is the
    point. The submitted URL is retrieved, its readable content becomes the text
    the pipeline analyses, and every downstream stage is the existing Phase 1-6
    machinery running unchanged. A client that already renders a text result
    renders this one without a special case.

    What is new is `url_source` in the response: the submitted URL, the host
    contacted, the addresses it resolved to, whether it redirected, and whether
    the page was truncated. That provenance is what makes a website result
    checkable — a reader can see exactly which host produced the analysis.

    Returns `200` even for a partial run. A page that is too large, or one whose
    content is built with JavaScript and therefore has no readable text, is a
    real result with a stated limitation rather than a refusal.

    Fails with `422` when the submission itself is the problem — a malformed
    address, a scheme that will not be fetched, or a destination that is not a
    public internet address — with `502` when the request was fine but the site
    could not be read, and with `503` when this deployment does not offer website
    investigation at all. None of those is `500`: nothing has broken.
    """
    return _investigate(
        payload.url,
        InvestigationInputType.URL,
        payload.language,
        context,
        repo,
    )


@router.post(
    "/investigations/image",
    response_model=InvestigationResponse,
    status_code=status.HTTP_200_OK,
    summary="Investigate a submitted screenshot",
    responses=_IMAGE_ERROR_RESPONSES,
)
async def investigate_image(
    file: Annotated[
        UploadFile,
        File(description="The screenshot to investigate (PNG, JPEG or WebP)."),
    ],
    context: Annotated[GraphContext, Depends(get_graph_context_dep)],
    repo: Annotated[InvestigationRepository, Depends(get_repository_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    language: Annotated[Language, Form()] = Language.EN,
) -> InvestigationResponse:
    """Read the submitted screenshot and return the investigation of its text.

    The response is the **same shape** as a text investigation, and that
    is the point. The uploaded image is decoded locally and its text is
    recovered by the OCR engine; the recovered text becomes the input
    every downstream stage analyses, so the pipeline itself is the
    existing Phase 1-6 machinery running unchanged. A client that already
    renders a text result renders this one without a special case.

    What is new is `image_source` in the response: the filename, the
    declared and decoded media types, the decoded dimensions, whether any
    text was recovered, and whether that text was cut at the character
    budget. That provenance is what makes a screenshot result checkable —
    a reader can see exactly which image produced the analysis and how
    much of its text the engine actually read.

    The file is read **bounded**: at most one byte past the upload limit,
    so an oversized upload never reaches memory in full. The limit itself
    is enforced by the OCR service, the same authority that enforces it
    for any other caller, so the route cannot disagree with the service
    about what counts as too large. The declared media type is likewise
    only a first filter — the image library's decode is the authority on
    what the file actually is.

    The recognition work runs in a worker thread, because this endpoint
    is `async` only for the bounded file read; the decode and recognition
    themselves are synchronous and would otherwise hold the event loop
    for the seconds an OCR run can take.

    Returns `200` even for a partial run. A screenshot whose engine is
    absent, or one that is a photograph with no readable text, is a real
    result with a stated limitation rather than a refusal.

    Fails with `422` when the submission itself is the problem — no file,
    a file larger than the limit, a type this version does not read, or
    bytes that do not decode as an image — with `503` when this deployment
    does not offer screenshot investigation at all, and with `500` only
    when a stage broke its documented contract. None of those is a verdict
    about the screenshot's content.
    """
    max_bytes = settings.max_upload_bytes
    raw = await file.read(max_bytes + 1)
    declared = (file.content_type or "application/octet-stream").lower()
    upload = ImageUpload(
        content=raw,
        content_type=declared,
        filename=file.filename,
    )
    return await run_in_threadpool(
        _investigate_image,
        file.filename or "submitted image",
        upload,
        language,
        context,
        repo,
    )


@router.post(
    "/investigations/pdf",
    response_model=InvestigationResponse,
    status_code=status.HTTP_200_OK,
    summary="Investigate a submitted PDF",
    responses=_PDF_ERROR_RESPONSES,
)
async def investigate_pdf(
    file: Annotated[
        UploadFile,
        File(description="The PDF to investigate."),
    ],
    context: Annotated[GraphContext, Depends(get_graph_context_dep)],
    repo: Annotated[InvestigationRepository, Depends(get_repository_dep)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    language: Annotated[Language, Form()] = Language.EN,
) -> InvestigationResponse:
    """Read the submitted PDF and return the investigation of its text.

    The response is the **same shape** as a text investigation, and
    that is the point. The uploaded PDF is parsed locally and its
    text is extracted; the recovered text becomes the input every
    downstream stage analyses, so the pipeline itself is the existing
    Phase 1-6 machinery running unchanged. A client that already
    renders a text result renders this one without a special case.

    What is new is `pdf_source` in the response: the filename, the
    declared and detected media types, the page count, how many pages
    were read, whether any text was recovered, and whether that text
    was cut at the character budget or the page limit. That provenance
    is what makes a PDF result checkable — a reader can see exactly
    which document produced the analysis and how much of it the engine
    actually read.

    The file is read **bounded**: at most one byte past the upload
    limit, so an oversized upload never reaches memory in full. The
    limit itself is enforced by the PDF service, the same authority
    that enforces it for any other caller, so the route cannot
    disagree with the service about what counts as too large. The
    declared media type is likewise only a first filter — the PDF
    library's parse is the authority on what the file actually is.

    The extraction work runs in a worker thread, because this
    endpoint is `async` only for the bounded file read; the parse and
    extraction themselves are synchronous and would otherwise hold the
    event loop for the seconds a large document can take.

    Returns `200` even for a partial run. A PDF whose library is
    absent, a scan with no recoverable text, or a document longer
    than the page limit, is a real result with a stated limitation
    rather than a refusal.

    Fails with `422` when the submission itself is the problem — no
    file, a file larger than the limit, a type this version does not
    read, or bytes that do not parse as a PDF — with `503` when this
    deployment does not offer PDF investigation at all, and with `500`
    only when a stage broke its documented contract. None of those is
    a verdict about the document's content.
    """
    max_bytes = settings.max_upload_bytes
    raw = await file.read(max_bytes + 1)
    declared = (file.content_type or "application/octet-stream").lower()
    upload = PdfUpload(
        content=raw,
        content_type=declared,
        filename=file.filename,
    )
    return await run_in_threadpool(
        _investigate_pdf,
        file.filename or "submitted pdf",
        upload,
        language,
        context,
        repo,
    )


@router.get(
    "/investigations/limits",
    summary="Input types this version analyses",
)
def investigation_limits(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, object]:
    """Report which input types are accepted today, and their limits.

    Exists so a client can discover the constraint before building a
    submission, rather than after a rejection. Cheap, needs no
    investigation, and never fails.

    The image limits are the ones the OCR service actually enforces:
    the byte limit is read from the settings the app was built with,
    and the accepted media types are the formats the image library can
    decode — the authority on what counts as a readable image — rather
    than the broader upload policy, which also names formats this
    version analyses through a different endpoint.

    The PDF limits are the ones the PDF service actually enforces: the
    accepted media type is the one document format this version
    analyses, and the page limit bounds how many pages of a document
    are read, so a client knows a long PDF is read partly rather than
    wholly.

    Declared **before** `/investigations/{investigation_id}` on purpose.
    FastAPI matches routes in declaration order, so a path parameter
    registered first would swallow this one and answer a request for
    the limits with a `404` about a missing investigation.
    """
    return {
        "supported_input_types": [kind.value for kind in SUPPORTED_INPUT_TYPES],
        "max_text_length": MAX_TEXT_LENGTH,
        "max_url_length": MAX_URL_LENGTH,
        "max_upload_bytes": settings.max_upload_bytes,
        "allowed_image_types": list(OCR_IMAGE_FORMATS.values()),
        "allowed_pdf_types": list(PDF_CONTENT_TYPES),
        "pdf_max_pages": settings.pdf_max_pages,
        "ocr_languages": settings.ocr_languages,
        "languages": [language.value for language in Language],
        "translation_enabled": False,
    }


@router.get(
    "/investigations",
    response_model=InvestigationListResponse,
    summary="List stored investigations",
)
def list_investigations(
    repo: Annotated[InvestigationRepository, Depends(get_repository_dep)],
    limit: Annotated[
        int, Query(ge=1, le=100, description="Maximum entries to return.")
    ] = 20,
    offset: Annotated[
        int, Query(ge=0, description="Entries to skip, for paging.")
    ] = 0,
) -> InvestigationListResponse:
    """Return stored runs, newest first.

    Reads only the `investigations` table. The per-kind counts are stored on the
    run rather than aggregated here, so listing a hundred runs costs one indexed
    query instead of a thousand joins — which is what keeps this endpoint a
    listing rather than a report.

    Ordered by when each run was **stored**, not by when it started, with the
    surrogate key as a tie-break. Two runs of the same content share a public id
    and can start in the same millisecond; without a total order a client paging
    through the history would see a row twice or skip one.
    """
    page, total = repo.list_page(limit=limit, offset=offset)
    return InvestigationListResponse(
        investigations=[_summary(item) for item in page],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/investigations/{investigation_id}",
    response_model=InvestigationResponse,
    summary="Retrieve a stored investigation",
    responses=_RETRIEVE_RESPONSES,
)
def get_investigation(
    investigation_id: str,
    repo: Annotated[InvestigationRepository, Depends(get_repository_dep)],
) -> InvestigationResponse:
    """Return a stored run, rebuilt exactly as the write path shaped it.

    The response body is produced by the same adapter the `POST` endpoints use,
    from an `InvestigationState` the repository rebuilt from the rows. That is
    the whole guarantee this endpoint makes: a retrieved investigation is
    indistinguishable from the live one, because it is built by the same code
    from the same objects.

    Where the same content has been investigated more than once, the **most
    recent** run is returned. `investigation_id` is a digest of the submitted
    text, so a re-run cannot get a new id; the history endpoint is where every
    run remains visible.

    `404` when nothing is stored under that id. The wording is fixed and the
    body carries no detail, so this endpoint cannot be used to probe which ids
    exist.
    """
    try:
        state = repo.load(investigation_id)
    except NotFound as exc:
        raise InvestigationNotFound(
            "INVESTIGATION_NOT_FOUND",
            "No stored investigation carries that id.",
        ) from exc
    except ApiError:
        raise
    except Exception as exc:
        # A row that cannot be rebuilt is a defect in the mapping, not in the
        # caller's request. The exception's detail is logged, never returned.
        logger.exception(
            "Failed to rebuild stored investigation",
            extra={"investigation_id": investigation_id},
        )
        raise

    return serialize_investigation(state)


def _summary(item: InvestigationSummary) -> InvestigationSummaryResponse:
    """Shape one stored row as a history entry.

    Args:
        item: The stored run's summary.

    Returns:
        The response model for that entry.
    """
    return InvestigationSummaryResponse(
        investigation_id=item.investigation_id,
        status=InvestigationStatus(item.status),
        input_type=InvestigationInputType(item.input_type),
        current_stage=item.current_stage,
        started_at=item.started_at,
        completed_at=item.completed_at,
        created_at=item.created_at,
        claim_count=item.claim_count,
        entity_count=item.entity_count,
        red_flag_count=item.red_flag_count,
        verification_count=item.verification_count,
        source_count=item.source_count,
        evidence_count=item.evidence_count,
        factor_count=item.factor_count,
    )


__all__ = ["router"]
