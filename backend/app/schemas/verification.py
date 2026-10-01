"""Verification schemas (Phase 4, §1–§3).

Verification answers one narrow question: *can external sources independently
establish this specific factual claim?* It answers it with one of five
controlled statuses, never with a judgement about the people involved.

| Status | Meaning |
| --- | --- |
| `VERIFIED` | An authoritative, claim-relevant source directly supports the claim for the identified entity |
| `UNVERIFIED` | Relevant searches completed; no source establishes the claim, and none disputes it |
| `CONTRADICTED` | An authoritative, claim-relevant source directly conflicts with the claim for the identified entity |
| `INSUFFICIENT_EVIDENCE` | Verification could not be completed or could not be done reliably |
| `NOT_APPLICABLE` | The statement is an instruction or opinion, not an externally verifiable factual claim |

Three rules are encoded in this module and defended by tests:

* **No verdict vocabulary.** There is no `SCAM`, `FRAUD`, `SAFE` or
  `DANGEROUS` status, and no field may be added that expresses one. A claim can
  be perfectly `VERIFIED` and still be a dangerous promise; verification reports
  factual status, risk is a separate phase (D-006, D-017, D-020).
* **`confidence` is assessment confidence.** It measures how well the available
  evidence supports the *status assigned*. It is never a probability of fraud,
  of loss, or of truth of the underlying investment.
* **Negative evidence is not contradiction.** `UNVERIFIED` and
  `INSUFFICIENT_EVIDENCE` both mean "we could not establish this"; neither means
  "this is false". Only `CONTRADICTED` may ever be read that way, and only from a
  direct authoritative conflict (D-006, D-021).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.schemas.claims import ClaimType

#: Reasons the verification service can reach. Explanations are rendered from
#: these codes, never from free-form generated prose, so the wording of a status
#: is auditable and cannot drift into an accusation (D-022).
AUTHORITATIVE_SOURCE_CONFIRMS = "AUTHORITATIVE_SOURCE_CONFIRMS"
NO_CONFIRMATION_FOUND = "NO_CONFIRMATION_FOUND"
AUTHORITATIVE_SOURCE_CONTRADICTS = "AUTHORITATIVE_SOURCE_CONTRADICTS"
CONFLICTING_AUTHORITATIVE_SOURCES = "CONFLICTING_AUTHORITATIVE_SOURCES"
SEARCH_UNAVAILABLE = "SEARCH_UNAVAILABLE"
SEARCH_FAILED = "SEARCH_FAILED"
ZERO_RESULTS = "ZERO_RESULTS"
IDENTITY_AMBIGUOUS = "IDENTITY_AMBIGUOUS"
IDENTITY_NOT_FOUND = "IDENTITY_NOT_FOUND"
NO_CLAIM_RELEVANT_SOURCE = "NO_CLAIM_RELEVANT_SOURCE"
NO_QUERY_BUILT = "NO_QUERY_BUILT"
NOT_A_FACTUAL_CLAIM = "NOT_A_FACTUAL_CLAIM"

VERIFICATION_REASON_CODES = frozenset(
    {
        AUTHORITATIVE_SOURCE_CONFIRMS,
        NO_CONFIRMATION_FOUND,
        AUTHORITATIVE_SOURCE_CONTRADICTS,
        CONFLICTING_AUTHORITATIVE_SOURCES,
        SEARCH_UNAVAILABLE,
        SEARCH_FAILED,
        ZERO_RESULTS,
        IDENTITY_AMBIGUOUS,
        IDENTITY_NOT_FOUND,
        NO_CLAIM_RELEVANT_SOURCE,
        NO_QUERY_BUILT,
        NOT_A_FACTUAL_CLAIM,
    }
)


class VerificationStatus(str, Enum):
    """Controlled verification outcomes. No verdict values exist here."""

    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class SourceTier(str, Enum):
    """How authoritative a *category of source* generally is.

    A tier is metadata about who published a document. It is not a truth label:
    a `TIER_1_PRIMARY_REGULATOR` page that says nothing about a claim never
    verifies anything, and a `TIER_6_GENERAL_WEB` page never falsifies it
    (D-006, D-020).
    """

    TIER_1_PRIMARY_REGULATOR = "TIER_1_PRIMARY_REGULATOR"
    TIER_2_GOVERNMENT = "TIER_2_GOVERNMENT"
    TIER_3_EXCHANGE = "TIER_3_EXCHANGE"
    TIER_4_OFFICIAL_ENTITY = "TIER_4_OFFICIAL_ENTITY"
    TIER_5_TRUSTED_SECONDARY = "TIER_5_TRUSTED_SECONDARY"
    TIER_6_GENERAL_WEB = "TIER_6_GENERAL_WEB"
    UNKNOWN = "UNKNOWN"


#: Ordered most to least authoritative. Used for ranking only.
TIER_PRIORITY: dict[SourceTier, int] = {
    SourceTier.TIER_1_PRIMARY_REGULATOR: 1,
    SourceTier.TIER_2_GOVERNMENT: 2,
    SourceTier.TIER_3_EXCHANGE: 3,
    SourceTier.TIER_4_OFFICIAL_ENTITY: 4,
    SourceTier.TIER_5_TRUSTED_SECONDARY: 5,
    SourceTier.TIER_6_GENERAL_WEB: 6,
    SourceTier.UNKNOWN: 7,
}

#: Tiers that may, on their own, support `VERIFIED` or `CONTRADICTED` for a
#: claim they are relevant to. Below this line a source can contribute context
#: but can never settle a claim.
AUTHORITATIVE_TIERS = frozenset(
    {
        SourceTier.TIER_1_PRIMARY_REGULATOR,
        SourceTier.TIER_2_GOVERNMENT,
        SourceTier.TIER_3_EXCHANGE,
    }
)


class IdentityMatch(str, Enum):
    """Whether a retrieved document is about the exact claimed entity."""

    MATCHED = "MATCHED"
    ABSENT = "ABSENT"
    AMBIGUOUS = "AMBIGUOUS"


class VerificationResult(BaseModel):
    """Verification outcome for exactly one claim.

    Attributes:
        claim_id: Id of the `Claim` this result describes.
        claim_type: Type of that claim, carried for traceability.
        status: The controlled outcome.
        reason: Plain-language explanation, rendered from `reason_code`.
        reason_code: Structured cause, so the wording can be audited.
        confidence: Confidence in *this assessment*, given the evidence found.
            Not a probability that the investment is a scam or will lose money.
        source_ids: Ids of the source documents that were actually relied on.
        matched_result_ids: Ids of the individual search results that addressed
            the claim. Phase 5 turns these references into formal evidence
            objects; Phase 4 only preserves them.
        queries: The targeted queries that were issued.
        warnings: Limitations that apply to this particular claim.
    """

    model_config = ConfigDict(frozen=True)

    claim_id: str = Field(min_length=1)
    claim_type: ClaimType = ClaimType.OTHER
    status: VerificationStatus
    reason: str = Field(min_length=1)
    reason_code: str
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Confidence in the verification assessment given the evidence found. "
            "This is NOT a probability that the investment is fraudulent."
        ),
    )
    source_ids: tuple[str, ...] = ()
    matched_result_ids: tuple[str, ...] = ()
    queries: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_consistency(self) -> VerificationResult:
        """Keep status, reason code and confidence mutually coherent.

        These checks exist so that no future edit can produce, say, a
        `VERIFIED` status that cites no sources or a `CONTRADICTED` status with a
        confidence implying strong proof from nothing.
        """
        if self.reason_code not in VERIFICATION_REASON_CODES:
            raise ValueError(f"unknown verification reason code: {self.reason_code}")

        if self.status is VerificationStatus.VERIFIED:
            if not self.source_ids:
                raise ValueError("a VERIFIED result must cite the sources it relied on")
            if self.reason_code != AUTHORITATIVE_SOURCE_CONFIRMS:
                raise ValueError("VERIFIED requires the AUTHORITATIVE_SOURCE_CONFIRMS reason")
        if self.status is VerificationStatus.CONTRADICTED:
            if not self.source_ids:
                raise ValueError("a CONTRADICTED result must cite the conflicting source")
            if self.reason_code not in (
                AUTHORITATIVE_SOURCE_CONTRADICTS,
                CONFLICTING_AUTHORITATIVE_SOURCES,
            ):
                raise ValueError("CONTRADICTED requires an authoritative-conflict reason")
        if self.status is VerificationStatus.NOT_APPLICABLE:
            if self.source_ids:
                raise ValueError("a NOT_APPLICABLE result cannot cite sources")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_positive(self) -> bool:
        """Whether an authoritative source established the claim."""
        return self.status is VerificationStatus.VERIFIED

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_inconclusive(self) -> bool:
        """Whether the claim could not be established either way.

        True for `UNVERIFIED`, `INSUFFICIENT_EVIDENCE` and `NOT_APPLICABLE`.
        None of these is an accusation (D-006).
        """
        return self.status in (
            VerificationStatus.UNVERIFIED,
            VerificationStatus.INSUFFICIENT_EVIDENCE,
            VerificationStatus.NOT_APPLICABLE,
        )


class VerificationResponse(BaseModel):
    """Verification outcomes for a batch of claims, with counts only.

    Attributes:
        results: One result per claim, in input order.
        warnings: Batch-level limitations, e.g. a search provider failure that
            affected every claim.
        verified_count: Number of `VERIFIED` results.
        unverified_count: Number of `UNVERIFIED` results.
        contradicted_count: Number of `CONTRADICTED` results.
        insufficient_evidence_count: Number of `INSUFFICIENT_EVIDENCE` results.
        not_applicable_count: Number of `NOT_APPLICABLE` results.
    """

    model_config = ConfigDict(frozen=True)

    results: tuple[VerificationResult, ...] = ()
    warnings: tuple[str, ...] = ()
    verified_count: int = 0
    unverified_count: int = 0
    contradicted_count: int = 0
    insufficient_evidence_count: int = 0
    not_applicable_count: int = 0

    @model_validator(mode="after")
    def _recompute_counts(self) -> VerificationResponse:
        """Derive the counts from the results so they can never disagree."""
        counts = {status: 0 for status in VerificationStatus}
        for result in self.results:
            counts[result.status] += 1
        object.__setattr__(
            self,
            "verified_count",
            counts[VerificationStatus.VERIFIED],
        )
        object.__setattr__(
            self,
            "unverified_count",
            counts[VerificationStatus.UNVERIFIED],
        )
        object.__setattr__(
            self,
            "contradicted_count",
            counts[VerificationStatus.CONTRADICTED],
        )
        object.__setattr__(
            self,
            "insufficient_evidence_count",
            counts[VerificationStatus.INSUFFICIENT_EVIDENCE],
        )
        object.__setattr__(
            self,
            "not_applicable_count",
            counts[VerificationStatus.NOT_APPLICABLE],
        )
        return self

    @property
    def claim_ids(self) -> tuple[str, ...]:
        """Ids of every claim covered, in input order."""
        return tuple(result.claim_id for result in self.results)


__all__ = [
    "AUTHORITATIVE_SOURCE_CONTRADICTS",
    "AUTHORITATIVE_SOURCE_CONFIRMS",
    "AUTHORITATIVE_TIERS",
    "CONFLICTING_AUTHORITATIVE_SOURCES",
    "IDENTITY_AMBIGUOUS",
    "IDENTITY_NOT_FOUND",
    "NO_CLAIM_RELEVANT_SOURCE",
    "NO_CONFIRMATION_FOUND",
    "NO_QUERY_BUILT",
    "NOT_A_FACTUAL_CLAIM",
    "SEARCH_FAILED",
    "SEARCH_UNAVAILABLE",
    "TIER_PRIORITY",
    "VERIFICATION_REASON_CODES",
    "ZERO_RESULTS",
    "IdentityMatch",
    "SourceTier",
    "VerificationResponse",
    "VerificationResult",
    "VerificationStatus",
]