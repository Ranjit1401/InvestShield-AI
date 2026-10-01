"""External search infrastructure (Phase 3).

Public surface for the search layer. Consumers import from here, never from a
concrete provider module:

    from app.services.search import SearchService, build_search_service

Layering:

    consumer -> SearchService -> SearchProvider -> SerpAPI -> SearchResult

`SearchService` holds policy (query normalization, result limits, source
classification, de-duplication). `SearchProvider` implementations hold transport.
SerpAPI's wire format stops at `serpapi_provider.py`.
"""

from __future__ import annotations

from app.schemas.search import (
    SEARCH_AUTH_ERROR,
    SEARCH_ERROR_CODES,
    SEARCH_INVALID_QUERY,
    SEARCH_INVALID_RESPONSE,
    SEARCH_NOT_CONFIGURED,
    SEARCH_RATE_LIMITED,
    SEARCH_SERVICE_ERROR,
    SEARCH_TIMEOUT,
    SOURCE_PRIORITY,
    SearchResponse,
    SearchResult,
    SearchStatus,
    SourceType,
)
from app.services.search.base import NullSearchProvider, SearchProvider, results_or_empty
from app.services.search.domain import canonicalize_url, extract_domain
from app.services.search.query import MAX_QUERY_LENGTH, is_meaningful_query, normalize_query
from app.services.search.search_service import (
    DEFAULT_MAX_RESULTS,
    HARD_MAX_RESULTS,
    SearchService,
    build_search_service,
)
from app.services.search.source_classifier import (
    EXCHANGE_DOMAINS,
    GOVERNMENT_SUFFIXES,
    REGULATOR_DOMAINS,
    classify_domain,
    classify_url,
)

__all__ = [
    "DEFAULT_MAX_RESULTS",
    "EXCHANGE_DOMAINS",
    "GOVERNMENT_SUFFIXES",
    "HARD_MAX_RESULTS",
    "MAX_QUERY_LENGTH",
    "NullSearchProvider",
    "REGULATOR_DOMAINS",
    "SEARCH_AUTH_ERROR",
    "SEARCH_ERROR_CODES",
    "SEARCH_INVALID_QUERY",
    "SEARCH_INVALID_RESPONSE",
    "SEARCH_NOT_CONFIGURED",
    "SEARCH_RATE_LIMITED",
    "SEARCH_SERVICE_ERROR",
    "SEARCH_TIMEOUT",
    "SOURCE_PRIORITY",
    "SearchProvider",
    "SearchResponse",
    "SearchResult",
    "SearchService",
    "SearchStatus",
    "SourceType",
    "build_search_service",
    "canonicalize_url",
    "classify_domain",
    "classify_url",
    "extract_domain",
    "is_meaningful_query",
    "normalize_query",
    "results_or_empty",
]