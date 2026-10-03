"""Adapter turning a final `InvestigationState` into an API response (Phase 8).

The graph knows what it found; this module knows how to say it over HTTP. That
split is the reason the file exists. Without it, a route handler would have to
reach into a `TypedDict` with eleven optional keys and decide for itself what a
missing key means — and the first route to guess "empty list" would quietly turn
"verification never ran" into "verification found nothing".

The one piece of interpretation here is the top-level `status`, and it is
deliberately mechanical: it is read out of the stage timeline, which already
distinguishes `PARTIAL` from `COMPLETED` for exactly this reason. This module
does not decide *which* limitations matter — the graph already decided that when
it chose a stage status. It also does not filter the warnings: every recorded
`GraphWarning` code is surfaced, because a client asking "what did this run not
manage to do" must be able to answer "all of it" (D-006, D-009).

No judgement, score or wording is invented here. In particular `RiskAssessment`
is forwarded as the Phase 6 object it is, caveats intact — a heuristic indicator
count must not be restated as a probability on its way out to a client (D-025).
"""

from __future__ import annotations

from app.graph.state import (
    GraphError,
    GraphWarning,
    InvestigationInputType,
    InvestigationState,
    TimelineEvent,
    TimelineStatus,
)
from app.schemas.api import (
    InvestigationErrorResponse,
    InvestigationResponse,
    InvestigationStatus,
    Language,
    LimitationResponse,
    TimelineEventResponse,
)

__all__ = [
    "dedupe_codes",
    "investigation_status",
    "serialize_investigation",
]

#: Timeline statuses that mean a stage did not finish its work. `SKIPPED` is
#: included because a stage that was skipped had its job done by no one else.
_INCOMPLETE_STAGE_STATUSES = frozenset({TimelineStatus.PARTIAL, TimelineStatus.SKIPPED})


def dedupe_codes(warnings: tuple[GraphWarning, ...]) -> list[str]:
    """Reduce warning objects to their codes, each appearing once.

    Order is first-seen, not sorted. The order the graph recorded them in is the
    order the run happened in, which is the order that explains a result; sorting
    would replace a narrative with an alphabet.

    Args:
        warnings: The accumulated :class:`~app.graph.state.GraphWarning` values.

    Returns:
        Unique codes in first-seen order. The same code can legitimately be
        recorded by two different stages, which is why deduplication matters.
    """
    seen: set[str] = set()
    codes: list[str] = []
    for warning in warnings:
        if warning.code in seen:
            continue
        seen.add(warning.code)
        codes.append(warning.code)
    return codes


def investigation_status(
    errors: tuple[GraphError, ...],
    timeline: tuple[TimelineEvent, ...],
) -> InvestigationStatus:
    """Derive the top-level run status from the graph's own timeline.

    A recorded error ends the run, so it decides first and unconditionally. Only
    then does the timeline matter: a run with a `PARTIAL` or `SKIPPED` stage is a
    partial result, and one whose stages all completed is complete.

    Note what this does *not* do. It does not look at the warnings list, because
    the graph already classified every degradation when it wrote a stage status,
    and some warnings are not degradations at all — `NO_RED_FLAGS_DETECTED` is
    recorded on content that matched no pattern, and treating that as an
    incomplete run would report a clean investigation as a degraded one.

    Args:
        errors: The accumulated :class:`~app.graph.state.GraphError` values.
        timeline: The accumulated :class:`~app.graph.state.TimelineEvent` values.

    Returns:
        `FAILED` if anything failed, `PARTIAL` if any stage was incomplete,
        otherwise `COMPLETED`.
    """
    if errors:
        return InvestigationStatus.FAILED
    if any(event.status in _INCOMPLETE_STAGE_STATUSES for event in timeline):
        return InvestigationStatus.PARTIAL
    return InvestigationStatus.COMPLETED


def serialize_investigation(
    state: InvestigationState,
    *,
    language: Language = Language.EN,
) -> InvestigationResponse:
    """Build the API response for one finished investigation.

    Absent keys become empty lists rather than being omitted, so a client can
    read `claims.length` without first checking whether the field exists. The
    distinction that must survive is preserved by `status` and `limitations`: a
    client can always tell a stage that ran and found nothing from a stage that
    never ran.

    Args:
        state: The final state returned by `run_investigation`.
        language: The language the caller asked for. Echoed, not translated.

    Returns:
        A fully populated :class:`~app.schemas.api.InvestigationResponse`.
    """
    warnings = tuple(state.get("warnings") or ())
    errors = tuple(state.get("errors") or ())
    timeline = tuple(state.get("timeline") or ())

    return InvestigationResponse(
        investigation_id=state.get("investigation_id") or "",
        status=investigation_status(errors, timeline),
        input_type=_input_type(state.get("input_type")),
        language=language,
        current_stage=state.get("current_stage") or "",
        url_source=state.get("url_source"),
        claims=list(state.get("claims") or ()),
        entities=list(state.get("entities") or ()),
        red_flags=list(state.get("red_flags") or ()),
        verification_results=list(state.get("verification_results") or ()),
        evidence=list(state.get("evidence") or ()),
        risk_assessment=state.get("risk_assessment"),
        timeline=[_timeline_event(event) for event in timeline],
        limitations=dedupe_codes(warnings),
        warnings=[_limitation(warning) for warning in warnings],
        errors=[_graph_error(error) for error in errors],
        started_at=state.get("started_at"),
        completed_at=state.get("completed_at"),
    )


def _input_type(declared: str | None) -> InvestigationInputType:
    """Coerce the state's input type to the enum, defaulting to `TEXT`.

    Args:
        declared: The `input_type` recorded by the input node.

    Returns:
        The matching enum member, or `TEXT` when the run never reached the stage
        that records it.
    """
    try:
        return InvestigationInputType(declared or InvestigationInputType.TEXT.value)
    except ValueError:
        return InvestigationInputType.TEXT


def _limitation(warning: GraphWarning) -> LimitationResponse:
    """Shape one warning, dropping its log-side diagnostic field.

    Args:
        warning: A recorded warning.

    Returns:
        The code, stage and message a client can display.
    """
    return LimitationResponse(code=warning.code, stage=warning.stage, message=warning.message)


def _graph_error(error: GraphError) -> InvestigationErrorResponse:
    """Shape one recorded failure.

    Args:
        error: A recorded error.

    Returns:
        The code, stage, message and exception type.
    """
    return InvestigationErrorResponse(
        code=error.code,
        stage=error.stage,
        message=error.message,
        error_type=error.error_type,
    )


def _timeline_event(event: TimelineEvent) -> TimelineEventResponse:
    """Shape one timeline entry.

    Args:
        event: A recorded timeline event.

    Returns:
        The stage, status, message and timestamp.
    """
    return TimelineEventResponse(
        stage=event.stage,
        status=event.status,
        message=event.message,
        at=event.at,
    )
