"""Investigation endpoints (Phase 8, extended by Phase 9).

Five endpoints, deliberately thin. Each validates the submission, calls
`run_investigation`, hands the final state to the adapter, and — for the write
endpoints — stores it. No threshold, score, verdict or wording is decided here.

- `POST /api/investigations/text` — the documented convenience form. `input_type`
  is fixed to `TEXT`, so a client cannot accidentally submit a URL and have it
  analysed as prose.
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

from fastapi import APIRouter, Depends, Query, status

from app.api.adapters import serialize_investigation
from app.api.deps import get_graph_context_dep, get_repository_dep
from app.api.errors import ApiError, InvestigationNotFound, raise_for_graph_errors
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
)

logger = get_logger(__name__)

router = APIRouter(tags=["investigations"])

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    422: {"model": ErrorEnvelope, "description": "The submission could not be accepted."},
    500: {"model": ErrorEnvelope, "description": "A stage broke its documented contract."},
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

    Only `TEXT` is analysed by this version. `URL`, `IMAGE` and `PDF` are
    recognised and refused with a `422` naming the kinds that do work, rather
    than being analysed as text — the product commits to all four inputs, but the
    ingestion for the other three is later work, and quietly treating a URL as
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


@router.get(
    "/investigations/limits",
    summary="Input types this version analyses",
)
def investigation_limits() -> dict[str, object]:
    """Report which input types are accepted today.

    Exists so a client can discover the constraint before building a submission,
    rather than after a rejection. Cheap, needs no investigation, and never
    fails.

    Declared **before** `/investigations/{investigation_id}` on purpose. FastAPI
    matches routes in declaration order, so a path parameter registered first
    would swallow this one and answer a request for the limits with a `404`
    about a missing investigation.
    """
    return {
        "supported_input_types": [InvestigationInputType.TEXT.value],
        "max_text_length": MAX_TEXT_LENGTH,
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
