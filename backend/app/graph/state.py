"""Investigation state, stage vocabulary and timeline metadata (Phase 7).

This module defines **what the investigation knows**, not how it is computed. The
LangGraph state is a transport between services that already exist: every field
carries an object built by Phases 1-6, so no stage re-derives another's work and
no stage invents a second shape for the same fact.

Three rules shape the design:

- **Real objects, not dictionaries.** `claims` holds `Claim` instances, not
  `{...}` blobs, because the services downstream are typed against those models.
  Rehydrating a plain dict would mean re-validating the same data twice and
  losing every invariant the schemas enforce.
- **Accumulation is structural, not conventional.** `warnings`, `errors` and
  `timeline` are declared with `operator.add` reducers, so a node that appends
  one warning cannot silently overwrite the three already recorded. Appending is
  a property of the schema rather than a rule every future node must remember.
- **Nothing in the state interprets the investigation.** The state carries
  `risk_assessment`, but no field restates it, no field ranks stages, and no
  field decides whether the investigation "passed". Interpretation is Phase 10's
  report, and the risk score is a Phase 6 heuristic that is not a probability
  (D-025).

The state is a `TypedDict`, because LangGraph reads the annotations to build its
channel map and needs `Annotated` reducers, which a Pydantic model expresses
poorly. Validation of *state shape* is therefore the graph builder's job, and
validation of *content* remains the job of the Phase 1-6 schemas themselves.
"""

from __future__ import annotations

import hashlib
import operator
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.claims import Claim
from app.schemas.entities import Entity
from app.schemas.evidence import EvidenceResponse
from app.schemas.extraction import ClaimEntityLink, ExtractionMode, ExtractionResult
from app.schemas.red_flags import RedFlag
from app.schemas.risk import RiskAssessment
from app.schemas.url import UrlSource
from app.schemas.verification import VerificationResponse, VerificationResult

__all__ = [
    "GraphStage",
    "GraphWarning",
    "GraphError",
    "InvestigationInputType",
    "InvestigationState",
    "TIMESTAMP_FIELDS",
    "TimelineEvent",
    "TimelineStatus",
    "investigation_id_for",
]


class InvestigationInputType(str, Enum):
    """The input kinds the product accepts.

    Declared in full because `PROJECT_CONTEXT.md` §9 commits the product to all
    four, and an enum that listed only the one implemented today would quietly
    redefine the product.

    **Phase 7 analyses `TEXT` only.** OCR, PDF and image ingestion are deferred,
    so the other three are recognised and refused with a typed reason rather than
    silently analysed as if they were text. See `INPUT_NODE` in `nodes.py`.
    """

    TEXT = "TEXT"
    URL = "URL"
    IMAGE = "IMAGE"
    PDF = "PDF"


class GraphStage(str, Enum):
    """Stages of the investigation, in the order the graph runs them.

    The values double as LangGraph node names, timeline stage labels and
    `current_stage` values, so a future UI can render one vocabulary rather than
    translating between three.
    """

    INPUT = "input"
    EXTRACTION = "extraction"
    RED_FLAGS = "red_flags"
    VERIFICATION = "verification"
    EVIDENCE = "evidence"
    RISK = "risk"
    COMPLETED = "completed"


class TimelineStatus(str, Enum):
    """How a stage finished.

    `PARTIAL` and `SKIPPED` exist so that a degraded run is distinguishable from
    a clean one. Collapsing them into `COMPLETED` would make "we could not check
    this" indistinguishable from "we checked and there was nothing to find",
    which is the exact confusion D-006 exists to prevent.
    """

    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class TimelineEvent(BaseModel):
    """One entry in the investigation timeline.

    Metadata for a future Phase 11/16 UI. It records **what the pipeline did**,
    never what it concluded: there is no field for an explanation, a confidence,
    or a judgement, so no natural-language stage narrative can leak in here.

    Attributes:
        stage: Which stage this entry describes.
        status: How that stage finished.
        message: Fixed wording describing the stage's mechanical outcome.
        at: When the stage finished. Metadata only, isolated from the semantic
            output by `semantic_view` and injectable through `GraphContext.clock`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: GraphStage
    status: TimelineStatus
    message: str
    at: datetime = Field(description="Metadata only; not part of the semantic output.")


class GraphWarning(BaseModel):
    """A structured, non-fatal limitation recorded during a run.

    Warnings never stop the investigation. They are the mechanism by which a
    degraded run says so: search unavailable, extraction fell back, evidence could
    not be assembled. Each carries a stable `code` so a caller can branch on the
    limitation without parsing English (D-009).

    Attributes:
        code: Stable machine-readable limitation code.
        stage: The stage that recorded it.
        message: Fixed, factual wording. It states what did not happen and never
            what it implies about the claim or the content.
        error_type: The exception class name when the limitation came from a
            service fault rather than an external condition. A diagnostic field,
            deliberately kept out of `message` so technical detail cannot leak
            into wording a user may read.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    stage: GraphStage
    message: str
    error_type: str | None = Field(
        default=None,
        description="Exception class name when the limitation is a service fault.",
    )


class GraphError(BaseModel):
    """A structured, fatal failure that ended the run early.

    An error is recorded only when continuing would misrepresent the result. The
    technical cause is logged, and only the exception's *type* is kept here:
    a stack trace or a provider's raw message must never reach a result object
    that a future API layer may return to a user.

    Attributes:
        code: Stable machine-readable failure code.
        stage: The stage that failed.
        message: Fixed, factual wording safe to surface.
        error_type: The exception class name, for diagnosis. Never a message.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    stage: GraphStage
    message: str
    error_type: str | None = Field(
        default=None,
        description="Exception class name only. Messages and stack traces stay in the logs.",
    )


class InvestigationState(TypedDict, total=False):
    """The state one investigation carries between graph stages.

    Every key is optional because the graph fills it in as stages run, and a run
    that stops early legitimately has no `risk_assessment`. Reading a missing key
    is a stage-ordering bug, which is why `current_stage` is written before the
    stage's own outputs and the nodes treat absent keys as empty rather than
    reaching for defaults that would hide the absence.

    The three ``Annotated`` fields accumulate via ``operator.add``: a node returns
    only its own new entries and the channel concatenates them.

    Attributes:
        input_type: Declared input kind, one of `InvestigationInputType`.
        raw_input: Exactly what the caller submitted, or — for a URL submission —
            the analysis text composed from the retrieved page. Red-flag spans are
            anchored to this string, so it is never replaced by a normalized
            variant. For `URL` it is the *derived* text the pipeline analysed, not
            the submitted URL; `url_source` holds the submitted URL itself.
        url_source: Phase 12 provenance for a URL submission — the submitted URL,
            the host contacted, the addresses it resolved to, and whether the
            response was truncated. Absent for every other input kind.
        extracted_text: The text Phase 2 worked from, which may be a normalized
            form of `raw_input`. Kept for traceability of Phase 2's own work.
        investigation_id: Deterministic digest of the submitted input. Derived,
            not sequential, so the same input yields the same id on every run.
        started_at: When the run began. Metadata only.
        completed_at: When the run finished, or ``None`` if it did not. Metadata
            only.
        current_stage: The stage that ran most recently.
        extraction: Phase 2's `ExtractionResult`, stored whole so Phase 4's
            `verify_extraction` can consume it without reconstruction.
        claims: Phase 2 claims, in source order.
        entities: Phase 2 entities, in source order.
        claim_entity_links: Phase 2 claim-to-entity relationships.
        extraction_mode: How Phase 2 produced its output. `FALLBACK` and `PARTIAL`
            are weaker than `LLM` and the graph records that as a limitation.
        red_flags: Phase 1 `RedFlag` objects, with spans, severity and metadata.
        verification: Phase 4's batch `VerificationResponse`.
        verification_results: The same results as a tuple, for stages that take
            the per-claim sequence `RiskService` expects.
        evidence: Phase 5 `EvidenceResponse` per claim, in claim order.
        risk_assessment: Phase 6's `RiskAssessment`. The only summary judgement
            in the pipeline, and a heuristic indicator count rather than a
            probability (D-025).
        warnings: Accumulated `GraphWarning` values.
        errors: Accumulated `GraphError` values. Any entry here ended the run.
        timeline: Accumulated `TimelineEvent` values, one or two per stage.
    """

    input_type: str
    raw_input: str
    url_source: UrlSource | None
    extracted_text: str
    investigation_id: str
    started_at: datetime
    completed_at: datetime | None
    current_stage: str

    extraction: ExtractionResult | None
    claims: tuple[Claim, ...]
    entities: tuple[Entity, ...]
    claim_entity_links: tuple[ClaimEntityLink, ...]
    extraction_mode: ExtractionMode | None

    red_flags: tuple[RedFlag, ...]

    verification: VerificationResponse | None
    verification_results: tuple[VerificationResult, ...]

    evidence: tuple[EvidenceResponse, ...]

    risk_assessment: RiskAssessment | None

    warnings: Annotated[tuple[GraphWarning, ...], operator.add]
    errors: Annotated[tuple[GraphError, ...], operator.add]
    timeline: Annotated[tuple[TimelineEvent, ...], operator.add]


def investigation_id_for(input_type: str | InvestigationInputType, raw_input: str) -> str:
    """Derive a stable investigation id from the submitted input.

    A digest rather than a counter or a database sequence, because Phase 7 has no
    persistence (that is Phase 9) and because a derived id makes a re-run
    comparable: submitting the same content twice yields the same id, so the two
    runs can be diffed field by field.

    The input type is mixed in so that the same text submitted as `TEXT` and
    `URL` does not collide.

    Args:
        input_type: Declared input kind.
        raw_input: The submitted text.

    Returns:
        A `inv_`-prefixed hex digest, identical for identical input.
    """
    kind = input_type.value if isinstance(input_type, InvestigationInputType) else str(input_type)
    digest = hashlib.sha256(f"{kind}\x00{raw_input}".encode()).hexdigest()
    return f"inv_{digest[:16]}"


#: Field names that record *when* something happened rather than *what* was
#: found. Every one of them is stamped from a real clock by whichever phase owns
#: it, so two runs of the same input legitimately differ in exactly these and
#: nothing else.
#:
#: They are listed rather than detected by type so that a genuine finding which
#: happens to be a datetime — a publication date carried on a claim, say — is not
#: silently discarded along with the metadata.
TIMESTAMP_FIELDS: frozenset[str] = frozenset(
    {
        "assessed_at",
        "built_at",
        "retrieved_at",
        "searched_at",
        "started_at",
        "completed_at",
    }
)


def _strip(value: Any) -> Any:
    """Recursively drop metadata timestamps from a nested structure.

    Pydantic models are dumped first so the recursion can be uniform; anything
    else is walked as a mapping or a sequence.

    Args:
        value: Any state value.

    Returns:
        The value with every `TIMESTAMP_FIELDS` key removed.
    """
    if isinstance(value, BaseModel):
        value = value.model_dump()
    if isinstance(value, dict):
        return {
            key: _strip(item)
            for key, item in value.items()
            if key not in TIMESTAMP_FIELDS
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_strip(item) for item in value)
    return value


def semantic_view(state: dict[str, Any]) -> dict[str, Any]:
    """Reduce a final state to the fields that must be reproducible.

    Wall-clock timestamps are metadata. They legitimately differ between two runs
    of the same input and are excluded here, so a determinism test compares what
    the pipeline *decided* rather than when it happened.

    This covers timestamps nested inside Phase 1-6 objects as well as the graph's
    own: `RiskAssessment.assessed_at`, `EvidenceResponse.built_at` and
    `SearchResult.retrieved_at` are all stamped by their own phases from their own
    clocks, which the graph cannot control. The timeline is reduced to
    stage/status/message triples for the same reason.

    Args:
        state: A final investigation state.

    Returns:
        A copy without any `TIMESTAMP_FIELDS`, and with the timeline reduced to
        its semantic triples.
    """
    reduced = _strip(state)
    timeline = state.get("timeline") or ()
    reduced["timeline"] = tuple(
        (event.stage.value, event.status.value, event.message) for event in timeline
    )
    return reduced