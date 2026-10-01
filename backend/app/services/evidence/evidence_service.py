"""Evidence service (Phase 5, §8/§9/§11/§17).

The application-facing entry point for the evidence stage:

```
Claim + Entity ─┐
Verification ───┼─► EvidenceService
Phase 3 Results ─┘
                       │
                       ▼
              EvidenceResponse
              (evidence + sources + counts + warnings)
```

Everything the service does is derivable: it assembles Phase 3 results and
Phase 4 decisions into traceable objects. It makes no network call, consults no
provider, and holds no opinion about any claim.

Three rules are structural here rather than merely documented:

- **No inputs, no evidence.** A claim whose search never ran, or never
  succeeded, yields an empty bundle plus a warning naming the reason. There is
  no placeholder item, no synthesised "no evidence found" document, and no
  evidence at all for a `NOT_APPLICABLE` claim (D-023).
- **The claim's own phase 3 results are used.** If the caller supplies results
  whose canonical URLs were not among the results the verification actually saw,
  they are dropped with a warning. A Phase 5 stage that accepted arbitrary
  documents would make "no fabricated evidence" unenforceable (D-023).
- **Phase 4 decides.** Relationships come from Phase 4's `assess_results()`;
  the status is copied, never recomputed. Evidence explains a verification
  result, and cannot contradict it (D-020, D-021).
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.schemas.claims import Claim
from app.schemas.entities import Entity
from app.schemas.evidence import EvidenceBundleResponse, EvidenceItem, EvidenceResponse
from app.schemas.search import SearchResult
from app.schemas.verification import (
    NOT_A_FACTUAL_CLAIM,
    SEARCH_FAILED,
    SEARCH_UNAVAILABLE,
    VerificationResponse,
    VerificationResult,
    VerificationStatus,
)
from app.services.evidence.evidence_builder import (
    build_evidence_item,
    group_by_query,
    status_supports_evidence,
)
from app.services.evidence.evidence_dedupe import (
    dedupe_evidence,
    distinct_sources,
    order_evidence,
)
from app.services.evidence.source_normalizer import canonical_url_for
from app.services.verification.comparator import (
    assess_results,
    result_id_for,
    source_id_for,
)
from app.services.verification.target import VerificationTarget, build_target

logger = get_logger(__name__)

#: Reason codes for which no evidence can exist, and the warning each produces.
#: The wording is fixed and factual: it says what did not happen, never what it
#: means for the claim.
NO_EVIDENCE_WARNINGS: dict[str, str] = {
    SEARCH_UNAVAILABLE: (
        "External search was unavailable for this claim, so no source was "
        "retrieved and no evidence could be assembled."
    ),
    SEARCH_FAILED: (
        "The search for this claim did not complete, so no source was "
        "retrieved and no evidence could be assembled."
    ),
    "NO_QUERY_BUILT": (
        "No search was issued for this claim, so no evidence could be assembled."
    ),
    "ZERO_RESULTS": (
        "The authoritative records searched returned no entry for this claim, "
        "so there is no evidence to show. This is an absence of confirmation, "
        "not a finding that the claim is false."
    ),
    "IDENTITY_NOT_FOUND": (
        "No retrieved source identified the claimed party, so there is no "
        "evidence to show."
    ),
    "NOT_A_FACTUAL_CLAIM": (
        "This statement is not externally verifiable, so no search was issued "
        "and no evidence exists for it."
    ),
}

#: Generic warning when a claim is simply missing from the supplied results.
NO_RESULTS_WARNING = (
    "No search results were supplied for this claim, so no evidence could be "
    "assembled."
)


class EvidenceService:
    """Assembles traceable evidence for verified claims.

    The service holds no state and performs no I/O. Constructing one is free;
    the same instance can be reused for a whole investigation.

    Example:
        >>> service = EvidenceService()
        >>> bundle = service.build_claim(claim, verification, results, entities)  # doctest: +SKIP
    """

    def build_claim(
        self,
        claim: Claim,
        verification: VerificationResult,
        search_results: tuple[SearchResult, ...] = (),
        entities: tuple[Entity, ...] = (),
        *,
        responses: tuple[object, ...] = (),
        target: VerificationTarget | None = None,
    ) -> EvidenceResponse:
        """Assemble the evidence for one claim.

        Args:
            claim: The claim. Its `id` must match `verification.claim_id`.
            verification: The claim's Phase 4 `VerificationResult`.
            search_results: The Phase 3 `SearchResult` objects produced for this
                claim.
            entities: All entities from the extraction, used to resolve the
                claim's identity parties for Phase 4 comparison.
            responses: Optional Phase 3 `SearchResponse` objects, used only to
                record which query surfaced which document.
            target: Optional prebuilt `VerificationTarget`. Supplied when the
                caller already built one during verification, so Phase 4's
                comparison is not repeated.

        Returns:
            An `EvidenceResponse`. Its `evidence` is empty — never a placeholder
            — whenever nothing was genuinely retrieved.

        Raises:
            ValueError: If `claim.id` and `verification.claim_id` disagree, since
                every evidence item is anchored to a claim and a mismatch would
                make the whole bundle unattributable.
        """
        if claim.id != verification.claim_id:
            raise ValueError(
                f"claim {claim.id!r} does not match verification result for "
                f"{verification.claim_id!r}"
            )

        warnings: list[str] = []

        if not status_supports_evidence(verification.status):
            return self._empty(
                verification,
                self._warning_for(verification.reason_code, NOT_A_FACTUAL_CLAIM),
            )

        if not search_results:
            return self._empty(verification, self._warning_for(verification.reason_code, None))

        resolved_target = target or build_target(claim, entities)
        seen_results = self._restrict_to_seen_results(
            verification, search_results, warnings
        )
        if not seen_results:
            warnings.append(NO_RESULTS_WARNING)
            return self._empty(verification, warnings, verification.reason_code)

        assessments = assess_results(seen_results, resolved_target)
        queries = self._query_index(seen_results, responses)

        items = tuple(
            build_evidence_item(
                result,
                assessment,
                verification,
                claim_type=claim.claim_type,
                provider_query=queries.get(canonical_url_for(result)),
            )
            for result, assessment in zip(seen_results, assessments, strict=True)
        )

        ordered = order_evidence(dedupe_evidence(items))
        warnings.extend(self._coverage_warnings(verification, ordered))

        return EvidenceResponse(
            claim_id=verification.claim_id,
            verification_status=verification.status,
            verification_reason_code=verification.reason_code,
            evidence=ordered,
            sources=distinct_sources(ordered),
            warnings=self._dedupe(warnings),
        )

    def build_all(
        self,
        claims: tuple[Claim, ...],
        verification: VerificationResponse,
        results_by_claim: dict[str, tuple[SearchResult, ...]] | None = None,
        entities: tuple[Entity, ...] = (),
        *,
        responses: tuple[object, ...] = (),
    ) -> EvidenceBundleResponse:
        """Assemble evidence for every verified claim in a batch.

        Args:
            claims: The claims, in input order.
            verification: The batch's Phase 4 `VerificationResponse`.
            results_by_claim: Phase 3 results per claim id. A claim absent from
                the mapping gets an empty bundle with a warning.
            entities: All entities from the extraction.
            responses: Optional Phase 3 responses, for query provenance.

        Returns:
            An `EvidenceBundleResponse` with one response per claim in the same
            order as `verification.results`.
        """
        results_by_claim = results_by_claim or {}
        by_id = {claim.id: claim for claim in claims}

        responses_built: list[EvidenceResponse] = []
        for result in verification.results:
            claim = by_id.get(result.claim_id)
            if claim is None:
                logger.debug(
                    "Skipping evidence for a claim with no matching Claim object",
                    extra={"claim_id": result.claim_id},
                )
                responses_built.append(
                    self._empty(
                        result,
                        "The claim object for this verification result was not "
                        "supplied, so no evidence could be assembled.",
                    )
                )
                continue
            responses_built.append(
                self.build_claim(
                    claim,
                    result,
                    results_by_claim.get(result.claim_id, ()),
                    entities,
                    responses=responses,
                )
            )

        return EvidenceBundleResponse(
            responses=tuple(responses_built),
            warnings=verification.warnings,
        )

    # -- internals ------------------------------------------------------

    @staticmethod
    def _empty(
        verification: VerificationResult,
        warnings: str | list[str] | tuple[str, ...],
        reason_code: str | None = None,
    ) -> EvidenceResponse:
        """Build an empty, honestly-labelled bundle.

        Args:
            verification: The claim's Phase 4 result.
            warning: The warning, or warnings, to record.
            reason_code: Override for the carried reason code.

        Returns:
            An `EvidenceResponse` with no evidence and no sources.
        """
        collected = (warnings,) if isinstance(warnings, str) else tuple(warnings)
        return EvidenceResponse(
            claim_id=verification.claim_id,
            verification_status=verification.status,
            verification_reason_code=reason_code or verification.reason_code,
            evidence=(),
            sources=(),
            warnings=EvidenceService._dedupe(list(collected)),
        )

    @staticmethod
    def _warning_for(reason_code: str, default: str | None) -> str:
        """Return the fixed warning for a reason code, or a generic one."""
        return NO_EVIDENCE_WARNINGS.get(reason_code, default or NO_RESULTS_WARNING)

    @staticmethod
    def _restrict_to_seen_results(
        verification: VerificationResult,
        search_results: tuple[SearchResult, ...],
        warnings: list[str],
    ) -> tuple[SearchResult, ...]:
        """Keep only results the verification result can account for.

        Phase 4 records the ``res_`` ids it examined. Anything outside that set
        was not part of the evidence base for this claim, so accepting it would
        let a caller attach a document the verification never saw — and Phase 5
        has no way to know whether such a document was real.

        Args:
            verification: The claim's Phase 4 result.
            search_results: Results supplied by the caller.
            warnings: Warning list to append to when results are dropped.

        Returns:
            The results Phase 4's assessment could have covered.
        """
        seen = set(verification.matched_result_ids) | set(verification.source_ids)
        if not seen:
            # No ids were recorded, which happens for the reason codes that
            # never produced results. Nothing can be cross-checked, so the
            # supplied results are taken as given.
            return search_results

        def was_seen(result: SearchResult) -> bool:
            """Whether Phase 4 recorded this document, as either id family."""
            canonical = canonical_url_for(result)
            return result_id_for(canonical) in seen or source_id_for(canonical) in seen

        kept = tuple(result for result in search_results if was_seen(result))
        dropped = len(search_results) - len(kept)
        if dropped:
            warnings.append(
                f"{dropped} supplied search result(s) were not among the "
                "results this claim's verification examined and were excluded "
                "from the evidence."
            )
        return kept

    @staticmethod
    def _query_index(
        seen_results: tuple[SearchResult, ...],
        responses: tuple[object, ...],
    ) -> dict[str, str]:
        """Map each canonical URL to the query that first surfaced it.

        Args:
            seen_results: The results in play.
            responses: Phase 3 responses.

        Returns:
            Canonical URL to query string. Only the first query per URL is
            recorded, so provenance is deterministic.
        """
        by_url: dict[str, str] = {}
        for query, results in group_by_query(responses).items():
            for result in results:
                by_url.setdefault(canonical_url_for(result), query)
        return {
            canonical_url_for(item): by_url[canonical_url_for(item)]
            for item in seen_results
            if canonical_url_for(item) in by_url
        }

    @staticmethod
    def _coverage_warnings(
        verification: VerificationResult,
        evidence: tuple[EvidenceItem, ...],
    ) -> list[str]:
        """Warn when the evidence set does not match the decision it explains.

        Args:
            verification: The claim's Phase 4 result.
            evidence: The assembled items.

        Returns:
            Warnings for any gap between what was decided and what can be shown.
        """
        warnings: list[str] = []
        supporting = [item for item in evidence if item.is_proof]

        if verification.status is VerificationStatus.CONTRADICTED and not supporting:
            warnings.append(
                "The verification result reports a conflict with an official "
                "record, but no retrieved text is available to show. The "
                "conflicting record should be reviewed directly."
            )
        if verification.status is VerificationStatus.VERIFIED and not supporting:
            warnings.append(
                "The verification result reports confirmation by an official "
                "record, but no retrieved text is available to show. The "
                "confirming record should be reviewed directly."
            )
        return warnings

    @staticmethod
    def _dedupe(warnings: list[str]) -> tuple[str, ...]:
        """De-duplicate warnings, preserving order."""
        seen: set[str] = set()
        unique: list[str] = []
        for warning in warnings:
            cleaned = " ".join(warning.split())
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            unique.append(cleaned)
        return tuple(unique)


def build_evidence_service() -> EvidenceService:
    """Construct an `EvidenceService`.

    Returns:
        A ready service. It holds no configuration, so there is nothing to
        inject; the factory exists so callers never construct the class
        directly, matching the other phases.
    """
    return EvidenceService()


__all__ = [
    "NO_EVIDENCE_WARNINGS",
    "NO_RESULTS_WARNING",
    "EvidenceService",
    "build_evidence_service",
]
