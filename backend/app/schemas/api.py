"""HTTP request and response contracts for the investigation API (Phase 8).

These models are the **only** shapes a client ever sees. They are deliberately
separate from the Phase 1-7 domain schemas for two reasons:

- **Stability.** The domain models carry internal detail that changes when a
  service improves — extraction modes, query lists, search bookkeeping. A client
  should not have to follow those changes, and a Phase 1-7 model leaked into the
  response body would couple the two contracts permanently.
- **Meaning.** `limitations` is the load-bearing example. The graph already
  expresses degradation as `TimelineStatus.PARTIAL` per stage; this layer turns
  that into one top-level status so a client does not have to reconstruct it, and
  turns the accumulated `GraphWarning` values into a deduplicated array of stable
  *codes* so a client branches on identifiers rather than parsing English (D-009).

Two rules constrain everything here:

- **The API layer interprets, it does not analyse.** No threshold, weight, score
  or verdict is computed in this module. Every judgement already exists in a
  Phase 1-7 model and is passed through unchanged — `RiskAssessment` in
  particular is forwarded verbatim, caveats included (D-025).
- **Nothing is summarised away.** A degraded run is a *successful* request that
  reports what it could not do. Silently returning an empty array would make
  "we checked and found nothing" indistinguishable from "we never checked",
  which is the confusion D-006 exists to prevent.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.graph.state import (
    GraphStage,
    InvestigationInputType,
    TimelineStatus,
)
from app.schemas.claims import Claim
from app.schemas.entities import Entity
from app.schemas.evidence import EvidenceResponse
from app.schemas.red_flags import RedFlag
from app.schemas.risk import RiskAssessment
from app.schemas.verification import VerificationResult

__all__ = [
    "MAX_TEXT_LENGTH",
    "ErrorBody",
    "ErrorEnvelope",
    "FailureDetail",
    "InvestigationCreateRequest",
    "InvestigationErrorResponse",
    "InvestigationResponse",
    "InvestigationStatus",
    "Language",
    "LimitationResponse",
    "RecordedErrorDetail",
    "TextInvestigationRequest",
    "TimelineEventResponse",
]

#: Upper bound on submitted text. A submission larger than this is refused at the
#: edge rather than handed to extraction, where it would be truncated silently and
#: the red-flag spans computed over it would refer to text the client never saw.
MAX_TEXT_LENGTH = 20_000


class Language(str, Enum):
    """Languages the report layer will eventually render (D-027).

    Accepted and echoed back so the contract does not have to change when Phase
    15 ships translations. **No translation happens in Phase 8**: every
    `message` in this API is English only, and the declared language is
    recorded, not honoured.
    """

    EN = "en"
    HI = "hi"
    MR = "mr"


class InvestigationStatus(str, Enum):
    """Top-level outcome of a run, derived from the graph's own timeline.

    - `COMPLETED` — every stage that ran, ran to completion.
    - `PARTIAL` — the run produced a usable result, but at least one stage was
      skipped or could not finish (no search key, an extraction fallback).
    - `FAILED` — the run ended on a recorded error and has no assessment.

    The distinction is not cosmetic. `PARTIAL` is a **200**, not a 5xx: a search
    provider being down says nothing about the caller's request, and a product
    that errored on it would look broken exactly when it is being truthful about
    a degraded check (D-009).
    """

    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class TextInvestigationRequest(BaseModel):
    """Body for `POST /api/investigations/text`.

    Field names are kept short and match the documented API spec; see
    `InvestigationCreateRequest` for the typed multi-kind variant.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        min_length=1,
        max_length=MAX_TEXT_LENGTH,
        description="Content to investigate. Whitespace-only text is refused.",
    )
    language: Language = Field(
        default=Language.EN,
        description="Recorded for Phase 15 rendering; not translated in this version.",
    )


class InvestigationCreateRequest(BaseModel):
    """Body for `POST /api/investigations`, the typed multi-kind entry point.

    `input_type` is explicit rather than inferred from the body so that an
    unsupported kind is refused with a typed `422` instead of being guessed at.
    Only `TEXT` is analysed today; `URL`, `IMAGE` and `PDF` are recognised and
    refused so that a client learns which ones are missing rather than receiving
    a text analysis of something it did not send (Phase 8 scope).
    """

    model_config = ConfigDict(extra="forbid")

    input_type: InvestigationInputType = Field(
        default=InvestigationInputType.TEXT,
        description="Declared input kind. Only TEXT is analysed by this version.",
    )
    text: str = Field(
        min_length=1,
        max_length=MAX_TEXT_LENGTH,
        description="Content to investigate, for the TEXT input type.",
    )
    language: Language = Field(
        default=Language.EN,
        description="Recorded for Phase 15 rendering; not translated in this version.",
    )


class TimelineEventResponse(BaseModel):
    """One stage entry in the returned timeline."""

    model_config = ConfigDict(extra="forbid")

    stage: GraphStage = Field(description="Which stage this entry describes.")
    status: TimelineStatus = Field(description="How that stage finished.")
    message: str = Field(description="Fixed wording describing the mechanical outcome.")
    at: datetime = Field(description="When the stage finished.")


class LimitationResponse(BaseModel):
    """A human-readable limitation, kept alongside its code.

    Kept separate from the flat `limitations` code array on purpose: the codes
    exist for branching, the messages exist for display, and collapsing them into
    one list would force every client to re-derive half the information.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="Stable machine-readable limitation code.")
    stage: GraphStage = Field(description="Stage that recorded the limitation.")
    message: str = Field(description="Fixed wording stating what did not happen.")


class InvestigationErrorResponse(BaseModel):
    """A recorded failure from the graph.

    Carries the exception *type* only. Provider messages and stack traces stay in
    the logs: an error object here is one a future UI may render verbatim.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="Stable machine-readable failure code.")
    stage: GraphStage = Field(description="Stage that failed.")
    message: str = Field(description="Fixed wording safe to surface.")
    error_type: str | None = Field(
        default=None,
        description="Exception class name only, for diagnosis.",
    )


class InvestigationResponse(BaseModel):
    """The complete result of one investigation.

    Returned synchronously with the full run rather than as a job handle:
    persistence is Phase 9, so there is nothing to poll, and a client that
    receives `investigation_id` with nothing behind it would be invited to build
    a lookup that cannot work.

    The domain models are embedded rather than reshaped. `Claim`, `Entity`,
    `RedFlag`, `VerificationResult`, `EvidenceResponse` and `RiskAssessment` are
    the Phase 1-6 objects themselves: re-projecting them here would create a
    second definition of "a claim" that could drift from the first.
    """

    model_config = ConfigDict(extra="forbid")

    investigation_id: str = Field(
        description="Deterministic id derived from the submitted input, so a re-run is comparable.",
    )
    status: InvestigationStatus = Field(
        description="COMPLETED, PARTIAL or FAILED; derived from the graph's own stage timeline.",
    )
    input_type: InvestigationInputType = Field(description="Declared input kind.")
    language: Language = Field(
        default=Language.EN,
        description="Echo of the requested language; no translation is performed.",
    )
    current_stage: str = Field(description="The last stage that ran.")

    claims: list[Claim] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    red_flags: list[RedFlag] = Field(default_factory=list)
    verification_results: list[VerificationResult] = Field(default_factory=list)
    evidence: list[EvidenceResponse] = Field(default_factory=list)
    risk_assessment: RiskAssessment | None = Field(
        default=None,
        description="The Phase 6 assessment, forwarded verbatim. Absent when the run failed.",
    )

    timeline: list[TimelineEventResponse] = Field(default_factory=list)
    limitations: list[str] = Field(
        default_factory=list,
        description="Deduplicated warning codes, in first-seen order. Branch on these, not on text.",
    )
    warnings: list[LimitationResponse] = Field(
        default_factory=list,
        description="Human-readable form of the same limitations, with the stage that recorded each.",
    )
    errors: list[InvestigationErrorResponse] = Field(
        default_factory=list,
        description="Recorded failures. Any entry here means status is FAILED.",
    )

    started_at: datetime | None = None
    completed_at: datetime | None = None


class RecordedErrorDetail(BaseModel):
    """One recorded graph failure, flattened for an error envelope.

    Kept separate from `InvestigationErrorResponse` because this one travels
    inside `detail` on a *failed request*, where there is no investigation to
    return: no id, no status, no partial results.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="Stable machine-readable failure code.")
    stage: GraphStage = Field(description="Stage that failed.")
    error_type: str | None = Field(
        default=None,
        description="Exception class name only, for diagnosis.",
    )


class FailureDetail(BaseModel):
    """The `detail` payload carried by every failed investigation request.

    Lists **every** recorded error rather than only the first. The graph stops a
    run at the first error, so today that list always has one entry, but reporting
    the whole set means a later phase that records several failures does not need
    a breaking change to the contract.

    The input-type fields are present only when the caller submitted a kind this
    version does not analyse. They are there so a client can correct its
    submission rather than only log the rejection — the product commits to four
    input types and this version implements one, so "not yet" is the useful
    answer.
    """

    model_config = ConfigDict(extra="forbid")

    errors: list[RecordedErrorDetail] = Field(
        default_factory=list,
        description="Every recorded failure, in the order the graph recorded them.",
    )
    submitted_input_type: str | None = Field(
        default=None,
        description=(
            "The refused input type. Present only when the input type was the "
            "reason for the refusal."
        ),
    )
    supported_input_types: list[InvestigationInputType] = Field(
        default_factory=list,
        description="Input types this version analyses. Present only on an unsupported-type refusal. Present only on an unsupported-type refusal.",
    )


class ErrorBody(BaseModel):
    """Inner object of the documented error envelope."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    detail: FailureDetail | None = None


class ErrorEnvelope(BaseModel):
    """The single error shape every failing endpoint returns.

    Declared here so `/openapi.json` documents the failures alongside the
    successes. Without it the generated schema advertises only the 200 path, and
    a client has no way to learn that `detail` is structured rather than a string.
    """

    model_config = ConfigDict(extra="forbid")

    error: ErrorBody
