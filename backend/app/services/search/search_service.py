"""Search service orchestration (Phase 3, §11).

`SearchService` is the application-facing entry point. Consumers depend on it,
never on `SerpAPIProvider`:

```
Claim / Entity
      ↓
SearchService          ← normalizes, validates, classifies, de-duplicates
      ↓
SearchProvider         ← abstraction; SerpAPI today, others later
      ↓
Normalized SearchResponse
```

Transport concerns belong to the provider. **Policy** belongs here: what a valid
query is, how many results may be requested, which sources are authoritative
enough to be checked first, and how the three failure states are worded.

Phase 3 answers only "can we retrieve and normalize external information?".
It does not read the results for meaning, does not score evidence, and does not
say whether any claim is true — that is Phase 4 (D-017).
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.schemas.search import (
    SEARCH_INVALID_QUERY,
    SEARCH_NOT_CONFIGURED,
    SearchResponse,
    SearchResult,
    SearchStatus,
)
from app.services.search.base import NullSearchProvider, SearchProvider
from app.services.search.domain import canonicalize_url, extract_domain
from app.services.search.query import is_meaningful_query, normalize_query
from app.services.search.source_classifier import classify_domain

logger = get_logger(__name__)

#: Absolute ceiling on results per search, regardless of configuration. Bounds
#: the request a caller can make, so a bug cannot ask a paid provider for an
#: unbounded page.
HARD_MAX_RESULTS = 50

#: Default number of results when a caller does not specify one.
DEFAULT_MAX_RESULTS = 10


class SearchService:
    """Application-facing web-search facade.

    Args:
        settings: Optional settings override.
        provider: Optional explicit provider override. Tests inject fakes here;
            nothing else in the codebase constructs a provider.
    """

    def __init__(self, settings: Settings | None = None, provider: SearchProvider | None = None) -> None:
        self._settings = settings or get_settings()
        self._provider = provider or self._build_provider(self._settings)

    @property
    def provider_name(self) -> str:
        """Name of the provider in use."""
        return self._provider.name

    @staticmethod
    def _build_provider(settings: Settings) -> SearchProvider:
        """Return the provider named by configuration.

        An unrecognised provider name degrades to `NullSearchProvider` rather
        than raising: a typo in configuration must cost search coverage, not
        the whole investigation (D-009).
        """
        if settings.search_provider == "serpapi":
            from app.services.search.serpapi_provider import SerpAPIProvider

            return SerpAPIProvider(settings)

        logger.warning(
            "Unknown search provider configured; external search is unavailable",
            extra={"provider": settings.search_provider},
        )
        return NullSearchProvider(
            reason=SEARCH_NOT_CONFIGURED,
            provider_name=settings.search_provider,
        )

    @property
    def available(self) -> bool:
        """Whether a search can be attempted right now."""
        return bool(self._provider.is_available())

    def search(self, query: str | None, max_results: int | None = None) -> SearchResponse:
        """Run a web search and return normalized, classified results.

        Args:
            query: Raw query text. Normalized here; callers may pass a claim
                string or entity name verbatim.
            max_results: Desired result count, clamped to
                `[1, HARD_MAX_RESULTS]` and to the configured maximum.

        Returns:
            A stable :class:`SearchResponse`. Never raises and never invents
            results: a failure is reported as `UNAVAILABLE` or `ERROR`.
        """
        normalized = normalize_query(query)

        if not is_meaningful_query(normalized):
            logger.info(
                "Search rejected: query contained no searchable content",
                extra={"query_chars": len(query or "")},
            )
            return SearchResponse(
                query=normalized,
                results=(),
                provider=self._provider.name,
                status=SearchStatus.ERROR,
                warnings=(
                    "The search query was empty or contained no searchable text, "
                    "so no search was performed.",
                ),
                error_code=SEARCH_INVALID_QUERY,
                provider_query_sent=False,
            )

        if not self._provider.is_available():
            logger.info(
                "Search skipped: provider unavailable",
                extra={"provider": self._provider.name},
            )
            return NullSearchProvider(
                reason=SEARCH_NOT_CONFIGURED,
                provider_name=self._provider.name,
            ).search(normalized, self._clamp(max_results))

        limit = self._clamp(max_results)
        response = self._provider.search(normalized, limit)

        if not response.success:
            # A failed search stays failed; policy does not dress it up.
            return response

        results = self._enrich(response.results)
        deduped, duplicates = self._deduplicate(results, limit)

        warnings = list(response.warnings)
        if duplicates:
            warnings.append(
                f"{duplicates} duplicate search result(s) were collapsed by canonical URL."
            )
        if len(response.results) >= limit:
            # A full page means the provider may have had more; saying so is
            # better than presenting a capped list as if it were everything.
            warnings.append(f"Results were limited to {limit} result(s).")

        return response.model_copy(
            update={
                "results": deduped,
                "warnings": tuple(warnings),
            }
        )

    # -- internals ------------------------------------------------------

    def _clamp(self, max_results: int | None) -> int:
        """Clamp a requested result count into the permitted range.

        Args:
            max_results: Requested count, possibly `None` or out of range.

        Returns:
            A count within `[1, min(HARD_MAX_RESULTS, configured maximum)]`.
        """
        configured = max(1, min(int(self._settings.search_max_results), HARD_MAX_RESULTS))
        if max_results is None:
            return min(DEFAULT_MAX_RESULTS, configured)
        try:
            requested = int(max_results)
        except (TypeError, ValueError):
            return min(DEFAULT_MAX_RESULTS, configured)
        if requested < 1:
            return 1
        return min(requested, configured, HARD_MAX_RESULTS)

    @staticmethod
    def _enrich(results: tuple[SearchResult, ...]) -> tuple[SearchResult, ...]:
        """Attach domain and source classification to each result.

        Args:
            results: Provider results, possibly with no classification.

        Returns:
            Results carrying `source_domain`, `source_type` and `canonical_url`.
        """
        enriched: list[SearchResult] = []
        for result in results:
            domain = result.source_domain or extract_domain(result.url) or ""
            canonical = result.canonical_url or canonicalize_url(result.url) or result.url
            enriched.append(
                result.model_copy(
                    update={
                        "source_domain": domain,
                        "canonical_url": canonical,
                        "source_type": classify_domain(domain),
                    }
                )
            )
        return tuple(enriched)

    @staticmethod
    def _deduplicate(
        results: tuple[SearchResult, ...], limit: int
    ) -> tuple[tuple[SearchResult, ...], int]:
        """Collapse results that share a canonical URL.

        Only canonical-URL equality merges entries. Titles are never used as a
        merge key: many unrelated pages share boilerplate titles, and collapsing
        them would silently discard sources from a later evidence review.

        Args:
            results: Enriched results in provider order.
            limit: Maximum results to keep.

        Returns:
            ``(kept, duplicates_collapsed)``, where kept preserves the earliest
            position of each canonical URL.
        """
        seen: set[str] = set()
        kept: list[SearchResult] = []
        duplicates = 0
        for result in results:
            key = result.canonical_url or result.url
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            kept.append(result)
            if len(kept) >= limit:
                break
        return tuple(kept), duplicates


def build_search_service(
    settings: Settings | None = None, provider: SearchProvider | None = None
) -> SearchService:
    """Construct a `SearchService` from configuration.

    Args:
        settings: Optional settings override; cached settings when omitted.
        provider: Optional provider override, used by tests.

    Returns:
        A configured :class:`SearchService`. An unknown configured provider
        name yields a service backed by `NullSearchProvider`, which degrades
        explicitly instead of failing at call time.
    """
    resolved = settings or get_settings()
    return SearchService(settings=resolved, provider=provider)


__all__ = [
    "DEFAULT_MAX_RESULTS",
    "HARD_MAX_RESULTS",
    "SearchService",
    "build_search_service",
]