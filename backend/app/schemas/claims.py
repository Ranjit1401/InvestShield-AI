"""Claim schemas (Phase 2).

A **claim** is a statement in the submitted content that could potentially be
verified, contradicted, supported or contextualised by a later phase. Generic
conversational filler is not a claim.

Phase 2 extracts claims. It never judges them. No field on these models carries
a truth judgement, and :attr:`Claim.confidence` means *extraction* confidence,
not the likelihood that the claim is true (see ``Claim.confidence``).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.schemas.common import EvidenceSpan


class ClaimType(str, Enum):
    """Controlled vocabulary for claim types.

    Intentionally small and extensible. A new value may be added when a claim
    family appears that none of these describe; existing values must not be
    repurposed, because they are persisted and shown to users.
    """

    REGULATORY_STATUS = "REGULATORY_STATUS"
    RETURN_PROMISE = "RETURN_PROMISE"
    PROFIT_PROMISE = "PROFIT_PROMISE"
    PERFORMANCE_CLAIM = "PERFORMANCE_CLAIM"
    CREDENTIAL_CLAIM = "CREDENTIAL_CLAIM"
    COMPANY_CLAIM = "COMPANY_CLAIM"
    PRODUCT_CLAIM = "PRODUCT_CLAIM"
    INVESTMENT_OPPORTUNITY = "INVESTMENT_OPPORTUNITY"
    PAYMENT_INSTRUCTION = "PAYMENT_INSTRUCTION"
    WITHDRAWAL_CLAIM = "WITHDRAWAL_CLAIM"
    AFFILIATION_CLAIM = "AFFILIATION_CLAIM"
    OWNERSHIP_CLAIM = "OWNERSHIP_CLAIM"
    GUARANTEE_CLAIM = "GUARANTEE_CLAIM"
    OTHER = "OTHER"


#: Claim types whose truth is decided by checking an external record. Phase 4
#: verification uses this to decide what is worth searching for.
VERIFIABLE_CLAIM_TYPES: frozenset[ClaimType] = frozenset(
    {
        ClaimType.REGULATORY_STATUS,
        ClaimType.CREDENTIAL_CLAIM,
        ClaimType.COMPANY_CLAIM,
        ClaimType.OWNERSHIP_CLAIM,
        ClaimType.AFFILIATION_CLAIM,
        ClaimType.PERFORMANCE_CLAIM,
        ClaimType.WITHDRAWAL_CLAIM,
    }
)

#: Claim types that state a promise about money. These are the highest-value
#: targets for claim verification.
PROMISE_CLAIM_TYPES: frozenset[ClaimType] = frozenset(
    {
        ClaimType.GUARANTEE_CLAIM,
        ClaimType.RETURN_PROMISE,
        ClaimType.PROFIT_PROMISE,
    }
)


class Claim(BaseModel):
    """One atomically extracted claim, located in the original text.

    Attributes:
        id: Deterministic per-extraction id, e.g. ``claim_001``.
        text: The verbatim claim text. Always a substring of the original input.
        claim_type: Which claim family this belongs to.
        confidence: **Extraction** confidence in ``[0, 1]`` — how sure the
            extractor is that this text expresses a claim of this type. It is
            *not* the probability that the claim is true.
        evidence_span: Exact offsets of ``text`` in the original input.
        entity_ids: Ids of entities referenced by this claim.
        is_complete_sentence: False when ``text`` is a clause or fragment
            extracted from a longer sentence, so the report layer can label it.
        signals: Other claim signals observed in the same clause. Preserves
            information without emitting duplicate claims.
        metadata: Values extracted from the claim text itself, e.g.
            ``monetary_amounts`` or ``percentages``. Nothing here is inferred
            from outside the input.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    text: str
    claim_type: ClaimType
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Confidence that the text expresses a claim of this type. "
            "This is an extraction confidence, NOT the likelihood the claim is true."
        ),
    )
    evidence_span: EvidenceSpan
    entity_ids: tuple[str, ...] = ()
    is_complete_sentence: bool = True
    signals: tuple[ClaimType, ...] = ()
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _text_matches_span(self) -> Claim:
        """Keep ``text`` and ``evidence_span`` consistent."""
        if self.text != self.evidence_span.text:
            raise ValueError("claim text must equal evidence_span.text")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_verifiable(self) -> bool:
        """Whether Phase 4 should attempt external verification for this claim."""
        return self.claim_type in VERIFIABLE_CLAIM_TYPES

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_promise(self) -> bool:
        """Whether this claim promises a financial outcome."""
        return self.claim_type in PROMISE_CLAIM_TYPES

    @computed_field  # type: ignore[prop-decorator]
    @property
    def references_entities(self) -> bool:
        """Whether any entity is linked to this claim."""
        return bool(self.entity_ids)


__all__ = [
    "PROMISE_CLAIM_TYPES",
    "VERIFIABLE_CLAIM_TYPES",
    "Claim",
    "ClaimType",
]
