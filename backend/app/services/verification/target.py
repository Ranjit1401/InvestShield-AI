"""Verification targets (Phase 4, §3).

A `VerificationTarget` is the fully-resolved question verification will ask for
one claim: *is this claim, about this entity, checkable against these sources?*

Building it is deliberately separate from searching. The target answers "what
would we look for, and where?", which is pure derivation from the `Claim` and
its linked `Entity` objects. Nothing here touches the network, and nothing here
decides an outcome — so query strategy can be tested exhaustively without a
provider, a key, or a network (D-009, D-019).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.schemas.claims import Claim, ClaimType
from app.schemas.entities import IDENTITY_ENTITY_TYPES, Entity, normalize_entity_name
from app.schemas.search import SourceType
from app.services.verification.authority_registry import (
    AuthoritySource,
    authorities_for_claim_type,
    source_type_for_tier,
)

#: Claim families that state an instruction or an opinion rather than a factual
#: assertion about a named party. No external record can confirm "invest now",
#: "send money to this UPI id", or a stray opinion, so verification does not
#: apply and no search is spent (D-006).
NON_VERIFIABLE_CLAIM_TYPES: frozenset[ClaimType] = frozenset(
    {
        ClaimType.INVESTMENT_OPPORTUNITY,
        ClaimType.PAYMENT_INSTRUCTION,
        ClaimType.OTHER,
    }
)

#: Claim families for which "we searched and found nothing" is a meaningful
#: finding, because the thing being asserted is the *existence* of a record.
#: For these, zero results support `UNVERIFIED`; for other families, zero
#: results only mean the search was not conclusive.
REGISTRATION_STYLE_CLAIM_TYPES: frozenset[ClaimType] = frozenset(
    {
        ClaimType.REGULATORY_STATUS,
        ClaimType.CREDENTIAL_CLAIM,
        ClaimType.OWNERSHIP_CLAIM,
    }
)


@dataclass(frozen=True)
class VerificationTarget:
    """Everything verification needs to decide how to look for one claim.

    Attributes:
        claim_id: Id of the `Claim` under verification.
        claim_type: Its claim family.
        claim_text: Verbatim claim text.
        entity_ids: Ids of linked entities, in link order.
        identity_entities: Linked entities that can name a party, in link order.
            A claim with none cannot be attributed to a specific organisation,
            which materially limits what verification may conclude.
        preferred_source_types: Publisher categories worth consulting first.
        preferred_authorities: Registry authorities whose records are relevant
            to this claim family, best tier first. Empty for a family no
            official record can settle.
        search_queries: Targeted queries to issue, already de-duplicated.
        identifiers: Verbatim identifiers found in the claim or its entities
            (registration numbers, UPI ids). Preserved exactly; never rewritten
            (D-019).
    """

    claim_id: str
    claim_type: ClaimType
    claim_text: str
    entity_ids: tuple[str, ...] = ()
    identity_entities: tuple[Entity, ...] = ()
    preferred_source_types: tuple[SourceType, ...] = ()
    preferred_authorities: tuple[AuthoritySource, ...] = ()
    search_queries: tuple[str, ...] = ()
    identifiers: tuple[str, ...] = ()

    @property
    def is_applicable(self) -> bool:
        """Whether external verification could ever apply to this claim."""
        return self.claim_type not in NON_VERIFIABLE_CLAIM_TYPES

    @property
    def is_registration_style(self) -> bool:
        """Whether absence of a record is itself the informative outcome."""
        return self.claim_type in REGISTRATION_STYLE_CLAIM_TYPES

    @property
    def has_identity(self) -> bool:
        """Whether a named party can be pinned down from the claim alone."""
        return bool(self.identity_entities)

    @property
    def subject(self) -> str:
        """The best name to search for: the first identity entity, else the text.

        Returns:
            The entity name as written in the input, or the claim text when no
            entity names a party.
        """
        for entity in self.identity_entities:
            if entity.name.strip():
                return entity.name.strip()
        return self.claim_text.strip()


def identity_entities_for(claim: Claim, entities: tuple[Entity, ...]) -> tuple[Entity, ...]:
    """Return the linked entities that can name a party under verification.

    Order follows the claim's own `entity_ids` so the result is deterministic,
    and duplicates are collapsed by `(type, normalized_name)` so one mention
    appearing twice cannot dominate query construction.

    Args:
        claim: The claim whose linked entities are wanted.
        entities: All entities from the extraction.

    Returns:
        Identity entities in link order, de-duplicated.
    """
    by_id = {entity.id: entity for entity in entities}
    chosen: list[Entity] = []
    seen: set[tuple[str, str]] = set()
    for entity_id in claim.entity_ids:
        entity = by_id.get(entity_id)
        if entity is None or entity.entity_type not in IDENTITY_ENTITY_TYPES:
            continue
        if entity.key in seen:
            continue
        seen.add(entity.key)
        chosen.append(entity)
    return tuple(chosen)


def identifiers_for(claim: Claim, entities: tuple[Entity, ...]) -> tuple[str, ...]:
    """Collect verbatim identifiers attached to a claim.

    Registration numbers and payment handles are the terms a registry lookup
    must match exactly. They are passed through untouched — no spacing, casing
    or punctuation is "fixed", because a modified identifier is a different
    identifier (D-019).

    Args:
        claim: The claim.
        entities: All entities from the extraction.

    Returns:
        Identifier strings in link order, de-duplicated exactly.
    """
    by_id = {entity.id: entity for entity in entities}
    identifiers: list[str] = []
    seen: set[str] = set()
    for entity_id in claim.entity_ids:
        entity = by_id.get(entity_id)
        if entity is None:
            continue
        value = entity.name.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        identifiers.append(value)
    return tuple(identifiers)


def build_target(claim: Claim, entities: tuple[Entity, ...] = ()) -> VerificationTarget:
    """Resolve a `Claim` into a fully specified `VerificationTarget`.

    No search is issued here. The result records which authorities are relevant
    to the claim family and, for the applicable ones, which targeted queries
    would be sent.

    Args:
        claim: The claim to verify.
        entities: All entities from the extraction, used to resolve
            `claim.entity_ids`.

    Returns:
        A `VerificationTarget`. For a non-applicable claim family,
        `search_queries` is empty and no authority is relevant.
    """
    identities = identity_entities_for(claim, entities)
    authorities = authorities_for_claim_type(claim.claim_type)
    identifiers = identifiers_for(claim, entities)

    source_types: list[SourceType] = []
    for authority in authorities:
        source_type = source_type_for_tier(authority.tier)
        if source_type not in source_types:
            source_types.append(source_type)

    target = VerificationTarget(
        claim_id=claim.id,
        claim_type=claim.claim_type,
        claim_text=claim.text,
        entity_ids=tuple(claim.entity_ids),
        identity_entities=identities,
        preferred_source_types=tuple(source_types),
        preferred_authorities=authorities,
        identifiers=identifiers,
    )

    if not target.is_applicable:
        return target

    from app.services.verification.query_builder import build_queries

    return replace(target, search_queries=build_queries(target))


def normalized_subject(target: VerificationTarget) -> str:
    """Return the subject in the normalized form used for identity matching.

    Args:
        target: The verification target.

    Returns:
        The normalized subject name, or ``""`` when there is none.
    """
    if target.identity_entities:
        return normalize_entity_name(target.identity_entities[0].name)
    return normalize_entity_name(target.subject) if target.subject else ""


__all__ = [
    "NON_VERIFIABLE_CLAIM_TYPES",
    "REGISTRATION_STYLE_CLAIM_TYPES",
    "VerificationTarget",
    "build_target",
    "identifiers_for",
    "identity_entities_for",
    "normalized_subject",
]