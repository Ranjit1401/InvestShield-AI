"""URL investigation input and output contracts (Phase 12).

Kept apart from :mod:`app.schemas.api` because these describe *a URL input
modality* rather than the API surface, and are consumed by the graph, the
persistence layer and the adapter. The API request model itself lives in
:mod:`app.schemas.api` so every request body the API accepts is discoverable in
one place.

Two rules govern everything here.

**A website is evidence, not a verdict.** `UrlSource` records what was fetched
and what it said about itself. Nothing in this module states that a host is
malicious, and the field names are chosen so that a value cannot be read as a
finding: `page_title` is what the page called itself, `is_https` is a transport
fact, and neither implies anything about legitimacy.

**A URL is untrusted input.** It arrives from a user, is dereferenced by the
server, and names a host that may be hostile. The validation here is therefore
about *accepting or refusing* a submission, never about scoring it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "MAX_URL_LENGTH",
    "UrlInvestigationRequest",
    "UrlSource",
    "WebsiteDocument",
]

#: Maximum submitted URL length. Long enough for a real deep link with query
#: parameters, short enough that the value is unmistakably a URL rather than an
#: attempt to push a large payload through a field meant for a link.
MAX_URL_LENGTH = 2_048


class UrlInvestigationRequest(BaseModel):
    """Body for ``POST /api/investigations/url``.

    Field-level validation here is limited to what can be decided without a
    network call: length, and that the value is a single string. Scheme, host and
    address checks belong to
    :class:`~app.services.url_fetch.URLFetchService`, because deciding whether a
    host is reachable *is* the fetch, and a validator that duplicated those rules
    would be a second SSRF check that could disagree with the real one.
    """

    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        min_length=1,
        max_length=MAX_URL_LENGTH,
        description="The page to investigate. Must be an absolute http:// or https:// URL.",
    )
    language: str = Field(
        default="en",
        description="Recorded with the investigation; not translated in this version.",
    )


class UrlSource(BaseModel):
    """What was fetched, and what the page said about itself.

    Persisted on the run and returned in the investigation response so a report
    can state which page it is describing. Every field is either a fact about the
    HTTP exchange or a string the page itself published.

    Attributes:
        submitted_url: The URL exactly as the caller wrote it.
        normalized_url: The same URL after scheme/host lowercasing and default
            port removal. Two spellings of one address share an id.
        final_url: The URL actually fetched, after redirects. Equal to
            `normalized_url` when no redirect occurred.
        hostname: The host that was contacted, lowercased, without port.
        resolved_addresses: Every address the hostname resolved to. Recorded so
            a reader can see which host was actually contacted; never used to
            make a judgement about the host.
        redirected: Whether the fetch followed at least one redirect.
        redirect_count: How many it followed.
        page_title: The document title, as the page published it.
        meta_description: The ``<meta name="description">`` content, if present.
        http_status: Final HTTP status code.
        content_type: Response media type, without parameters.
        is_https: Whether the final URL used TLS. A transport fact, not a trust
            judgement.
        charset: Encoding the page declared, when it declared one.
        byte_size: Bytes actually read from the response body.
        fetched_at: When the fetch completed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    submitted_url: str
    normalized_url: str
    final_url: str
    hostname: str
    resolved_addresses: tuple[str, ...] = ()
    redirected: bool = False
    redirect_count: int = 0
    page_title: str | None = None
    meta_description: str | None = None
    http_status: int
    content_type: str | None = None
    is_https: bool = False
    charset: str | None = None
    byte_size: int = 0
    fetched_at: datetime

    def as_metadata(self) -> dict[str, object]:
        """Render as JSON-safe primitives for the persistence layer.

        The run stores this as a JSON blob rather than a set of columns, because
        it is descriptive metadata about one input modality: a text investigation
        has none of it, and adding a nullable column per field for a single
        modality would spread URL concerns across the core schema.
        """
        payload = self.model_dump(mode="json")
        payload["resolved_addresses"] = list(self.resolved_addresses)
        return payload


class WebsiteDocument(BaseModel):
    """The result of turning a fetched page into investigation text.

    Attributes:
        source: The fetch record the text came from.
        title: Page title, or an empty string.
        text: Normalised visible text, ready for Phase 2 extraction.
        truncated: Whether `text` was cut short against a character budget. A
            truncated page produces a recorded limitation so the pipeline never
            reports analysing content it did not see.
        truncated_at: The character offset the text was cut at.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: UrlSource
    title: str = ""
    text: str = ""
    truncated: bool = False
    truncated_at: int | None = None