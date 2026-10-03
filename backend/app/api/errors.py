"""Typed API failures and their mapping onto HTTP status codes (Phase 8).

Every failing endpoint returns the same envelope — ``{"error": {"code",
"message", "detail"}}`` — so a client handles one failure shape rather than
learning a new one per route. What differs is the status code, and the code is
chosen by **whose fault the failure is**, not by how serious it feels:

- The **caller** sent something unusable → `400`/`422`. An empty submission or a
  `URL` input type are the client's to fix, so the response must say so.
- **Our** pipeline broke its own contract → `500`. A stage raising an exception
  it documented it would never raise means the service beneath it is wrong, and
  returning anything softer would dress a defect up as a normal outcome.
- An **external dependency** being unavailable → `200` with `status: PARTIAL` and
  the limitation listed. A search provider being down says nothing about whether
  the request was good, and failing the request would make the product look
  broken at the exact moment it is correctly reporting that it checked less than
  it wanted to (D-009).

That third case is why `GraphError` values are mapped here rather than raised.
The graph already distinguishes them: it records a structured error for each, and
this module only decides what HTTP status that record deserves.

Provider messages and stack traces never cross this boundary. Only the graph's
own fixed wording and the exception *type* are exposed; the full cause is
logged, where it is useful, and kept out of a body a future UI may render
verbatim.
"""

from __future__ import annotations

from fastapi import status

from app.graph.nodes import (
    IMAGE_CAPABILITY_UNAVAILABLE_CODES,
    IMAGE_CLIENT_FAULT_CODES,
    PDF_CAPABILITY_UNAVAILABLE_CODES,
    PDF_CLIENT_FAULT_CODES,
    URL_CAPABILITY_UNAVAILABLE_CODES,
    URL_CLIENT_FAULT_CODES,
    URL_SITE_FAULT_CODES,
)
from app.graph.state import GraphError, InvestigationInputType
from app.schemas.api import FailureDetail, Language, RecordedErrorDetail

__all__ = [
    "ApiError",
    "CapabilityUnavailable",
    "GraphContractError",
    "InvestigationNotFound",
    "LanguageNotAccepted",
    "SubmissionRejected",
    "SUPPORTED_INPUT_TYPES",
    "SUPPORTED_LANGUAGES",
    "UNPROCESSABLE_CONTENT",
    "UnsupportedInputType",
    "UpstreamSiteError",
    "error_status_for",
    "raise_for_graph_errors",
]

#: Input kinds this version actually analyses. Named here rather than imported
#: from the route so the error contract cannot drift silently when a new kind
#: ships: adding one to this tuple is a deliberate act, not a side effect.
SUPPORTED_INPUT_TYPES: tuple[InvestigationInputType, ...] = (
    InvestigationInputType.TEXT,
    InvestigationInputType.URL,
    InvestigationInputType.IMAGE,
    InvestigationInputType.PDF,
)

#: Starlette renamed this constant when the status was reclassified, and the old
#: name now resolves through a deprecation shim. Resolving it once keeps the
#: suite warning-free; the literal is the fallback for older Starlette.
_UNPROCESSABLE_CONTENT: int = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)

#: Public alias, so the validation handler in `app.main` and this module agree on
#: one number rather than each resolving its own constant.
UNPROCESSABLE_CONTENT = _UNPROCESSABLE_CONTENT


class ApiError(Exception):
    """Base class for failures that render as the documented error envelope.

    Attributes:
        code: Stable machine-readable code clients branch on.
        message: Fixed wording safe to surface.
        detail: Optional diagnostic payload. A model, a list of codes, or
            ``None``. Never a stack trace or a provider message.
        status_code: HTTP status this failure renders as.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        detail: object | None = None,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.status_code = status_code


#: Report languages this version renders. Named here so the
#: language refusal and the localization layer cannot disagree
#: about which languages exist: adding one is a deliberate act.
SUPPORTED_LANGUAGES: tuple[str, ...] = (
    Language.EN.value,
    Language.HI.value,
    Language.MR.value,
)


class SubmissionRejected(ApiError):
    """The submission was unusable; the caller must change it and retry.

    Raised for an empty or whitespace-only body. `422` rather than `400` because
    the body *is* structurally valid JSON of the right shape — there is simply
    nothing in it to investigate, which is what the other `422` in this API
    already means.
    """

    def __init__(self, code: str, message: str, *, detail: object | None = None) -> None:
        super().__init__(
            code,
            message,
            detail=detail,
            status_code=_UNPROCESSABLE_CONTENT,
        )


class UnsupportedInputType(SubmissionRejected):
    """The declared input type is recognised but not analysed by this version.

    Carries the supported kinds in `detail` so a client can correct itself
    instead of only logging the rejection.
    """

    def __init__(self, code: str, message: str, *, detail: object | None = None) -> None:
        super().__init__(code, message, detail=detail)


class LanguageNotAccepted(SubmissionRejected):
    """The requested report language is not one this version renders.

    `422` because the caller must change the `language` parameter and
    retry. Carries the languages this version renders in `detail` so a
    client can correct itself rather than only logging the rejection —
    the same courtesy `UnsupportedInputType` extends to input kinds.
    """

    def __init__(self, code: str, message: str, *, detail: object | None = None) -> None:
        super().__init__(code, message, detail=detail)


class InvestigationNotFound(ApiError):
    """No stored investigation carries the requested id.

    `404`, and the caller's to fix. The id is a content digest the client holds
    from a previous `POST`, so a miss means either the id was never issued or the
    run is not in this database — either way nothing about the request was wrong
    and nothing about our pipeline is at fault.

    Carries no detail. Echoing the requested id back would tell a caller
    nothing they do not already know, and a future enumeration attempt learns
    only what a `404` already says.
    """

    def __init__(self, code: str, message: str, *, detail: object | None = None) -> None:
        super().__init__(
            code,
            message,
            detail=detail,
            status_code=status.HTTP_404_NOT_FOUND,
        )


class UpstreamSiteError(ApiError):
    """The submitted site could not be read, and the request was not at fault.

    `502 Bad Gateway` on purpose. Phase 12 asks the server to retrieve a page
    the caller named, and when that retrieval fails — a DNS failure, a timeout,
    an error status, a redirect loop, an oversized or non-HTML response — the
    request was well-formed and the fault is the upstream's. The caller cannot
    fix it by changing the URL, so `422` would wrongly tell them to, and `500`
    would wrongly tell them this product is broken.

    `422` is reserved for the refusals that *are* the caller's to fix: a
    malformed address, a scheme this version will not fetch, or a destination the
    SSRF policy blocks. Those are in `URL_CLIENT_FAULT_CODES`.
    """

    def __init__(self, code: str, message: str, *, detail: object | None = None) -> None:
        super().__init__(
            code,
            message,
            detail=detail,
            status_code=status.HTTP_502_BAD_GATEWAY,
        )


class GraphContractError(ApiError):
    """A stage broke a documented contract, ending the run.

    `500` on purpose. The graph guarantees `extract` never raises and that a
    provider failure never propagates; an exception that escaped anyway is a
    defect below this layer, and reporting it as a degraded result would ship a
    plausible-looking investigation built on a stage that silently did nothing.
    """

    def __init__(self, code: str, message: str, *, detail: object | None = None) -> None:
        super().__init__(
            code,
            message,
            detail=detail,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


#: Graph error codes that describe the *submission*, not the pipeline. Everything
#: else recorded as a `GraphError` is a contract violation by our own services
#: and maps to `500`. The distinction is carried by the code rather than by the
#: stage, because `INPUT_TYPE_NOT_SUPPORTED` and `INPUT_EMPTY` are both recorded
#: at the `input` stage while every other code in `ERROR_CODES` names a stage
#: that was expected to succeed.
_CLIENT_FAULT_CODES: frozenset[str] = frozenset(
    {"INPUT_EMPTY", "INPUT_TYPE_NOT_SUPPORTED"}
) | set(URL_CLIENT_FAULT_CODES) | set(IMAGE_CLIENT_FAULT_CODES) | set(
    PDF_CLIENT_FAULT_CODES
)

#: Failures caused by the submitted site or by our inability to reach it. These
#: render as `502`, because the request was valid and the upstream we were asked
#: to read could not be read. Imported from `app.graph.nodes` rather than
#: re-listed, so the graph's classification and the API's status mapping cannot
#: disagree about which code means what.
_SITE_FAULT_CODES: frozenset[str] = frozenset(URL_SITE_FAULT_CODES)

#: The request was well-formed and this deployment does not offer URL,
#: image or PDF analysis, so nothing was attempted. `503` rather than
#: `500` because nothing is broken here, and rather than `502` because
#: there is no upstream that failed.
_CAPABILITY_UNAVAILABLE_CODES: frozenset[str] = (
    URL_CAPABILITY_UNAVAILABLE_CODES
    | IMAGE_CAPABILITY_UNAVAILABLE_CODES
    | PDF_CAPABILITY_UNAVAILABLE_CODES
)


class CapabilityUnavailable(ApiError):
    """This deployment does not offer the requested capability right now.

    `503 Service Unavailable`. Distinct from both `422` — the request was fine —
    and `502` — nothing upstream was contacted, so there is no upstream to have
    failed. Saying so is what lets a client tell "this product cannot do that
    yet" apart from "the site I sent you is down", which are different things to
    a user and to whoever is on call.
    """

    def __init__(self, code: str, message: str, *, detail: object | None = None) -> None:
        super().__init__(
            code,
            message,
            detail=detail,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


def _detail(
    errors: tuple[GraphError, ...],
    submitted_input_type: str | None = None,
) -> dict[str, object]:
    """Build the `detail` payload for a failed request.

    Args:
        errors: Every recorded failure.
        submitted_input_type: The kind the caller submitted, when an unsupported
            kind is the reason for the refusal.

    Returns:
        A `FailureDetail` rendered for JSON. Serialised here rather than handed
        to `JSONResponse` as a model, because a response body must not depend on
        an encoder being configured for Pydantic.
    """
    payload = FailureDetail(
        errors=[
            RecordedErrorDetail(
                code=error.code,
                stage=error.stage,
                error_type=error.error_type,
            )
            for error in errors
        ],
        submitted_input_type=submitted_input_type,
        supported_input_types=SUPPORTED_INPUT_TYPES
        if submitted_input_type is not None
        else [],
    )
    return payload.model_dump(mode="json")


def error_status_for(error: GraphError) -> int:
    """Return the HTTP status one recorded graph failure deserves.

    Args:
        error: A :class:`~app.graph.state.GraphError` from a finished state.

    Returns:
        `422` for a submission the caller must fix, `502` when the submitted
        site could not be read, `500` for a contract violation.
    """
    if error.code in _CLIENT_FAULT_CODES:
        return _UNPROCESSABLE_CONTENT
    if error.code in _SITE_FAULT_CODES:
        return status.HTTP_502_BAD_GATEWAY
    if error.code in _CAPABILITY_UNAVAILABLE_CODES:
        return status.HTTP_503_SERVICE_UNAVAILABLE
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def raise_for_graph_errors(
    errors: tuple[GraphError, ...],
    *,
    submitted_input_type: str | None = None,
) -> None:
    """Raise the first recorded failure, typed by who caused it.

    Called only when the run recorded an **error**. A run that recorded warnings
    and no errors is a successful, possibly partial, investigation and is
    returned as a `200` — see `investigation_status`.

    Args:
        errors: The accumulated :class:`~app.graph.state.GraphError` values.
        submitted_input_type: The kind the caller declared, so an unsupported
            kind can be refused with the list of kinds that do work.

    Raises:
        UnsupportedInputType: If the failure was an unanalysed input type.
        SubmissionRejected: If the failure was an empty submission or a URL the
            caller must fix.
        UpstreamSiteError: If the submitted site could not be retrieved.
        CapabilityUnavailable: If this deployment does not offer URL analysis.
        GraphContractError: If a stage broke its documented contract.
    """
    if not errors:
        return

    first = errors[0]

    if first.code == "INPUT_TYPE_NOT_SUPPORTED":
        # Only this refusal carries the input-type fields. Reporting them for an
        # empty submission would be noise: the caller already sent TEXT, which is
        # the supported kind, so naming it explains nothing.
        detail = _detail(errors, submitted_input_type=submitted_input_type)
        raise UnsupportedInputType(first.code, first.message, detail=detail)
    if first.code == "INPUT_EMPTY":
        raise SubmissionRejected(first.code, first.message, detail=_detail(errors))
    if first.code in _SITE_FAULT_CODES:
        raise UpstreamSiteError(first.code, first.message, detail=_detail(errors))
    if first.code in _CAPABILITY_UNAVAILABLE_CODES:
        raise CapabilityUnavailable(first.code, first.message, detail=_detail(errors))
    if first.code in _CLIENT_FAULT_CODES:
        # The URL the caller must fix: a malformed address, an unsupported
        # scheme, or a destination the SSRF policy refuses. Retrying the same
        # URL unchanged fails identically, which is what `422` means.
        raise SubmissionRejected(first.code, first.message, detail=_detail(errors))
    raise GraphContractError(first.code, first.message, detail=_detail(errors))
