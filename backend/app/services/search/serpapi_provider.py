"""SerpAPI search provider (Phase 3, §4/§5/§6).

The only module in InvestShield that speaks SerpAPI's wire format. Its whole job
is to turn a provider response into `SearchResult` objects so that nothing
SerpAPI-shaped — `organic_results`, `<b>` tags in titles, provider error strings
— can leak into the pipeline.

Secrets: the API key is read from settings, sent as a request parameter (the
form SerpAPI documents) and never logged. Logging deliberately records only the
provider name, HTTP status and duration — never the URL, the query parameters or
the response body, because all three can carry the key or the user's query.

Failure handling maps every failure mode onto a stable code:

| Condition | Code |
| --- | --- |
| No key configured | `SEARCH_NOT_CONFIGURED` (handled before any request) |
| `httpx` timeout | `SEARCH_TIMEOUT` |
| HTTP 401 / 403 | `SEARCH_AUTH_ERROR` |
| HTTP 429 | `SEARCH_RATE_LIMITED` |
| Other HTTP >= 400, transport error | `SEARCH_SERVICE_ERROR` |
| Body is not JSON, or JSON is not an object | `SEARCH_INVALID_RESPONSE` |
| Body carries an `error` field | `SEARCH_AUTH_ERROR` or `SEARCH_SERVICE_ERROR` |

A provider failure is never converted into "zero results", because those mean
opposite things downstream.
"""

from __future__ import annotations

import html
import re
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.schemas.search import (
    SEARCH_AUTH_ERROR,
    SEARCH_INVALID_RESPONSE,
    SEARCH_RATE_LIMITED,
    SEARCH_SERVICE_ERROR,
    SEARCH_TIMEOUT,
    SearchResponse,
    SearchResult,
    SearchStatus,
)
from app.services.search.base import SearchProvider

logger = get_logger(__name__)

#: Search backend requested from SerpAPI. Google is the general-web engine; a
#: later provider or a site: targeted engine can change this by configuration.
DEFAULT_ENGINE = "google"

#: SerpAPI returns at most 100 organic results per page. We ask for slightly more
#: than we intend to keep so that de-duplication has something to work with.
_MAX_NUM = 100

#: Markup SerpAPI leaves in titles/snippets. Stripped deterministically so that
#: a search-term highlight never reaches a report as literal text.
_TAG = re.compile(r"<[^>]+>")

#: Whitespace left behind after tag removal.
_COLLAPSE = re.compile(r"\s+")


def clean_text(value: Any) -> str | None:
    """Strip markup from a provider string and collapse its whitespace.

    Args:
        value: Raw value from the provider; may be `None` or non-string.

    Returns:
        Cleaned text, or `None` when nothing usable remains.
    """
    if not isinstance(value, str):
        return None
    without_tags = _TAG.sub(" ", value)
    collapsed = _COLLAPSE.sub(" ", html.unescape(without_tags)).strip()
    return collapsed or None


class SerpAPIProvider(SearchProvider):
    """SerpAPI over its public HTTP search API.

    Args:
        settings: Application settings supplying the key, base URL, timeout and
            result cap.
    """

    name = "serpapi"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def is_available(self) -> bool:
        """Return True when a SerpAPI key is configured."""
        return bool(self._settings.serpapi_key.strip())

    def search(self, query: str, max_results: int) -> SearchResponse:
        """Query SerpAPI and return normalized results.

        Args:
            query: Normalized, non-empty query.
            max_results: Upper bound on results to keep, pre-clamped by
                `SearchService`.

        Returns:
            A :class:`SearchResponse`. Never raises.
        """
        if not self.is_available():
            # Defensive: SearchService normally short-circuits first.
            from app.services.search.base import NullSearchProvider

            return NullSearchProvider(provider_name=self.name).search(query, max_results)

        requested = max(1, min(int(max_results), _MAX_NUM))
        params = {
            "engine": self._settings.serpapi_engine.strip() or DEFAULT_ENGINE,
            "q": query,
            "api_key": self._settings.serpapi_key,
            "num": requested,
        }

        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self._settings.search_timeout_seconds) as client:
                response = client.get(self._settings.serpapi_base_url, params=params)
        except httpx.TimeoutException:
            logger.warning(
                "Search request timed out", extra={"provider": self.name}
            )
            return self._failure(query, SEARCH_TIMEOUT, "The search provider did not respond in time.")
        except httpx.HTTPError as exc:
            # The exception text may embed the request URL, which carries the
            # API key, so only the type name is ever recorded or returned.
            logger.warning(
                "Search request failed",
                extra={"provider": self.name, "error_type": type(exc).__name__},
            )
            return self._failure(
                query, SEARCH_SERVICE_ERROR, "The search provider could not be reached."
            )

        duration_ms = int((time.perf_counter() - started) * 1000)

        status_code = response.status_code
        if status_code == 401 or status_code == 403:
            logger.warning(
                "Search provider rejected the request",
                extra={"provider": self.name, "status": status_code},
            )
            return self._failure(
                query,
                SEARCH_AUTH_ERROR,
                "The configured search credentials were rejected by the provider.",
                duration_ms=duration_ms,
            )
        if status_code == 429:
            logger.warning(
                "Search provider rate limited", extra={"provider": self.name}
            )
            return self._failure(
                query,
                SEARCH_RATE_LIMITED,
                "The search provider rate limit was reached. No results were retrieved.",
                duration_ms=duration_ms,
            )
        if status_code >= 400:
            logger.warning(
                "Search provider returned an error status",
                extra={"provider": self.name, "status": status_code},
            )
            return self._failure(
                query,
                SEARCH_SERVICE_ERROR,
                "The search provider returned an error.",
                duration_ms=duration_ms,
            )

        try:
            body = response.json()
        except ValueError:
            logger.warning("Search provider returned non-JSON", extra={"provider": self.name})
            return self._failure(
                query,
                SEARCH_INVALID_RESPONSE,
                "The search provider returned an unreadable response.",
                duration_ms=duration_ms,
            )

        if not isinstance(body, dict):
            return self._failure(
                query,
                SEARCH_INVALID_RESPONSE,
                "The search provider returned an unexpected response shape.",
                duration_ms=duration_ms,
            )

        provider_error = self._read_provider_error(body)
        if provider_error:
            code = (
                SEARCH_AUTH_ERROR
                if provider_error in ("invalid api key", "invalid_api_key", "unauthorized")
                else SEARCH_SERVICE_ERROR
            )
            logger.warning(
                "Search provider reported an error",
                extra={"provider": self.name, "error_kind": code},
            )
            return self._failure(
                query,
                code,
                "The search provider rejected this request.",
                duration_ms=duration_ms,
            )

        organic = body.get("organic_results")
        if organic is None:
            organic = []
        if not isinstance(organic, list):
            return self._failure(
                query,
                SEARCH_INVALID_RESPONSE,
                "The search provider returned an unexpected results structure.",
                duration_ms=duration_ms,
            )

        results, skipped = self._normalize_results(organic)
        warnings: list[str] = []
        if skipped:
            warnings.append(
                f"{skipped} search result(s) were unusable and were skipped; "
                "this does not indicate anything about the remaining results."
            )

        return SearchResponse(
            query=query,
            results=tuple(results[:max_results]),
            provider=self.name,
            status=SearchStatus.OK,
            warnings=tuple(warnings),
            provider_query_sent=True,
        )

    @staticmethod
    def _read_provider_error(body: dict[str, Any]) -> str | None:
        """Return the provider's error marker, if it sent one.

        SerpAPI reports some failures with HTTP 200 and an ``error`` field, so a
        status check alone would read as a successful empty search.

        Args:
            body: Decoded response body.

        Returns:
            The error string, or ``None``.
        """
        error = body.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip().lower()
        return None

    @staticmethod
    def _normalize_results(organic: list[Any]) -> tuple[list[SearchResult], int]:
        """Convert raw provider entries into `SearchResult` objects.

        One malformed entry must never discard the whole search, so each item is
        validated independently and skipped entries are counted for the caller
        to report.

        Args:
            organic: The provider's organic results list.

        Returns:
            ``(results, skipped_count)``.
        """
        results: list[SearchResult] = []
        skipped = 0
        for position, entry in enumerate(organic, start=1):
            if not isinstance(entry, dict):
                skipped += 1
                continue

            url = entry.get("link") or entry.get("url")
            if not isinstance(url, str):
                skipped += 1
                continue

            title = clean_text(entry.get("title"))
            if title is None:
                skipped += 1
                continue

            try:
                results.append(
                    SearchResult(
                        title=title,
                        url=url,
                        snippet=clean_text(entry.get("snippet")),
                        position=position,
                    )
                )
            except ValueError:
                # Unusable scheme or malformed URL: skip just this entry.
                skipped += 1

        return results, skipped

    def _failure(
        self,
        query: str,
        code: str,
        message: str,
        duration_ms: int | None = None,
    ) -> SearchResponse:
        """Build an error response that cannot leak credentials.

        Args:
            query: The normalized query, echoed back.
            code: Stable error code.
            message: Human-readable, credential-free explanation.
            duration_ms: Unused by the response model but accepted for symmetry
                with future instrumentation.

        Returns:
            A `SearchResponse` with `status=ERROR`.
        """
        return SearchResponse(
            query=query,
            results=(),
            provider=self.name,
            status=SearchStatus.ERROR,
            warnings=(message,),
            error_code=code,
            provider_query_sent=True,
        )


__all__ = ["DEFAULT_ENGINE", "SerpAPIProvider", "clean_text"]