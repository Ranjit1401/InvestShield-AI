"""Verification service (Phase 4, §6).

The application-facing entry point for claim verification:

```
Claim + Entity ─┬─► build_target ──────────► VerificationTarget
                │        (no network)
                │
                └─► SearchService.search() × MAX_QUERIES_PER_CLAIM
                             │
                             ▼
                      decide_verification()
                             │
                             ▼
                    VerificationResult / Response
```

Everything downstream of the target is a pure function of the search responses,
so this class owns only orchestration: issuing the bounded set of queries,
tolerating a provider that fails, and collecting results for a whole extraction.

Two properties are structural here:

* **The search boundary is Phase 3's.** This service calls
  `SearchService.search()` and never constructs a provider, a URL or a query
  payload. SerpAPI's wire format stops in Phase 3 (D-017).
* **A failed search never becomes a verdict.** A provider that is absent,
  errors or returns nothing produces `INSUFFICIENT_EVIDENCE` or `UNVERIFIED`
  with the reason stated — never `CONTRADICTED`, and never a fabricated source
  (D-006, D-009).
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.schemas.claims import Claim
from app.schemas.entities import Entity
from app.schemas.extraction import ExtractionResult
from app.schemas.search import (
    SEARCH_SERVICE_ERROR,
    SearchResponse,
    SearchStatus,
)
from app.schemas.verification import (
    SEARCH_FAILED,
    SEARCH_UNAVAILABLE,
    VerificationResponse,
    VerificationResult,
)
from app.services.search import SearchService, build_search_service
from app.services.verification.decision_engine import decide_verification
from app.services.verification.query_builder import MAX_QUERIES_PER_CLAIM
from app.services.verification.target import build_target

logger = get_logger(__name__)

#: Results requested per query. Verification reads a small number of documents
#: and cannot responsibly interpret an unbounded page; Phase 3 clamps this
#: further if configuration asks for fewer.
DEFAULT_RESULTS_PER_QUERY = 5


class VerificationService:
    """Verifies claims against external authoritative sources.

    Args:
        settings: Optional settings override.
        search_service: Optional `SearchService` override. Tests inject a
            service backed by a fake provider here; nothing else in the
            codebase constructs a provider.
        results_per_query: Results requested for each targeted query.

    Example:
        >>> service = VerificationService()  # doctest: +SKIP
        >>> service.verify_claims(extraction.claims, extraction.entities)  # doctest: +SKIP
    """

    def __init__(
        self,
        settings: Settings | None = None,
        search_service: SearchService | None = None,
        results_per_query: int = DEFAULT_RESULTS_PER_QUERY,
    ) -> None:
        self._settings = settings or get_settings()
        self._search = search_service or build_search_service(self._settings)
        self._results_per_query = max(1, int(results_per_query))

    @property
    def search_service(self) -> SearchService:
        """The Phase 3 search facade this service depends on."""
        return self._search

    @property
    def available(self) -> bool:
        """Whether any external search can be attempted right now.

        False on a machine without `SERPAPI_KEY`. Every claim then receives
        `INSUFFICIENT_EVIDENCE` / `SEARCH_UNAVAILABLE`, which is an honest
        limitation rather than a finding about the claim (D-009).
        """
        return bool(self._search.available)

    def verify_claim(
        self, claim: Claim, entities: tuple[Entity, ...] = ()
    ) -> VerificationResult:
        """Verify one claim.

        Args:
            claim: The claim to verify.
            entities: All entities from the extraction, used to resolve
                `claim.entity_ids`.

        Returns:
            A `VerificationResult`. Never raises: a provider failure degrades to
            a stated limitation.
        """
        target = build_target(claim, entities)

        if not target.is_applicable:
            logger.debug(
                "Verification not applicable to claim",
                extra={"claim_id": claim.id, "claim_type": claim.claim_type.value},
            )
            return decide_verification(target, ())

        responses: list[SearchResponse] = []
        for query in target.search_queries[:MAX_QUERIES_PER_CLAIM]:
            responses.append(self._run_search(query, claim.id))

        return decide_verification(target, tuple(responses))

    def verify_claims(
        self,
        claims: tuple[Claim, ...],
        entities: tuple[Entity, ...] = (),
    ) -> VerificationResponse:
        """Verify a batch of claims.

        Args:
            claims: Claims to verify, in source order.
            entities: All entities from the extraction.

        Returns:
            A `VerificationResponse` with one result per claim, in input order,
            plus batch-level warnings. Counts are recomputed by the schema and
            can never disagree with the results.
        """
        results = tuple(
            self.verify_claim(claim, entities) for claim in claims
        )
        return VerificationResponse(
            results=results,
            warnings=self._batch_warnings(results),
        )

    def verify_extraction(self, extraction: ExtractionResult) -> VerificationResponse:
        """Verify every claim of an extraction.

        Args:
            extraction: The Phase 2 extraction output.

        Returns:
            A `VerificationResponse` covering every claim in the extraction.
        """
        return self.verify_claims(extraction.claims, extraction.entities)

    # -- internals ------------------------------------------------------

    def _run_search(self, query: str, claim_id: str) -> SearchResponse:
        """Run one targeted search, converting an unexpected error into a response.

        `SearchService` documents that it never raises. A provider that violates
        that contract must still not fail an investigation, so the violation is
        converted into an explicit `ERROR` response here rather than propagated
        (D-009).

        Args:
            query: The targeted query.
            claim_id: Claim the search belongs to, for logs only.

        Returns:
            A `SearchResponse`; on failure, one carrying a stable error code.
        """
        try:
            return self._search.search(query, self._results_per_query)
        except Exception as exc:  # pragma: no cover - defensive contract guard
            logger.warning(
                "Search provider violated its no-raise contract; degrading the claim",
                extra={"claim_id": claim_id, "error_type": type(exc).__name__},
            )
            return SearchResponse(
                query=query,
                results=(),
                provider="unknown",
                status=SearchStatus.ERROR,
                warnings=(
                    "An internal search failure prevented this search from "
                    "completing. No conclusion about the claim can be drawn from it.",
                ),
                error_code=SEARCH_SERVICE_ERROR,
                provider_query_sent=True,
            )

    @staticmethod
    def _batch_warnings(results: tuple[VerificationResult, ...]) -> tuple[str, ...]:
        """Summarise degraded coverage across a batch.

        Args:
            results: The per-claim results.

        Returns:
            Batch-level warnings, deduplicated and capped. Empty when every claim
            was assessed normally.
        """
        if not results:
            return ()

        reason_codes = {result.reason_code for result in results}

        warnings: list[str] = []
        if SEARCH_UNAVAILABLE in reason_codes:
            warnings.append(
                "External search was unavailable for at least one claim, so no "
                "claim in this batch was checked against external sources."
            )
        if SEARCH_FAILED in reason_codes:
            warnings.append(
                "One or more searches failed during this batch; claim coverage is "
                "partial."
            )
        return tuple(warnings)


def build_verification_service(
    settings: Settings | None = None,
    search_service: SearchService | None = None,
) -> VerificationService:
    """Construct a `VerificationService` from configuration.

    Args:
        settings: Optional settings override; cached settings when omitted.
        search_service: Optional search service override, used by tests.

    Returns:
        A configured :class:`VerificationService`.
    """
    resolved = settings or get_settings()
    return VerificationService(settings=resolved, search_service=search_service)


__all__ = [
    "DEFAULT_RESULTS_PER_QUERY",
    "VerificationService",
    "build_verification_service",
]