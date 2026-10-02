"""Investigation graph nodes (Phase 7).

Each function here does exactly one thing: read the state, call one existing
service, and return the partial state that service's output implies. No node
contains a rule, a prompt, a regex, a threshold or a score. If one of these
functions ever grew business logic, the phase boundary between "what was found"
and "what runs in what order" would be gone, and Phases 1-6 could no longer be
tested or replaced without rewriting the graph.

The pipeline:

```
input_processor ──► extraction ──► red_flags ──► verification ──► evidence ──► risk
      │                 │
      │                 └── no claims extracted: skip straight to risk
      └── invalid input: stop with a structured error
```

## Failure policy

The distinction that matters is **typed degradation versus contract violation**.

Phase 3, 4 and 5 already convert real-world problems into typed outcomes: a
missing key becomes `SEARCH_UNAVAILABLE`, a provider timeout becomes
`SEARCH_FAILED`, a claim that cannot be checked becomes
`INSUFFICIENT_EVIDENCE`. Those arrive as *values*, so the graph records them as
warnings and keeps going. A `SERPAPI` outage must not end an investigation.

An exception escaping a service is the opposite. Phase 2 documents that `extract`
never raises and Phase 4 documents that a provider failure never propagates, so
an escaping exception means the contract is broken — not that the world is
difficult. Continuing would produce a plausible-looking investigation built on a
stage that silently did nothing, and a red-flag stage that fails quietly scores
the content as though nothing were wrong with it. That understates risk, which
is the one direction this product must never err in. So a contract violation ends
the run with a structured `GraphError`.

The evidence stage is the deliberate exception. Phase 6 established that evidence
never contributes to the score and only attaches provenance ids, so losing it
degrades the *explanation* of an assessment without changing the assessment. It
is recorded as a warning, logged in full, and the run continues. That is a
deliberate, documented asymmetry, not an oversight.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.graph.context import GraphContext
from app.graph.state import (
    GraphError,
    GraphStage,
    GraphWarning,
    InvestigationInputType,
    InvestigationState,
    TimelineEvent,
    TimelineStatus,
    investigation_id_for,
)
from app.schemas.extraction import ExtractionMode, ExtractionResult
from app.schemas.verification import VerificationResponse, VerificationStatus

logger = get_logger(__name__)

__all__ = [
    "ERROR_CODES",
    "WARNING_CODES",
    "evidence_node",
    "extraction_node",
    "input_node",
    "red_flags_node",
    "risk_node",
    "verification_node",
]

#: Stable limitation codes. A caller branches on these rather than parsing
#: English, so wording can change without breaking anyone (D-009).
WARNING_CODES: tuple[str, ...] = (
    "INPUT_TYPE_NOT_ANALYSED",
    "EXTRACTION_FALLBACK",
    "EXTRACTION_PARTIAL",
    "NO_CLAIMS_EXTRACTED",
    "NO_RED_FLAGS_DETECTED",
    "SEARCH_UNAVAILABLE",
    "PARTIAL_VERIFICATION",
    "EVIDENCE_UNAVAILABLE",
    "SEARCH_RESULTS_NOT_RECORDED",
)

#: Stable failure codes. Any of these ends the run.
ERROR_CODES: tuple[str, ...] = (
    "INPUT_EMPTY",
    "INPUT_TYPE_NOT_SUPPORTED",
    "EXTRACTION_FAILED",
    "RED_FLAG_DETECTION_FAILED",
    "VERIFICATION_FAILED",
    "RISK_ASSESSMENT_FAILED",
)

#: Fixed wording per limitation. Each says what did not happen, never what it
#: means about the content or anyone named in it.
WARNING_MESSAGES: dict[str, str] = {
    "INPUT_TYPE_NOT_ANALYSED": (
        "This investigation analysed only text input. The submitted input type is "
        "not analysed by this version, so no analysis of its content was performed."
    ),
    "EXTRACTION_FALLBACK": (
        "Claims and entities were extracted without the language model, using "
        "deterministic patterns only, so the extraction is weaker than a "
        "model-assisted one."
    ),
    "EXTRACTION_PARTIAL": (
        "Some part of the extraction did not complete. The claims and entities "
        "that were extracted are reported, and the omitted content was not."
    ),
    "NO_CLAIMS_EXTRACTED": (
        "No claims were extracted from the submitted content, so there was nothing "
        "to check against external records. Content patterns were still analysed."
    ),
    "NO_RED_FLAGS_DETECTED": (
        "No content patterns from the project's rule set were found in the "
        "submitted text. This reports the absence of those patterns and nothing "
        "further about the content."
    ),
    "SEARCH_UNAVAILABLE": (
        "External search was unavailable, so no external record could be consulted "
        "for any claim."
    ),
    "PARTIAL_VERIFICATION": (
        "At least one claim could not be checked against external records. Its "
        "status records the reason, and no conclusion about it can be drawn."
    ),
    "EVIDENCE_UNAVAILABLE": (
        "The evidence stage did not complete for every claim, so some retrieved "
        "text cannot be shown with its source. The claims and their recorded "
        "verification statuses are unaffected."
    ),
    "SEARCH_RESULTS_NOT_RECORDED": (
        "The searches performed during verification were not recorded, so the "
        "query behind each retrieved document could not be traced. Retrieved text "
        "is still shown with its source."
    ),
}

#: Fixed wording per failure. Safe to surface: it describes the pipeline, never
#: the content, and carries no provider detail.
ERROR_MESSAGES: dict[str, str] = {
    "INPUT_EMPTY": (
        "No content was submitted, so there was nothing to investigate."
    ),
    "INPUT_TYPE_NOT_SUPPORTED": (
        "This version analyses text input only. The submitted input type was "
        "recognised but is not analysed, so no investigation was performed."
    ),
    "EXTRACTION_FAILED": (
        "The extraction stage did not complete, so no claims or entities were "
        "available and the investigation stopped here."
    ),
    "RED_FLAG_DETECTION_FAILED": (
        "Content pattern detection did not complete, so the investigation stopped "
        "rather than report a partial pattern analysis as a complete one."
    ),
    "VERIFICATION_FAILED": (
        "The verification stage did not complete as expected, so the investigation "
        "stopped rather than report unchecked claims as checked."
    ),
    "RISK_ASSESSMENT_FAILED": (
        "The risk stage did not complete, so no risk assessment is reported for "
        "this investigation."
    ),
}


# -- helpers -------------------------------------------------------------


def _event(
    context: GraphContext,
    stage: GraphStage,
    status: TimelineStatus,
    message: str,
) -> TimelineEvent:
    """Build a timeline event stamped with the context's clock."""
    return TimelineEvent(
        stage=stage,
        status=status,
        message=message,
        at=context.clock(),
    )


def _warning(stage: GraphStage, code: str, *, error_type: str | None = None) -> GraphWarning:
    """Build a warning from the fixed message for `code`.

    Args:
        stage: The stage the limitation is attributed to.
        code: A key of `WARNING_MESSAGES`.
        error_type: Exception class name when the limitation is a service fault.

    Returns:
        A `GraphWarning`. The wording comes from the fixed table, never from the
        exception, so no provider detail or stack frame can reach it.
    """
    return GraphWarning(
        code=code,
        stage=stage,
        message=WARNING_MESSAGES[code],
        error_type=error_type,
    )


def _error(stage: GraphStage, code: str, error_type: str | None) -> GraphError:
    """Build a structured error from the fixed message for `code`.

    Args:
        stage: The stage that failed.
        code: A key of `ERROR_MESSAGES`.
        error_type: The exception class name, kept for diagnosis. The message
            itself never reaches the result.

    Returns:
        A `GraphError` whose wording comes from the fixed table.
    """
    return GraphError(
        code=code,
        stage=stage,
        message=ERROR_MESSAGES[code],
        error_type=error_type,
    )


def _text_for(state: InvestigationState) -> str:
    """Return the text red-flag spans must be anchored to.

    `raw_input`, not `extracted_text`: a `RedFlag`'s `EvidenceSpan` indexes the
    string the user submitted, so a span computed over a normalized variant would
    point at the wrong characters. Phase 2's normalized text is kept in the state
    for traceability but is never substituted here.
    """
    return state.get("raw_input") or ""


def _warnings_from(statuses: tuple[VerificationStatus, ...]) -> tuple[str, ...]:
    """Warning codes implied by a set of verification statuses.

    Only statuses that mean *coverage was lost* produce a warning. `VERIFIED` and
    `NOT_APPLICABLE` are complete answers, not gaps.
    """
    codes: list[str] = []
    if any(
        status
        in (VerificationStatus.UNVERIFIED, VerificationStatus.INSUFFICIENT_EVIDENCE)
        for status in statuses
    ):
        codes.append("PARTIAL_VERIFICATION")
    return tuple(codes)


# -- nodes ---------------------------------------------------------------


def input_node(state: InvestigationState, context: GraphContext) -> dict[str, Any]:
    """Validate the submission and open the investigation record.

    The only job here is to establish that there is something to investigate.
    Normalization belongs to Phase 2, which already does it, so nothing is
    rewritten or trimmed here — an empty or unusable submission must be visible
    as submitted rather than quietly repaired.

    Args:
        state: The incoming state. Requires `raw_input` and `input_type`.
        context: Services and clock.

    Returns:
        Partial state with `investigation_id`, `started_at`, `current_stage`, and
        either an `errors` entry or a timeline `STARTED` event.
    """
    raw_input = state.get("raw_input") or ""
    declared = state.get("input_type") or InvestigationInputType.TEXT.value

    try:
        input_type = InvestigationInputType(declared)
    except ValueError:
        logger.warning(
            "Unrecognised input type submitted",
            extra={"input_type": declared},
        )
        return {
            "current_stage": GraphStage.INPUT.value,
            "errors": (
                _error(GraphStage.INPUT, "INPUT_TYPE_NOT_SUPPORTED", None),
            ),
            "timeline": (
                _event(
                    context,
                    GraphStage.INPUT,
                    TimelineStatus.FAILED,
                    "Submission was not accepted.",
                ),
            ),
        }

    if input_type is not InvestigationInputType.TEXT:
        logger.info(
            "Input type recognised but not analysed by this version",
            extra={"input_type": input_type.value},
        )
        return {
            "input_type": input_type.value,
            "investigation_id": investigation_id_for(input_type, raw_input),
            "current_stage": GraphStage.INPUT.value,
            "started_at": context.clock(),
            "warnings": (
                _warning(GraphStage.INPUT, "INPUT_TYPE_NOT_ANALYSED"),
            ),
            "errors": (
                _error(GraphStage.INPUT, "INPUT_TYPE_NOT_SUPPORTED", None),
            ),
            "timeline": (
                _event(
                    context,
                    GraphStage.INPUT,
                    TimelineStatus.FAILED,
                    "Submission was recognised but not analysed by this version.",
                ),
            ),
        }

    if not raw_input.strip():
        logger.info("Empty submission received")
        return {
            "input_type": input_type.value,
            "current_stage": GraphStage.INPUT.value,
            "started_at": context.clock(),
            "errors": (_error(GraphStage.INPUT, "INPUT_EMPTY", None),),
            "timeline": (
                _event(
                    context,
                    GraphStage.INPUT,
                    TimelineStatus.FAILED,
                    "No content was submitted.",
                ),
            ),
        }

    return {
        "input_type": input_type.value,
        "raw_input": raw_input,
        "extracted_text": raw_input,
        "investigation_id": investigation_id_for(input_type, raw_input),
        "started_at": context.clock(),
        "current_stage": GraphStage.INPUT.value,
        "timeline": (
            _event(
                context,
                GraphStage.INPUT,
                TimelineStatus.STARTED,
                "Content received.",
            ),
        ),
    }


def extraction_node(state: InvestigationState, context: GraphContext) -> dict[str, Any]:
    """Run Phase 2 extraction over the submitted text.

    Args:
        state: State carrying `raw_input`.
        context: Services and clock.

    Returns:
        Partial state with claims, entities, links and the extraction mode. A
        degraded extraction is a *warning*, not a failure: Phase 2 degrades to
        deterministic patterns on purpose, and those patterns are still worth
        reporting. An exception escaping the service is a contract violation and
        stops the run.
    """
    stage = GraphStage.EXTRACTION
    raw_input = state.get("raw_input") or ""
    dependencies = context.dependencies

    try:
        result: ExtractionResult = dependencies.extraction_service.extract(raw_input)
    except Exception as exc:
        logger.exception("Extraction service violated its no-raise contract")
        return {
            "current_stage": stage.value,
            "claims": (),
            "entities": (),
            "claim_entity_links": (),
            "errors": (
                _error(stage, "EXTRACTION_FAILED", type(exc).__name__),
            ),
            "timeline": (
                _event(
                    context,
                    stage,
                    TimelineStatus.FAILED,
                    "Extraction did not complete.",
                ),
            ),
        }

    warnings: list[GraphWarning] = []
    for message in result.processing_warnings:
        warnings.append(
            GraphWarning(
                code="EXTRACTION_PARTIAL",
                stage=stage,
                message=message,
            )
        )

    if not result.claims:
        warnings.append(_warning(stage, "NO_CLAIMS_EXTRACTED"))

    if result.extraction_mode is ExtractionMode.FALLBACK and result.claims:
        warnings.append(_warning(stage, "EXTRACTION_FALLBACK"))

    status = (
        TimelineStatus.PARTIAL
        if warnings
        else TimelineStatus.COMPLETED
    )

    return {
        "current_stage": stage.value,
        "extraction": result,
        "claims": tuple(result.claims),
        "entities": tuple(result.entities),
        "claim_entity_links": tuple(result.relationships),
        "extraction_mode": result.extraction_mode,
        "extracted_text": result.normalized_text or raw_input,
        "warnings": tuple(warnings),
        "timeline": (
            _event(
                context,
                stage,
                status,
                f"Extracted {len(result.claims)} claim(s) and "
                f"{len(result.entities)} entit(ies).",
            ),
        ),
    }


def red_flags_node(state: InvestigationState, context: GraphContext) -> dict[str, Any]:
    """Run Phase 1 deterministic pattern detection.

    Args:
        state: State carrying `raw_input`, whose spans the results index.
        context: Services and clock.

    Returns:
        Partial state with `red_flags`. No score is computed here; that is
        Phase 6's job and it needs claims and verification too.
    """
    stage = GraphStage.RED_FLAGS
    text = _text_for(state)

    try:
        flags = context.dependencies.red_flag_engine.detect(text)
    except Exception as exc:
        logger.exception("Red flag engine raised during detection")
        return {
            "current_stage": stage.value,
            "red_flags": (),
            "errors": (
                _error(stage, "RED_FLAG_DETECTION_FAILED", type(exc).__name__),
            ),
            "timeline": (
                _event(
                    context,
                    stage,
                    TimelineStatus.FAILED,
                    "Content pattern detection did not complete.",
                ),
            ),
        }

    detected = tuple(flags)
    warnings: tuple[GraphWarning, ...] = ()
    if not detected:
        warnings = (_warning(stage, "NO_RED_FLAGS_DETECTED"),)

    return {
        "current_stage": stage.value,
        "red_flags": detected,
        "warnings": warnings,
        "timeline": (
            _event(
                context,
                stage,
                TimelineStatus.COMPLETED,
                f"Detected {len(detected)} content pattern(s).",
            ),
        ),
    }


def verification_node(state: InvestigationState, context: GraphContext) -> dict[str, Any]:
    """Run Phase 4 verification over every extracted claim.

    The graph passes claims and entities and does nothing else: it does not
    build a query, call a provider, classify a source or decide a status. Phase 4
    owns all of that, and the searches it performs are recorded so the evidence
    stage can cite them.

    Args:
        state: State carrying `claims` and `entities`.
        context: Services and clock.

    Returns:
        Partial state with `verification` and `verification_results`, plus
        warnings for lost coverage. A claim that could not be checked is a
        covered case, not a failure.
    """
    stage = GraphStage.VERIFICATION
    dependencies = context.dependencies
    claims = tuple(state.get("claims") or ())
    entities = tuple(state.get("entities") or ())

    if not claims:
        return {
            "current_stage": stage.value,
            "verification": VerificationResponse(),
            "verification_results": (),
            "timeline": (
                _event(
                    context,
                    stage,
                    TimelineStatus.SKIPPED,
                    "No claims were extracted, so no claim was checked against "
                    "external records.",
                ),
            ),
        }

    dependencies.clear_search_recording()

    try:
        response: VerificationResponse = (
            dependencies.verification_service.verify_claims(claims, entities)
        )
    except Exception as exc:
        logger.exception("Verification service violated its no-raise contract")
        return {
            "current_stage": stage.value,
            "verification_results": (),
            "errors": (
                _error(stage, "VERIFICATION_FAILED", type(exc).__name__),
            ),
            "timeline": (
                _event(
                    context,
                    stage,
                    TimelineStatus.FAILED,
                    "Verification did not complete.",
                ),
            ),
        }

    results = tuple(response.results)
    statuses = tuple(result.status for result in results)

    warnings: list[GraphWarning] = []
    if dependencies.search_recorder is None:
        warnings.append(_warning(stage, "SEARCH_RESULTS_NOT_RECORDED"))
    elif not dependencies.search_recorder.available:
        warnings.append(_warning(stage, "SEARCH_UNAVAILABLE"))
    for code in _warnings_from(statuses):
        warnings.append(_warning(stage, code))

    status = (
        TimelineStatus.PARTIAL if warnings else TimelineStatus.COMPLETED
    )

    return {
        "current_stage": stage.value,
        "verification": response,
        "verification_results": results,
        "warnings": tuple(warnings),
        "timeline": (
            _event(
                context,
                stage,
                status,
                f"Checked {len(results)} claim(s) against external records.",
            ),
        ),
    }


def evidence_node(state: InvestigationState, context: GraphContext) -> dict[str, Any]:
    """Assemble Phase 5 evidence for the claims that were verified.

    The Phase 3 results handed to the service are **the responses Phase 4
    retrieved**, recovered from the recorder by matching each result's recorded
    queries. The graph issues no search of its own, so a document can only reach
    the evidence stage if verification actually saw it (D-023).

    Args:
        state: State carrying claims, entities, verification and the recorder.
        context: Services and clock.

    Returns:
        Partial state with `evidence`. A failure here is a warning rather than a
        failure: Phase 6 established that evidence contributes no weight, so
        losing it removes provenance without changing the score.
    """
    stage = GraphStage.EVIDENCE
    dependencies = context.dependencies

    if not state.get("claims"):
        return {
            "current_stage": stage.value,
            "evidence": (),
            "timeline": (
                _event(
                    context,
                    stage,
                    TimelineStatus.SKIPPED,
                    "No claims were extracted, so no evidence was assembled.",
                ),
            ),
        }

    verification = state.get("verification")
    if verification is None:
        verification = VerificationResponse()

    results_by_claim: dict[str, Any] = {}
    responses: tuple[object, ...] = ()
    if dependencies.search_recorder is not None:
        results_by_claim = dependencies.search_recorder.results_by_claim(
            tuple(state.get("verification_results") or ())
        )
        responses = dependencies.search_recorder.responses

    try:
        bundle = dependencies.evidence_service.build_all(
            tuple(state.get("claims") or ()),
            verification,
            results_by_claim,
            tuple(state.get("entities") or ()),
            responses=responses,
        )
    except Exception as exc:
        logger.exception("Evidence service raised during assembly")
        return {
            "current_stage": stage.value,
            "evidence": (),
            "warnings": (
                _warning(
                    stage, "EVIDENCE_UNAVAILABLE", error_type=type(exc).__name__
                ),
            ),
            "timeline": (
                _event(
                    context,
                    stage,
                    TimelineStatus.PARTIAL,
                    "Evidence could not be assembled for every claim.",
                ),
            ),
        }

    assembled = tuple(bundle.responses)
    item_count = sum(len(response.evidence) for response in assembled)

    return {
        "current_stage": stage.value,
        "evidence": assembled,
        "timeline": (
            _event(
                context,
                stage,
                TimelineStatus.COMPLETED,
                f"Assembled {item_count} evidence item(s) across "
                f"{len(assembled)} claim(s).",
            ),
        ),
    }


def risk_node(state: InvestigationState, context: GraphContext) -> dict[str, Any]:
    """Run Phase 6 risk assessment over everything gathered so far.

    The node sums nothing. It hands the four inputs `RiskService.assess_batch`
    expects and stores the result, which is the only place in the system a
    score, a band and a factor list are produced.

    Args:
        state: State carrying red flags, claims, verification results, evidence.
        context: Services and clock.

    Returns:
        Partial state with `risk_assessment`. An exception here ends the run: a
        reported assessment with no score would misrepresent a failed stage as a
        low-risk result.
    """
    stage = GraphStage.RISK

    try:
        assessment = context.dependencies.risk_service.assess_batch(
            tuple(state.get("claims") or ()),
            tuple(state.get("verification_results") or ()),
            tuple(state.get("evidence") or ()),
            tuple(state.get("red_flags") or ()),
        )
    except Exception as exc:
        logger.exception("Risk service raised during assessment")
        return {
            "current_stage": stage.value,
            "errors": (
                _error(stage, "RISK_ASSESSMENT_FAILED", type(exc).__name__),
            ),
            "timeline": (
                _event(
                    context,
                    stage,
                    TimelineStatus.FAILED,
                    "Risk assessment did not complete.",
                ),
            ),
        }

    return {
        "current_stage": stage.value,
        "risk_assessment": assessment,
        "timeline": (
            _event(
                context,
                stage,
                TimelineStatus.COMPLETED,
                f"Assessed {len(assessment.factors)} contributing risk factor(s).",
            ),
        ),
    }