"""Search provider abstraction (Phase 3, §3).

Every outbound web search in InvestShield goes through :class:`SearchProvider`.
No service may know that SerpAPI exists, and no service may build a search URL.
That is the same rule the LLM side follows (D-004), and it exists for the same
reason: the provider is the most likely thing to be replaced, rate-limited or
paid for, and it must never reach into the pipeline's logic.

A new backend — Bing, Brave, DuckDuckGo, a local corpus, a recorded fixture for
regression tests — is added by implementing `search()` and registering a name.
No consumer changes.

Implementations must **never raise**. A timeout, a rate limit, a rejected key and
a malformed body all return a `SearchResponse` carrying a stable error code, so
an unavailable search degrades coverage instead of failing an investigation
(D-009).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas.search import (
    SEARCH_NOT_CONFIGURED,
    SearchResponse,
    SearchResult,
    SearchStatus,
)


class SearchProvider(ABC):
    """Contract every search backend implements."""

    #: Short provider name recorded in `SearchResponse.provider`, e.g. ``serpapi``.
    name: str = "unknown"

    @abstractmethod
    def is_available(self) -> bool:
        """Return True when this provider has everything it needs to run.

        Implementations must answer from configuration alone, without making a
        network call: this is consulted on every search to decide whether to
        degrade before any quota is spent.
        """

    @abstractmethod
    def search(self, query: str, max_results: int) -> SearchResponse:
        """Execute `query` and return a normalized response.

        Args:
            query: An already-normalized, non-empty query.
            max_results: Upper bound on returned results, pre-clamped by
                `SearchService` to the configured maximum.

        Returns:
            A :class:`SearchResponse`. Never raises; failures become a
            `SearchResponse` with `status=ERROR` and an error code.
        """


class NullSearchProvider(SearchProvider):
    """Provider used when no search backend is configured.

    It reports unavailability rather than returning an empty successful search.
    That distinction is the whole point: "we found nothing" and "we could not
    look" lead to different, honest conclusions later, and only the first one is
    a statement about the world.

    Args:
        reason: Error code recorded on every response.
        provider_name: Provider name to report, so the configured-but-missing
            backend is still named in logs and reports.
    """

    name = "null"

    def __init__(
        self,
        reason: str = SEARCH_NOT_CONFIGURED,
        provider_name: str | None = None,
    ) -> None:
        self._reason = reason
        if provider_name:
            self.name = provider_name

    def is_available(self) -> bool:
        """Always False: this provider can never run."""
        return False

    def search(self, query: str, max_results: int) -> SearchResponse:
        """Return an explicit unavailable response without contacting anything.

        Args:
            query: The normalized query, echoed back for traceability.
            max_results: Ignored.

        Returns:
            A `SearchResponse` with `status=UNAVAILABLE`, no results, and a
            warning naming the missing configuration.
        """
        from app.schemas.search import SEARCH_NOT_CONFIGURED as _not_configured

        reason = self._reason or _not_configured
        detail = (
            "External web search is not configured, so no search was performed. "
            "No conclusion about any claim can be drawn from this."
        )
        return SearchResponse(
            query=query,
            results=(),
            provider=self.name,
            status=SearchStatus.UNAVAILABLE,
            warnings=(detail,),
            error_code=reason,
            provider_query_sent=False,
        )


def results_or_empty(response: SearchResponse) -> tuple[SearchResult, ...]:
    """Return the response's results, or an empty tuple.

    A convenience for consumers that must degrade rather than branch on status.

    Args:
        response: Any search response.

    Returns:
        The results tuple, empty on failure.
    """
    return response.results if response.success else ()


__all__ = ["NullSearchProvider", "SearchProvider", "results_or_empty"]