"""Investigation endpoints (Phase 8).

Three endpoints, deliberately thin. Each validates the submission, calls
`run_investigation`, and hands the final state to the adapter — no threshold,
score, verdict or wording is decided here.

- `POST /api/investigations/text` — the documented convenience form. `input_type`
  is fixed to `TEXT`, so a client cannot accidentally submit a URL and have it
  analysed as prose.
- `POST /api/investigations` — the typed form, where `input_type` is explicit.
- `GET /api/investigations/limits` — what this version accepts, so a client can
  find out before submitting rather than after being refused.

The run is **synchronous and complete**. The earlier API spec sketched a `202`
with a job handle plus `GET /api/investigations/{id}`, and that design is
deliberately not implemented: it presupposes persistence, which is Phase 9, and
returning an id with nothing behind it would invite a client to build a lookup
that cannot succeed. See `docs/API_SPEC.md` for the reconciliation.

Because the result is returned whole, a run that completed with limitations is a
`200` carrying `status: PARTIAL` and the limitation codes. A search provider
being down is not a failed request.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.adapters import serialize_investigation
from app.api.deps import get_graph_context_dep
from app.api.errors import raise_for_graph_errors
from app.core.logging import get_logger
from app.graph.context import GraphContext
from app.graph.investigation_graph import run_investigation
from app.graph.state import InvestigationInputType
from app.schemas.api import (
    MAX_TEXT_LENGTH,
    ErrorEnvelope,
    InvestigationCreateRequest,
    InvestigationResponse,
    Language,
    TextInvestigationRequest,
)

logger = get_logger(__name__)

router = APIRouter(tags=["investigations"])

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    422: {"model": ErrorEnvelope, "description": "The submission could not be accepted."},
    500: {"model": ErrorEnvelope, "description": "A stage broke its documented contract."},
}


def _investigate(
    text: str,
    input_type: InvestigationInputType,
    language: Language,
    context: GraphContext,
) -> InvestigationResponse:
    """Run one investigation and shape the result.

    Args:
        text: The submitted content.
        input_type: The declared input kind.
        language: The requested language, echoed into the response.
        context: The `GraphContext` the run executes against.

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
) -> InvestigationResponse:
    """Investigate a block of text and return the complete result.

    Returns `200` even when the run was only partial. The body carries `status`
    (`COMPLETED`, `PARTIAL` or `FAILED`), the `limitations` that explain a
    partial run, and the risk assessment exactly as the risk engine produced it.
    """
    return _investigate(
        payload.text,
        InvestigationInputType.TEXT,
        payload.language,
        context,
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
) -> InvestigationResponse:
    """Investigate a submission whose input kind is declared explicitly.

    Only `TEXT` is analysed by this version. `URL`, `IMAGE` and `PDF` are
    recognised and refused with a `422` naming the kinds that do work, rather
    than being analysed as text — the product commits to all four inputs, but the
    ingestion for the other three is later work, and quietly treating a URL as
    prose would report an analysis of something the client never sent.
    """
    return _investigate(
        payload.text,
        payload.input_type,
        payload.language,
        context,
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
    """
    return {
        "supported_input_types": [InvestigationInputType.TEXT.value],
        "max_text_length": MAX_TEXT_LENGTH,
        "languages": [language.value for language in Language],
        "translation_enabled": False,
    }


__all__ = ["router"]
