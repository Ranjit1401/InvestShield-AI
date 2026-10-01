"""Search schemas (Phase 3, §7/§8).

Search results are *inputs to later verification*, never conclusions. A
`SearchResult` describes a document that was found; a `SearchResponse` describes
what happened when we went looking. Neither type carries a field that could be
misread as a truth determination about an investment claim.

Three states are modelled explicitly and must stay distinguishable, because
collapsing them is how a product ends up asserting things it never checked:

| State | Meaning | Phase 4 must not treat it as |
| --- | --- | --- |
| `OK` with zero results | The provider answered; nothing matched | A contradiction |
| `UNAVAILABLE` | No credentials, so we never asked | Evidence either way |
| `ERROR` | We asked and the attempt failed | Evidence either way |

`SourceType` records *what kind of organisation published a document*. It says
nothing about whether that document supports or refutes a claim, and
`SOURCE_PRIORITY` is a ranking of customary authority for **later** stages to
consider — never a verdict (D-006, D-007, D-017).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.capabilities import utc_now

#: Error codes surfaced to the pipeline. Never replaced with exception text, and
#: never used to describe a *result* — only what happened while looking.
SEARCH_NOT_CONFIGURED = "SEARCH_NOT_CONFIGURED"
SEARCH_INVALID_QUERY = "SEARCH_INVALID_QUERY"
SEARCH_SERVICE_ERROR = "SEARCH_SERVICE_ERROR"
SEARCH_TIMEOUT = "SEARCH_TIMEOUT"
SEARCH_RATE_LIMITED = "SEARCH_RATE_LIMITED"
SEARCH_AUTH_ERROR = "SEARCH_AUTH_ERROR"
SEARCH_INVALID_RESPONSE = "SEARCH_INVALID_RESPONSE"

#: The complete set of codes this phase can emit, for exhaustive tests.
SEARCH_ERROR_CODES = frozenset(
    {
        SEARCH_NOT_CONFIGURED,
        SEARCH_INVALID_QUERY,
        SEARCH_SERVICE_ERROR,
        SEARCH_TIMEOUT,
        SEARCH_RATE_LIMITED,
        SEARCH_AUTH_ERROR,
        SEARCH_INVALID_RESPONSE,
    }
)


class SourceType(str, Enum):
    """What kind of organisation published a result.

    The classification is a statement about the *publisher*, derived
    deterministically from the hostname. It is not a statement about the
    document's claims, and never about the truth of an investment claim.
    """

    REGULATOR = "REGULATOR"
    GOVERNMENT = "GOVERNMENT"
    EXCHANGE = "EXCHANGE"
    OFFICIAL_ENTITY = "OFFICIAL_ENTITY"
    TRUSTED_SECONDARY = "TRUSTED_SECONDARY"
    GENERAL_WEB = "GENERAL_WEB"
    UNKNOWN = "UNKNOWN"


#: Customary authority ordering, lowest number = most authoritative category.
#:
#: This ranks *source categories* for later stages. It is emphatically not a
#: truth scale: a REGULATOR page that says nothing about a claim is not
#: "contradicting" it, and a GENERAL_WEB page that supports a claim does not
#: make it true. Phase 4 decides what a given document means; Phase 3 only
#: reports where it came from (D-017).
SOURCE_PRIORITY: dict[SourceType, int] = {
    SourceType.REGULATOR: 1,
    SourceType.GOVERNMENT: 2,
    SourceType.EXCHANGE: 3,
    SourceType.OFFICIAL_ENTITY: 4,
    SourceType.TRUSTED_SECONDARY: 5,
    SourceType.GENERAL_WEB: 6,
    SourceType.UNKNOWN: 7,
}

#: Schemas we are willing to treat as a result URL. Anything else (``file:``,
#: ``javascript:``, ``data:``) is rejected rather than recorded, so a hostile
#: search result cannot smuggle an unusable scheme into a later fetch stage.
ALLOWED_RESULT_SCHEMES = frozenset({"http", "https"})


class SearchStatus(str, Enum):
    """Outcome of a search attempt."""

    #: The provider answered. `results` may legitimately be empty.
    OK = "OK"
    #: No credentials were configured, so no search was attempted.
    UNAVAILABLE = "UNAVAILABLE"
    #: A search was attempted and failed. Not the same as "no results".
    ERROR = "ERROR"


class SearchResult(BaseModel):
    """One normalized document returned by a search provider.

    Attributes:
        title: Result title as published, with markup stripped.
        url: The result URL. Must be ``http``/``https``.
        snippet: Extracted text excerpt, or ``None`` when the provider gave none.
        source_domain: Lower-cased registrable hostname, derived from `url`.
        source_type: Publisher category, filled in by `SearchService`.
        source_priority: Rank derived from `source_type`; see `SOURCE_PRIORITY`.
        retrieved_at: When this result was retrieved (UTC).
        position: 1-based position in the provider's result list.
        canonical_url: Deduplication key derived from `url`.
    """

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1, description="Result title as published.")
    url: str = Field(min_length=1, description="Absolute http(s) result URL.")
    snippet: str | None = None
    source_domain: str = Field(default="", description="Registrable hostname; derived when empty.")
    source_type: SourceType = Field(
        default=SourceType.UNKNOWN,
        description="Publisher category. Assigned by SearchService, not by the caller.",
    )
    retrieved_at: datetime = Field(default_factory=utc_now)
    position: int = Field(default=1, ge=1)
    canonical_url: str = Field(default="", description="Deduplication key; derived when empty.")

    @field_validator("url")
    @classmethod
    def _require_http_url(cls, value: str) -> str:
        """Reject any scheme other than http/https."""
        parts = urlsplit(value.strip())
        if parts.scheme.lower() not in ALLOWED_RESULT_SCHEMES:
            raise ValueError("result url must use http or https")
        if not parts.netloc:
            raise ValueError("result url must include a host")
        return value.strip()

    @field_validator("title")
    @classmethod
    def _require_title(cls, value: str) -> str:
        """A result with no title carries no information for a user."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("result title must not be empty")
        return stripped

    @model_validator(mode="after")
    def _derive_missing_fields(self) -> SearchResult:
        """Fill `source_domain` and `canonical_url` from the URL.

        Derived here rather than trusted from the caller so that every result
        carries a usable deduplication key regardless of which provider
        produced it.
        """
        from app.services.search.domain import canonicalize_url, extract_domain

        updates: dict[str, str] = {}
        if not self.source_domain:
            updates["source_domain"] = extract_domain(self.url) or ""
        if not self.canonical_url:
            updates["canonical_url"] = canonicalize_url(self.url) or self.url.strip()
        if updates:
            object.__setattr__(self, "__dict__", {**self.__dict__, **updates})
        return self

    @property
    def priority(self) -> int:
        """Customary authority rank of this result's source category."""
        return SOURCE_PRIORITY[self.source_type]


class SearchResponse(BaseModel):
    """Outcome of one search, including *why* it looks the way it does.

    Attributes:
        query: The normalized query as sent to the provider.
        results: Normalized, de-duplicated, source-classified results.
        provider: Provider name that was used, e.g. ``serpapi``.
        status: `SearchStatus`; `UNAVAILABLE` and `ERROR` are not "no results".
        warnings: Human-readable notes. Never contain credentials or URLs with
            embedded secrets.
        error_code: Stable error code when `status` is not `OK`.
        searched_at: When the search ran (UTC).
        provider_query_sent: Whether a request was actually issued. `False` for
            `UNAVAILABLE`, so "we never looked" is auditable.
    """

    model_config = ConfigDict(frozen=True)

    query: str = ""
    results: tuple[SearchResult, ...] = ()
    provider: str = "unknown"
    status: SearchStatus = SearchStatus.OK
    warnings: tuple[str, ...] = ()
    error_code: str | None = None
    searched_at: datetime = Field(default_factory=utc_now)
    provider_query_sent: bool = False

    @model_validator(mode="after")
    def _reject_inconsistent_state(self) -> SearchResponse:
        """Keep status, error code and sent-flag mutually consistent.

        A failure must never look like an empty successful search, because the
        two mean opposite things to every consumer downstream (D-009).
        """
        if self.status is SearchStatus.OK:
            if self.error_code is not None:
                raise ValueError("a successful search must not carry an error code")
        elif not self.error_code:
            raise ValueError("an unsuccessful search must carry an error code")
        if self.error_code is not None and self.error_code not in SEARCH_ERROR_CODES:
            raise ValueError(f"unknown search error code: {self.error_code}")
        if self.status is SearchStatus.UNAVAILABLE and self.provider_query_sent:
            raise ValueError("an unavailable search cannot have issued a request")
        return self

    @property
    def success(self) -> bool:
        """True only when a search actually completed."""
        return self.status is SearchStatus.OK

    @property
    def degraded(self) -> bool:
        """True when results are absent for a reason other than 'nothing matched'.

        Phase 4 reads this to distinguish "we found nothing" from "we could not
        look", which is the difference between `UNVERIFIED` and
        `INSUFFICIENT_EVIDENCE`.
        """
        return self.status in (SearchStatus.UNAVAILABLE, SearchStatus.ERROR)


__all__ = [
    "ALLOWED_RESULT_SCHEMES",
    "SEARCH_AUTH_ERROR",
    "SEARCH_ERROR_CODES",
    "SEARCH_INVALID_QUERY",
    "SEARCH_INVALID_RESPONSE",
    "SEARCH_NOT_CONFIGURED",
    "SEARCH_RATE_LIMITED",
    "SEARCH_SERVICE_ERROR",
    "SEARCH_TIMEOUT",
    "SOURCE_PRIORITY",
    "SearchResponse",
    "SearchResult",
    "SearchStatus",
    "SourceType",
]