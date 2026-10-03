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
from app.schemas.ocr import content_digest
from app.schemas.pdf import content_digest as pdf_content_digest
from app.schemas.verification import VerificationResponse, VerificationStatus
from app.services.ocr_guards import SYNTAX_MESSAGES
from app.services.ocr_service import (
    ENGINE_MESSAGES,
    OCRError,
    build_image_analysis_text,
)
from app.services.pdf_guards import SYNTAX_MESSAGES as PDF_GUARD_MESSAGES
from app.services.pdf_service import (
    ENGINE_MESSAGES as PDF_ENGINE_MESSAGES,
    PDFError,
    build_pdf_analysis_text,
)
from app.services.url_fetch import FETCH_MESSAGES, URL_MESSAGES, UrlFetchError
from app.services.website_extractor import build_analysis_text

logger = get_logger(__name__)

__all__ = [
    "ERROR_CODES",
    "URL_CLIENT_FAULT_CODES",
    "URL_CAPABILITY_UNAVAILABLE_CODES",
    "URL_SITE_FAULT_CODES",
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
    "EXTRACTION_FALLBACK",
    "EXTRACTION_PARTIAL",
    "NO_CLAIMS_EXTRACTED",
    "NO_RED_FLAGS_DETECTED",
    "SEARCH_UNAVAILABLE",
    "PARTIAL_VERIFICATION",
    "EVIDENCE_UNAVAILABLE",
    "SEARCH_RESULTS_NOT_RECORDED",
    "URL_CONTENT_TRUNCATED",
    "PAGE_TEXT_NOT_RETRIEVED",
    "OCR_UNAVAILABLE",
    "OCR_TEXT_NOT_RETRIEVED",
    "OCR_CONTENT_TRUNCATED",
    "OCR_FAILED",
    "PDF_UNAVAILABLE",
    "PDF_TEXT_NOT_RETRIEVED",
    "PDF_CONTENT_TRUNCATED",
    "PDF_PAGE_LIMIT_REACHED",
    "PDF_EXTRACTION_FAILED",
)

#: Stable failure codes. Any of these ends the run.
ERROR_CODES: tuple[str, ...] = (
    "INPUT_EMPTY",
    "INPUT_TYPE_NOT_SUPPORTED",
    "EXTRACTION_FAILED",
    "RED_FLAG_DETECTION_FAILED",
    "VERIFICATION_FAILED",
    "RISK_ASSESSMENT_FAILED",
    "URL_FETCH_UNAVAILABLE",
    "IMAGE_INPUT_UNAVAILABLE",
    "PDF_INPUT_UNAVAILABLE",
    *URL_MESSAGES,
    *SYNTAX_MESSAGES,
    *PDF_GUARD_MESSAGES,
)

#: URL failures the **caller** must fix. The submission itself is the problem: a
#: malformed address, a scheme this version will not fetch, or a destination the
#: SSRF policy refuses. Retrying the same URL unchanged fails identically, so the
#: API answers `422`.
#:
#: Split from `ERROR_CODES` by fault rather than by stage, exactly as Phase 8
#: splits `INPUT_EMPTY` from the contract violations recorded at the same stage.
URL_CLIENT_FAULT_CODES: frozenset[str] = frozenset(
    {
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
    }
)

#: URL failures caused by the **submitted site's** behaviour or by our own
#: inability to reach it once we tried: a DNS failure, a timeout, an error
#: status, a redirect loop, an oversized or non-HTML response. None of these is
#: fixed by the caller changing the request, so the API answers `502 Bad Gateway`
#: — the request was well-formed and the upstream we were asked to read could not
#: be read.
#:
#: `URL_FETCH_DISABLED` is deliberately excluded: nothing was attempted, so
#: there is no upstream to have failed. It belongs to
#: `URL_CAPABILITY_UNAVAILABLE_CODES`.
URL_SITE_FAULT_CODES: frozenset[str] = frozenset(FETCH_MESSAGES) - {
    "URL_FETCH_DISABLED"
}

#: URL analysis is not offered by this deployment, so nothing was attempted.
#:
#: Two causes, distinguished because they are configured differently: the
#: feature is switched off in settings, or the graph was built without URL
#: services at all. Neither is the caller's fault and neither is a defect, so
#: neither is a `422` or a `500` — the API answers `503 Service Unavailable`,
#: which is the status that says "this deployment does not do this right now".
URL_CAPABILITY_UNAVAILABLE_CODES: frozenset[str] = frozenset(
    {"URL_FETCH_DISABLED", "URL_FETCH_UNAVAILABLE"}
)

#: Image refusals the **caller** must fix. The submission itself is
#: the problem: no image was sent, an image larger than the upload
#: limit, a file that is not an image, or an image this version does
#: not read. Retrying the same file unchanged fails identically, so
#: the API answers `422`.
#:
#: These are the image modality's `SYNTAX_MESSAGES`, worded where
#: the refusal is decided and imported here so the graph and the API
#: classify them identically.
IMAGE_CLIENT_FAULT_CODES: frozenset[str] = frozenset(SYNTAX_MESSAGES)

#: Image analysis is not offered by this deployment, so nothing was
#: attempted. The graph was built without an OCR service — a
#: configuration, not a defect and not the caller's fault — so the
#: API answers `503 Service Unavailable`, the status that says "this
#: deployment does not do this right now".
IMAGE_CAPABILITY_UNAVAILABLE_CODES: frozenset[str] = frozenset(
    {"IMAGE_INPUT_UNAVAILABLE"}
)

#: PDF refusals the **caller** must fix. The submission itself is
#: the problem: no PDF was sent, a file larger than the upload
#: limit, a file that is not a PDF, or a PDF this version does
#: not read. Retrying the same file unchanged fails identically,
#: so the API answers `422`.
#:
#: These are the PDF modality's `SYNTAX_MESSAGES`, worded where
#: the refusal is decided and imported here so the graph and the
#: API classify them identically.
PDF_CLIENT_FAULT_CODES: frozenset[str] = frozenset(PDF_GUARD_MESSAGES)

#: PDF analysis is not offered by this deployment, so nothing was
#: attempted. The graph was built without a PDF service — a
#: configuration, not a defect and not the caller's fault — so
#: the API answers `503 Service Unavailable`, the status that says
#: "this deployment does not do this right now".
PDF_CAPABILITY_UNAVAILABLE_CODES: frozenset[str] = frozenset(
    {"PDF_INPUT_UNAVAILABLE"}
)

#: Fixed wording per limitation. Each says what did not happen, never what it
#: means about the content or anyone named in it.
WARNING_MESSAGES: dict[str, str] = {
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
    "URL_CONTENT_TRUNCATED": (
        "The submitted page was larger than this version reads, so only its "
        "earlier content was analysed. Content later in the page was not."
    ),
    "PAGE_TEXT_NOT_RETRIEVED": (
        "The submitted page was retrieved but no readable text was found in it. "
        "A page that builds its content with JavaScript often appears this way, "
        "so the analysis below covers the submitted URL and domain only and not "
        "the page's visible content."
    ),
    "OCR_UNAVAILABLE": (
        "Screenshot text recognition is not available in this deployment, so "
        "the submitted image's text was not read. The image itself was "
        "received, and the analysis below covers the image's own facts only "
        "and not its text content."
    ),
    "OCR_TEXT_NOT_RETRIEVED": (
        "The submitted image was read but no text was found in it. A "
        "photograph or a scan of a handwritten note often appears this way, "
        "so the analysis below covers the image's own facts only and not "
        "any text it may contain."
    ),
    "OCR_CONTENT_TRUNCATED": (
        "The text recovered from the submitted image is larger than this "
        "version reads, so only its earlier content was analysed. Text later "
        "in the image was not."
    ),
    "PDF_UNAVAILABLE": (
        "PDF text extraction is not available in this deployment, so "
        "the submitted PDF's text was not read. The PDF itself was "
        "received, and the analysis below covers the PDF's own facts "
        "only and not its text content."
    ),
    "PDF_TEXT_NOT_RETRIEVED": (
        "The submitted PDF was read but no text was found in it. A "
        "scan of a printed page often appears this way, so the "
        "analysis below covers the PDF's own facts only and not any "
        "text it may contain."
    ),
    "PDF_CONTENT_TRUNCATED": (
        "The text recovered from the submitted PDF is larger than this "
        "version reads, so only its earlier content was analysed. Text "
        "later in the PDF was not."
    ),
    "PDF_PAGE_LIMIT_REACHED": (
        "The submitted PDF has more pages than this version reads, so "
        "only its earlier pages were analysed. Pages later in the PDF "
        "were not."
    ),
}

#: Fixed wording per failure. Safe to surface: it describes the pipeline, never
#: the content, and carries no provider detail.
ERROR_MESSAGES: dict[str, str] = {
    "INPUT_EMPTY": (
        "No content was submitted, so there was nothing to investigate."
    ),
    "INPUT_TYPE_NOT_SUPPORTED": (
        "This version analyses text, URL, image and PDF input. The "
        "submitted input type was recognised but is not analysed, so no "
        "investigation was performed."
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
    "URL_FETCH_UNAVAILABLE": (
        "URL retrieval is not available in this deployment, so the "
        "submitted URL was not retrieved and no investigation was "
        "performed."
    ),
    "IMAGE_INPUT_UNAVAILABLE": (
        "Screenshot investigation is not available in this deployment, "
        "so the submitted image was not recognised and no "
        "investigation was performed."
    ),
    "PDF_INPUT_UNAVAILABLE": (
        "PDF investigation is not available in this deployment, "
        "so the submitted PDF was not read and no "
        "investigation was performed."
    ),
}

# Phase 12's refusals are worded where the decision is made — the address policy
# and the fetch service — and merged in here rather than retyped. That keeps the
# graph's promise true for every code it can record ("every failure code has fixed
# wording") without a second copy of these sentences that could drift from the
# code it describes.
#
# Two of these carry a `{placeholder}` for a fact the caller supplied. The
# placeholder is only ever formatted by the exception that raised it, and `_url_input`
# surfaces that formatted `message` — never a raw template — so the wording that
# reaches a caller is always complete. `_error`, which reads this table directly,
# is only ever called with the graph's own codes.
ERROR_MESSAGES.update(URL_MESSAGES)

# Phase 13's image refusals are worded where the decision is
# made — the OCR policy and the recognition service — and merged
# in for the same reason: the graph's promise holds for the
# image modality too, with no second copy of these sentences.
#
# The caller-fault refusals (`SYNTAX_MESSAGES`) end the run, so
# they belong in `ERROR_MESSAGES`. The engine's own failure
# (`ENGINE_MESSAGES`, `OCR_FAILED`) is a limitation the run
# continues past, so it belongs in `WARNING_MESSAGES` instead —
# the two tables stay disjoint, which a test relies on.
ERROR_MESSAGES.update(SYNTAX_MESSAGES)
WARNING_MESSAGES.update(ENGINE_MESSAGES)

# Phase 14's PDF refusals are worded where the decision is
# made — the PDF policy and the extraction service — and merged
# in for the same reason: the graph's promise holds for the
# PDF modality too, with no second copy of these sentences.
#
# As with the image modality, the caller-fault refusals
# (`PDF_GUARD_MESSAGES`) end the run and belong in
# `ERROR_MESSAGES`, while the engine's own failure
# (`PDF_ENGINE_MESSAGES`, `PDF_EXTRACTION_FAILED`) is a
# limitation the run continues past and belongs in
# `WARNING_MESSAGES`. The two tables stay disjoint.
ERROR_MESSAGES.update(PDF_GUARD_MESSAGES)
WARNING_MESSAGES.update(PDF_ENGINE_MESSAGES)


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


def _url_input(
    raw_input: str,
    context: GraphContext,
) -> dict[str, Any]:
    """Fetch a submitted URL and turn it into the text the pipeline analyses.

    This is the whole of Phase 12's contribution to the graph. It runs inside
    `input_node` rather than as a node of its own so that everything downstream
    — extraction, pattern detection, verification, evidence, risk — is the
    *existing* machinery operating on the resulting text. A URL submission
    therefore produces an investigation of exactly the same shape as a text one,
    and no downstream stage needs to know where the text came from.

    The node stays free of business logic by delegating every decision: the fetch
    service owns the network and the SSRF policy, the extractor owns HTML parsing,
    and this function only maps their typed outcomes onto graph vocabulary.

    Args:
        raw_input: The submitted URL.
        context: Services and clock.

    Returns:
        Partial state. On success it carries the composed analysis text as
        `raw_input`, the `UrlSource` provenance, and any limitation. On refusal
        it carries an `errors` entry and no analysis text.
    """
    stage = GraphStage.INPUT
    dependencies = context.dependencies

    # One capability, checked once. Fetching without extracting, or extracting
    # without fetching, is not a configuration this product ships.
    if not dependencies.supports_url:
        logger.info("URL submission received by a graph without URL support")
        return {
            "input_type": InvestigationInputType.URL.value,
            "investigation_id": investigation_id_for(
                InvestigationInputType.URL, raw_input
            ),
            "current_stage": stage.value,
            "started_at": context.clock(),
            "errors": (_error(stage, "URL_FETCH_UNAVAILABLE", None),),
            "timeline": (
                _event(
                    context,
                    stage,
                    TimelineStatus.FAILED,
                    "Website investigation is not available in this deployment.",
                ),
            ),
        }

    fetch_service = dependencies.url_fetch_service
    extractor = dependencies.website_extractor
    assert fetch_service is not None and extractor is not None  # supports_url

    try:
        page = fetch_service.fetch(raw_input)
    except UrlFetchError as exc:
        # A typed refusal is a normal outcome, not a defect: the URL was
        # understood and the answer is "no". Its message is fixed per code by
        # the layer that raised it and carries no socket, TLS or HTTP detail, so
        # it is safe to surface here rather than restating it in this module.
        logger.info(
            "Website retrieval refused",
            extra={"reason": exc.code},
        )
        return {
            "input_type": InvestigationInputType.URL.value,
            "investigation_id": investigation_id_for(
                InvestigationInputType.URL, raw_input
            ),
            "current_stage": stage.value,
            "started_at": context.clock(),
            "errors": (
                GraphError(
                    code=exc.code,
                    stage=stage,
                    message=exc.message,
                    error_type=None,
                ),
            ),
            "timeline": (
                _event(
                    context,
                    stage,
                    TimelineStatus.FAILED,
                    "The submitted website could not be retrieved.",
                ),
            ),
        }
    except Exception as exc:
        # Phase 12's fetch service documents that it raises only `UrlFetchError`
        # for every condition it can anticipate. Anything else escaping is a
        # contract violation below this layer, so it ends the run rather than
        # being reported as though the site were merely unavailable.
        logger.exception("URL fetch service violated its typed-failure contract")
        return {
            "input_type": InvestigationInputType.URL.value,
            "investigation_id": investigation_id_for(
                InvestigationInputType.URL, raw_input
            ),
            "current_stage": stage.value,
            "started_at": context.clock(),
            "errors": (
                _error(stage, "URL_FETCH_UNAVAILABLE", type(exc).__name__),
            ),
            "timeline": (
                _event(
                    context,
                    stage,
                    TimelineStatus.FAILED,
                    "Website retrieval did not complete as expected.",
                ),
            ),
        }

    document = extractor.extract(page)
    analysis_text = build_analysis_text(document)

    warnings: list[GraphWarning] = []
    if page.truncated or document.truncated:
        warnings.append(_warning(stage, "URL_CONTENT_TRUNCATED"))

    # A page with no readable text is still analysed — the URL and domain are
    # real evidence and the pattern rules still run over them — but the report
    # must say that the page's visible content was not covered. This is the
    # characteristic outcome for a JavaScript-rendered site, and reporting it as
    # a complete analysis of the page would overstate what was read.
    if not document.text.strip():
        warnings.append(_warning(stage, "PAGE_TEXT_NOT_RETRIEVED"))

    return {
        "input_type": InvestigationInputType.URL.value,
        "raw_input": analysis_text,
        "extracted_text": analysis_text,
        "url_source": document.source,
        "investigation_id": investigation_id_for(
            InvestigationInputType.URL, raw_input
        ),
        "started_at": context.clock(),
        "current_stage": stage.value,
        "warnings": tuple(warnings),
        "timeline": (
            _event(
                context,
                stage,
                TimelineStatus.PARTIAL if warnings else TimelineStatus.STARTED,
                "Website retrieved."
                if not warnings
                else "Website retrieved, with limitations.",
            ),
        ),
    }


def _image_input(
    state: InvestigationState,
    context: GraphContext,
) -> dict[str, Any]:
    """Recognise a submitted screenshot and turn it into analysis text.

    This is Phase 13's contribution to the graph, and it mirrors
    `_url_input`: it runs inside `input_node` so everything
    downstream — extraction, pattern detection, verification,
    evidence, risk — is the *existing* machinery operating on the
    resulting text. An image submission therefore produces an
    investigation of exactly the same shape as a text one, and no
    downstream stage needs to know where the text came from.

    The node stays free of business logic by delegating every
    decision: the recognition service owns decoding the image and
    driving the engine, and this function only maps its typed
    outcomes onto graph vocabulary.

    Args:
        state: The incoming state. Requires `input_type`, and
            carries the submitted image in `upload`.
        context: Services and clock.

    Returns:
        Partial state. On success it carries the composed analysis
        text as `raw_input`, the `ImageSource` provenance, and any
        limitation. On refusal it carries an `errors` entry and no
        analysis text.
    """
    stage = GraphStage.INPUT
    dependencies = context.dependencies
    raw_input = state.get("raw_input") or ""
    upload = state.get("upload")

    # An image submission is identified by its content, so a re-run
    # of the same screenshot shares an id no matter what the caller
    # named the file. When no image was submitted there is no
    # content to digest, and the seed text stands in.
    identity = content_digest(upload) if upload is not None else raw_input

    # One capability, checked once. A graph built without an OCR
    # service cannot recognise a screenshot at all, which is a
    # deployment configuration rather than a defect.
    if not dependencies.supports_image:
        logger.info("Image submission received by a graph without image support")
        return {
            "input_type": InvestigationInputType.IMAGE.value,
            "investigation_id": investigation_id_for(
                InvestigationInputType.IMAGE, identity
            ),
            "current_stage": stage.value,
            "started_at": context.clock(),
            "errors": (_error(stage, "IMAGE_INPUT_UNAVAILABLE", None),),
            "timeline": (
                _event(
                    context,
                    stage,
                    TimelineStatus.FAILED,
                    "Screenshot investigation is not available in this "
                    "deployment.",
                ),
            ),
        }

    # The typed JSON endpoint declares IMAGE but carries no image
    # bytes: there is nothing to recognise, and the caller is told
    # so rather than the run pretending an empty text was a
    # screenshot.
    if upload is None:
        logger.info("Image input type submitted without an image")
        return {
            "input_type": InvestigationInputType.IMAGE.value,
            "investigation_id": investigation_id_for(
                InvestigationInputType.IMAGE, identity
            ),
            "current_stage": stage.value,
            "started_at": context.clock(),
            "errors": (_error(stage, "OCR_IMAGE_EMPTY", None),),
            "timeline": (
                _event(
                    context,
                    stage,
                    TimelineStatus.FAILED,
                    "No image was submitted.",
                ),
            ),
        }

    ocr_service = dependencies.ocr_service
    assert ocr_service is not None  # supports_image

    try:
        document = ocr_service.extract(upload)
    except OCRError as exc:
        # A typed refusal is a normal outcome, not a defect: the
        # submission was understood and the answer is "no". Its
        # message is fixed per code by the layer that raised it and
        # carries no engine detail, so it is safe to surface here
        # rather than restating it in this module.
        logger.info("Image submission refused", extra={"reason": exc.code})
        return {
            "input_type": InvestigationInputType.IMAGE.value,
            "investigation_id": investigation_id_for(
                InvestigationInputType.IMAGE, identity
            ),
            "current_stage": stage.value,
            "started_at": context.clock(),
            "errors": (
                GraphError(
                    code=exc.code,
                    stage=stage,
                    message=exc.message,
                    error_type=None,
                ),
            ),
            "timeline": (
                _event(
                    context,
                    stage,
                    TimelineStatus.FAILED,
                    "The submitted image could not be accepted.",
                ),
            ),
        }
    except Exception as exc:
        # The OCR service documents that it raises only `OCRError`
        # for every condition it can anticipate. Anything else
        # escaping is a contract violation below this layer, so it
        # ends the run rather than being reported as though the
        # image were merely unreadable.
        logger.exception("OCR service violated its typed-failure contract")
        return {
            "input_type": InvestigationInputType.IMAGE.value,
            "investigation_id": investigation_id_for(
                InvestigationInputType.IMAGE, identity
            ),
            "current_stage": stage.value,
            "started_at": context.clock(),
            "errors": (
                _error(stage, "IMAGE_INPUT_UNAVAILABLE", type(exc).__name__),
            ),
            "timeline": (
                _event(
                    context,
                    stage,
                    TimelineStatus.FAILED,
                    "Screenshot recognition did not complete as expected.",
                ),
            ),
        }

    # The recognition outcome is mapped onto graph vocabulary here,
    # the same place a fetch outcome is mapped for a URL, so no
    # downstream stage learns where the text came from. Every
    # outcome below keeps the run going: an image that cannot be
    # read is a limitation to record, not a reason to stop, because
    # the image's own facts are still evidence.
    warnings: list[GraphWarning] = []

    if document.limitation == "OCR_UNAVAILABLE":
        warnings.append(_warning(stage, "OCR_UNAVAILABLE"))
    elif document.limitation == "OCR_FAILED":
        warnings.append(
            _warning(stage, "OCR_FAILED", error_type=document.error_type)
        )
    elif not document.text.strip():
        # Recognition ran and produced nothing. This is the
        # characteristic outcome for a photograph, and reporting it
        # as a complete reading of the image would overstate what
        # was read.
        warnings.append(_warning(stage, "OCR_TEXT_NOT_RETRIEVED"))

    if document.truncated:
        warnings.append(_warning(stage, "OCR_CONTENT_TRUNCATED"))

    analysis_text = build_image_analysis_text(document)

    return {
        "input_type": InvestigationInputType.IMAGE.value,
        "raw_input": analysis_text,
        "extracted_text": analysis_text,
        "image_source": document.source,
        "investigation_id": investigation_id_for(
            InvestigationInputType.IMAGE, identity
        ),
        "started_at": context.clock(),
        "current_stage": stage.value,
        "warnings": tuple(warnings),
        "timeline": (
            _event(
                context,
                stage,
                TimelineStatus.PARTIAL if warnings else TimelineStatus.STARTED,
                "Screenshot recognised."
                if not warnings
                else "Screenshot recognised, with limitations.",
            ),
        ),
    }


def _pdf_input(
    state: InvestigationState,
    context: GraphContext,
) -> dict[str, Any]:
    """Extract a submitted PDF's text and turn it into analysis text.

    This is Phase 14's contribution to the graph, and it mirrors
    `_image_input` and `_url_input`: it runs inside `input_node` so
    everything downstream — extraction, pattern detection,
    verification, evidence, risk — is the *existing* machinery
    operating on the resulting text. A PDF submission therefore
    produces an investigation of exactly the same shape as a text
    one, and no downstream stage needs to know where the text came
    from.

    The node stays free of business logic by delegating every
    decision: the extraction service owns parsing the PDF and
    reading its text, and this function only maps its typed
    outcomes onto graph vocabulary.

    Args:
        state: The incoming state. Requires `input_type`, and
            carries the submitted PDF in `pdf_upload`.
        context: Services and clock.

    Returns:
        Partial state. On success it carries the composed analysis
        text as `raw_input`, the `PdfSource` provenance, and any
        limitation. On refusal it carries an `errors` entry and no
        analysis text.
    """
    stage = GraphStage.INPUT
    dependencies = context.dependencies
    raw_input = state.get("raw_input") or ""
    pdf_upload = state.get("pdf_upload")

    # A PDF submission is identified by its content, so a re-run
    # of the same document shares an id no matter what the caller
    # named the file. When no PDF was submitted there is no
    # content to digest, and the seed text stands in.
    identity = (
        pdf_content_digest(pdf_upload) if pdf_upload is not None else raw_input
    )

    # One capability, checked once. A graph built without a PDF
    # service cannot read a PDF at all, which is a deployment
    # configuration rather than a defect.
    if not dependencies.supports_pdf:
        logger.info("PDF submission received by a graph without PDF support")
        return {
            "input_type": InvestigationInputType.PDF.value,
            "investigation_id": investigation_id_for(
                InvestigationInputType.PDF, identity
            ),
            "current_stage": stage.value,
            "started_at": context.clock(),
            "errors": (_error(stage, "PDF_INPUT_UNAVAILABLE", None),),
            "timeline": (
                _event(
                    context,
                    stage,
                    TimelineStatus.FAILED,
                    "PDF investigation is not available in this "
                    "deployment.",
                ),
            ),
        }

    # The typed endpoint declares PDF but carries no PDF bytes:
    # there is nothing to read, and the caller is told so rather
    # than the run pretending an empty submission was a document.
    if pdf_upload is None:
        logger.info("PDF input type submitted without a PDF")
        return {
            "input_type": InvestigationInputType.PDF.value,
            "investigation_id": investigation_id_for(
                InvestigationInputType.PDF, identity
            ),
            "current_stage": stage.value,
            "started_at": context.clock(),
            "errors": (_error(stage, "PDF_EMPTY", None),),
            "timeline": (
                _event(
                    context,
                    stage,
                    TimelineStatus.FAILED,
                    "No PDF was submitted.",
                ),
            ),
        }

    pdf_service = dependencies.pdf_service
    assert pdf_service is not None  # supports_pdf

    try:
        document = pdf_service.extract(pdf_upload)
    except PDFError as exc:
        # A typed refusal is a normal outcome, not a defect: the
        # submission was understood and the answer is "no". Its
        # message is fixed per code by the layer that raised it and
        # carries no engine detail, so it is safe to surface here
        # rather than restating it in this module.
        logger.info("PDF submission refused", extra={"reason": exc.code})
        return {
            "input_type": InvestigationInputType.PDF.value,
            "investigation_id": investigation_id_for(
                InvestigationInputType.PDF, identity
            ),
            "current_stage": stage.value,
            "started_at": context.clock(),
            "errors": (
                GraphError(
                    code=exc.code,
                    stage=stage,
                    message=exc.message,
                    error_type=None,
                ),
            ),
            "timeline": (
                _event(
                    context,
                    stage,
                    TimelineStatus.FAILED,
                    "The submitted PDF could not be accepted.",
                ),
            ),
        }
    except Exception as exc:
        # The PDF service documents that it raises only `PDFError`
        # for every condition it can anticipate. Anything else
        # escaping is a contract violation below this layer, so it
        # ends the run rather than being reported as though the
        # PDF were merely unreadable.
        logger.exception("PDF service violated its typed-failure contract")
        return {
            "input_type": InvestigationInputType.PDF.value,
            "investigation_id": investigation_id_for(
                InvestigationInputType.PDF, identity
            ),
            "current_stage": stage.value,
            "started_at": context.clock(),
            "errors": (
                _error(stage, "PDF_INPUT_UNAVAILABLE", type(exc).__name__),
            ),
            "timeline": (
                _event(
                    context,
                    stage,
                    TimelineStatus.FAILED,
                    "PDF text extraction did not complete as expected.",
                ),
            ),
        }

    # The extraction outcome is mapped onto graph vocabulary here,
    # the same place a fetch outcome is mapped for a URL, so no
    # downstream stage learns where the text came from. Every
    # outcome below keeps the run going: a PDF that cannot be
    # read is a limitation to record, not a reason to stop, because
    # the PDF's own facts are still evidence.
    warnings: list[GraphWarning] = []

    if document.limitation == "PDF_UNAVAILABLE":
        warnings.append(_warning(stage, "PDF_UNAVAILABLE"))
    elif document.limitation == "PDF_EXTRACTION_FAILED":
        warnings.append(
            _warning(stage, "PDF_EXTRACTION_FAILED", error_type=document.error_type)
        )
    elif not document.text.strip():
        # Extraction ran and produced nothing. This is the
        # characteristic outcome for a scan of a printed page, and
        # reporting it as a complete reading of the PDF would
        # overstate what was read.
        warnings.append(_warning(stage, "PDF_TEXT_NOT_RETRIEVED"))

    if document.truncated:
        warnings.append(_warning(stage, "PDF_CONTENT_TRUNCATED"))

    source = document.source
    if (
        source.page_count is not None
        and source.pages_processed is not None
        and source.pages_processed < source.page_count
    ):
        # Only the first `pdf_max_pages` pages were read; the rest
        # were skipped by the page limit. Recorded separately from
        # `PDF_CONTENT_TRUNCATED`, which is about the character
        # budget on the recovered text, not the page budget on the
        # document.
        warnings.append(_warning(stage, "PDF_PAGE_LIMIT_REACHED"))

    analysis_text = build_pdf_analysis_text(document)

    return {
        "input_type": InvestigationInputType.PDF.value,
        "raw_input": analysis_text,
        "extracted_text": analysis_text,
        "pdf_source": document.source,
        "investigation_id": investigation_id_for(
            InvestigationInputType.PDF, identity
        ),
        "started_at": context.clock(),
        "current_stage": stage.value,
        "warnings": tuple(warnings),
        "timeline": (
            _event(
                context,
                stage,
                TimelineStatus.PARTIAL if warnings else TimelineStatus.STARTED,
                "PDF text extracted."
                if not warnings
                else "PDF text extracted, with limitations.",
            ),
        ),
    }


def input_node(state: InvestigationState, context: GraphContext) -> dict[str, Any]:
    """Validate the submission and open the investigation record.

    The only job here is to establish that there is something to investigate.
    Normalization belongs to Phase 2, which already does it, so nothing is
    rewritten or trimmed here — an empty or unusable submission must be visible
    as submitted rather than quietly repaired.

    A `URL` submission is the one exception to "nothing is rewritten": the
    submitted string is a reference, not content, so `_url_input` retrieves it
    and the pipeline analyses the retrieved page. The submitted URL itself is
    preserved in `url_source`, and the investigation id is derived from the
    submitted URL rather than from the retrieved text, so two submissions of the
    same address share an id no matter what the site returned that day.

    An `IMAGE` submission is the same kind of exception: the submitted
    bytes are not text, so `_image_input` recognises them and the pipeline
    analyses the recovered text. The image's own facts are preserved in
    `image_source`, and the investigation id is derived from the image
    content rather than from the recovered text, so two submissions of the
    same screenshot share an id no matter what the engine returned that day.

    A `PDF` submission is the same kind of exception again: the submitted
    bytes are not text, so `_pdf_input` extracts the document's text and
    the pipeline analyses the recovered text. The PDF's own facts are
    preserved in `pdf_source`, and the investigation id is derived from the
    PDF content rather than from the recovered text, so two submissions of
    the same document share an id no matter what the engine returned that
    day.

    Args:
        state: The incoming state. Requires `raw_input` and `input_type`,
            and carries a submitted image in `upload` for an `IMAGE` input
            and a submitted PDF in `pdf_upload` for a `PDF` input.
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

    if input_type is InvestigationInputType.URL:
        return _url_input(raw_input, context)

    if input_type is InvestigationInputType.IMAGE:
        return _image_input(state, context)

    if input_type is InvestigationInputType.PDF:
        return _pdf_input(state, context)

    # Guard for a kind the vocabulary declares but this version has
    # not wired up yet. Every kind it carries is analysed today, so
    # this is unreachable now; it exists so that adding a kind to the
    # enum without the ingestion work is refused here — ending the run
    # — rather than quietly read as text.
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