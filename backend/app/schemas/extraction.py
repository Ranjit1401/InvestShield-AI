"""Extraction result schemas (Phase 2).

The top-level machine-readable output of the extraction stage:

    Raw Text -> Normalization -> Claims + Entities -> Claim/Entity links
             -> ExtractionResult

An ``ExtractionResult`` states **what the content claims and references**. It
never states whether those claims are true. There is deliberately no field on
this model for a verification verdict (D-006).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.claims import Claim
from app.schemas.entities import Entity


class ExtractionMode(str, Enum):
    """How the extraction was produced.

    The mode is never cosmetic: a fallback result is materially weaker than an
    LLM result and the UI/report must be able to say so.
    """

    #: Claims and entities produced by the structured LLM call.
    LLM = "LLM"
    #: Deterministic extraction only, because the LLM was unavailable or failed.
    FALLBACK = "FALLBACK"
    #: Mixed: the LLM produced something usable, but deterministic extraction was
    #: required to fill gaps (or the LLM output failed alignment and was dropped).
    PARTIAL = "PARTIAL"


class RelationshipType(str, Enum):
    """How an entity relates to a claim."""

    #: The entity is named inside the claim's text.
    MENTIONS = "MENTIONS"
    #: The entity is the subject of the claim.
    SUBJECT = "SUBJECT"


class ClaimEntityLink(BaseModel):
    """A claim-to-entity relationship.

    Attributes:
        claim_id: Id of the claim.
        entity_id: Id of the entity.
        relationship: How the entity relates to the claim.
    """

    model_config = ConfigDict(frozen=True)

    claim_id: str
    entity_id: str
    relationship: RelationshipType = RelationshipType.MENTIONS

    @property
    def key(self) -> tuple[str, str]:
        """Return the de-duplication key for this link."""
        return (self.claim_id, self.entity_id)


class ExtractionResult(BaseModel):
    """Machine-readable extraction output for one input text.

    Attributes:
        claims: Extracted claims, in source order.
        entities: Extracted entities, in source order.
        relationships: Claim-to-entity links.
        extraction_mode: Whether claims/entities came from the LLM, the
            deterministic fallback, or a mix.
        prompt_version: Version of the extraction prompt used, for auditability.
        model: LLM model identifier, or ``None`` in pure fallback mode.
        source_text: The original submitted text that all spans index into.
        normalized_text: Whitespace-normalised text actually scanned. Kept for
            debugging only; no span refers to it.
        processing_warnings: Human-readable notes about degraded coverage.
    """

    model_config = ConfigDict(frozen=True)

    claims: tuple[Claim, ...] = ()
    entities: tuple[Entity, ...] = ()
    relationships: tuple[ClaimEntityLink, ...] = ()
    extraction_mode: ExtractionMode = ExtractionMode.FALLBACK
    prompt_version: str | None = None
    model: str | None = None
    source_text: str = ""
    normalized_text: str = ""
    processing_warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _links_resolve(self) -> ExtractionResult:
        """Every link must reference a claim and entity present in this result."""
        claim_ids = {claim.id for claim in self.claims}
        entity_ids = {entity.id for entity in self.entities}
        for link in self.relationships:
            if link.claim_id not in claim_ids:
                raise ValueError(f"relationship references unknown claim {link.claim_id}")
            if link.entity_id not in entity_ids:
                raise ValueError(f"relationship references unknown entity {link.entity_id}")
        return self

    def claims_of_type(self, *claim_types: object) -> tuple[Claim, ...]:
        """Return claims matching any of the given types.

        Args:
            *claim_types: :class:`~app.schemas.claims.ClaimType` members.

        Returns:
            Matching claims in source order.
        """
        wanted = {str(item) for item in claim_types}
        return tuple(claim for claim in self.claims if claim.claim_type.value in wanted)

    def entities_of_type(self, *entity_types: object) -> tuple[Entity, ...]:
        """Return entities matching any of the given types.

        Args:
            *entity_types: :class:`~app.schemas.entities.EntityType` members.

        Returns:
            Matching entities in source order.
        """
        wanted = {str(item) for item in entity_types}
        return tuple(entity for entity in self.entities if entity.entity_type.value in wanted)

    def entity_ids_for(self, claim_id: str) -> tuple[str, ...]:
        """Return the entity ids linked to a claim.

        Args:
            claim_id: Id of the claim.

        Returns:
            Linked entity ids.
        """
        return tuple(link.entity_id for link in self.relationships if link.claim_id == claim_id)


__all__ = [
    "ClaimEntityLink",
    "ExtractionMode",
    "ExtractionResult",
    "RelationshipType",
]
