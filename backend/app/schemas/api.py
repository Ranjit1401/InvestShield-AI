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
from app.schemas.url import MAX_URL_LENGTH, UrlSource
from app.schemas.evidence import EvidenceResponse
from app.schemas.ocr import ImageSource
from app.schemas.pdf import PdfSource
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
    "InvestigationListResponse",
    "InvestigationResponse",
    "InvestigationStatus",
    "InvestigationSummaryResponse",
    "Language",
    "LimitationResponse",
    "LocalizedReport",
    "RecordedErrorDetail",
    "TextInvestigationRequest",
    "TimelineEventResponse",
    "UrlInvestigationRequest",
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
    `TEXT` and `URL` are analysed as of Phase 12; `IMAGE` and `PDF` are
    recognised and refused so that a client learns which ones are missing rather
    than receiving an analysis of something it did not send.

    For `URL`, `text` carries the address rather than prose. The field is not
    renamed to `url` because this endpoint is deliberately kind-agnostic: one
    body shape that means "the content for this kind" is what lets the typed
    endpoint stay a thin pass-through instead of growing a branch per input kind.
    `POST /api/investigations/url` is the form that names the field `url`.
    """

    model_config = ConfigDict(extra="forbid")

    input_type: InvestigationInputType = Field(
        default=InvestigationInputType.TEXT,
        description="Declared input kind. TEXT and URL are analysed by this version.",
    )
    text: str = Field(
        min_length=1,
        max_length=MAX_TEXT_LENGTH,
        description="Content to investigate. For the URL input type, the address to investigate.",
    )
    language: Language = Field(
        default=Language.EN,
        description="Recorded for Phase 15 rendering; not translated in this version.",
    )


class UrlInvestigationRequest(BaseModel):
    """Body for `POST /api/investigations/url`.

    The dedicated URL form of the same investigation, so a client does not have
    to know that the typed endpoint names the address `text`.

    The length bound here is `MAX_URL_LENGTH`, not `MAX_TEXT_LENGTH`. A URL is
    bounded by what a URL can be; bounding it as prose would either reject a
    legitimately long link or quietly allow a longer one than the fetcher's own
    limit, producing two different answers to the same question depending on
    which layer rejected it.

    Only presence and length are enforced here. Whether the address is a URL at
    all, whether its scheme may be fetched, and whether it points at a public
    host are all decided by `app.services.url_guards` at retrieval time, because
    they need one decision in one place and the answer to the last of those is
    not knowable without resolving the name (Phase 12 §5).
    """

    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        min_length=1,
        max_length=MAX_URL_LENGTH,
        description=(
            "The web address to investigate. Must begin with http:// or https://. "
            "Addresses that are not public internet destinations are refused."
        ),
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


class LocalizedReport(BaseModel):
    """The localized presentation layer for one investigation (Phase 15).

    This is the **only** part of a response that differs by language.
    The canonical fields — `claims`, `entities`, `red_flags`,
    `verification_results`, `evidence`, `risk_assessment` and
    `timeline` — are built from the investigation state and are
    byte-for-byte identical in every language; this model carries the
    human-facing layer alone: the section titles, the controlled-
    vocabulary labels and the fixed report prose, translated
    deterministically from `app/locales`.

    It deliberately carries **no** score, no verdict and no fact. A
    claim's text, a red flag's matched substring, an evidence excerpt
    and a verification reason are source material the pipeline
    produced in English, and translating them would risk changing
    their meaning, so they stay canonical. Localizing a report can
    therefore never change what the investigation found — the risk
    score, the risk level, every claim id, red-flag code, entity id,
    evidence URL and verification verdict are the same object the
    caller would have received in English.
    """

    model_config = ConfigDict(extra="forbid")

    language: Language = Field(description="The language this report is rendered in.")
    investigation_id: str = Field(
        description="The canonical investigation this report describes.",
    )
    sections: dict[str, str] = Field(
        default_factory=dict,
        description="Translated section titles, keyed by stable section key.",
    )
    labels: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        description=(
            "Translated controlled-vocabulary labels, keyed by vocabulary "
            "then by the vocabulary's own value (e.g. "
            "`labels['risk_level']['HIGH']`)."
        ),
    )
    summary: str = Field(
        default="",
        description="Translated one-paragraph summary built from canonical counts.",
    )
    safety_guidance: str = Field(default="", description="Translated safety guidance.")
    risk_caveat: str = Field(
        default="",
        description="Translated statement that the risk score is not a probability.",
    )
    disclaimer: str = Field(default="", description="Translated disclaimer.")


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
    report: LocalizedReport | None = Field(
        default=None,
        description=(
            "Phase 15 localized presentation layer: translated section titles, "
            "controlled-vocabulary labels and fixed report prose. The canonical "
            "fields beside it are identical in every language; only this layer "
            "differs. Populated on every investigation response, in the "
            "requested language (English by default)."
        ),
    )
    current_stage: str = Field(description="The last stage that ran.")

    url_source: UrlSource | None = Field(
        default=None,
        description=(
            "Phase 12 provenance for a URL investigation: the submitted URL, the host "
            "contacted, the addresses it resolved to, and whether the page was "
            "truncated. Null for every non-URL input kind."
        ),
    )

    image_source: ImageSource | None = Field(
        default=None,
        description=(
            "Phase 13 provenance for an image investigation: the submitted file, "
            "what it decoded to, and what the recognition engine recovered. Null "
            "for every non-IMAGE input kind."
        ),
    )

    pdf_source: PdfSource | None = Field(
        default=None,
        description=(
            "Phase 14 provenance for a PDF investigation: the submitted file, "
            "what it parsed to, how many pages it has, and what the extraction "
            "engine recovered. Null for every non-PDF input kind."
        ),
    )

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


class InvestigationSummaryResponse(BaseModel):
    """One entry in the run history.

    A summary rather than an investigation. It answers "what did I run, and did
    it work?" without loading twelve tables per row, which is what lets the
    history endpoint stay a cheap query instead of becoming a report.

    The counts are stored alongside the run rather than aggregated on read, so
    the numbers here are the numbers that were counted when the run happened.
    """

    model_config = ConfigDict(extra="forbid")

    investigation_id: str = Field(
        description="The id a client can pass to GET /api/investigations/{id}.",
    )
    status: InvestigationStatus = Field(description="Outcome of that run.")
    input_type: InvestigationInputType = Field(description="Kind of input submitted.")
    current_stage: str = Field(description="The last stage that ran.")
    started_at: datetime = Field(description="When the run began.")
    completed_at: datetime | None = Field(default=None, description="When it finished, if it did.")
    created_at: datetime = Field(
        description="When the run was stored. This is what the history is ordered by.",
    )
    claim_count: int = Field(default=0, description="Claims extracted.")
    entity_count: int = Field(default=0, description="Entities extracted.")
    red_flag_count: int = Field(default=0, description="Red flags detected.")
    verification_count: int = Field(default=0, description="Claims verified against external records.")
    source_count: int = Field(default=0, description="Distinct documents cited across the run.")
    evidence_count: int = Field(default=0, description="Evidence items assembled.")
    factor_count: int = Field(default=0, description="Risk factors recorded.")


class InvestigationListResponse(BaseModel):
    """A page of run history, newest first.

    `total` is the count of *all* stored runs, not of the page, so a client can
    render "showing 20 of 137" without a second request. It is counted from the
    same table as the page, so the two can never come from different queries and
    disagree about how much history exists.
    """

    model_config = ConfigDict(extra="forbid")

    investigations: list[InvestigationSummaryResponse] = Field(default_factory=list)
    total: int = Field(description="Total number of stored runs.")
    limit: int = Field(description="Maximum entries this page could hold.")
    offset: int = Field(description="Entries skipped before this page.")


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
