"""Entity schemas (Phase 2).

An **entity** is a person, organisation, instrument, identifier or endpoint that
the submitted content names or references.

Extraction identifies what the text refers to. It makes **no** statement about
whether the entity is legitimate, registered or fraudulent — that is Phase 4's
job (see D-006). ``Entity.confidence`` is extraction confidence only.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import EvidenceSpan


class EntityType(str, Enum):
    """Controlled vocabulary for entity types."""

    PERSON = "PERSON"
    COMPANY = "COMPANY"
    ORGANIZATION = "ORGANIZATION"
    REGULATOR = "REGULATOR"
    BROKER = "BROKER"
    INVESTMENT_ADVISER = "INVESTMENT_ADVISER"
    PLATFORM = "PLATFORM"
    WEBSITE = "WEBSITE"
    DOMAIN = "DOMAIN"
    PRODUCT = "PRODUCT"
    FINANCIAL_INSTRUMENT = "FINANCIAL_INSTRUMENT"
    LOCATION = "LOCATION"
    SOCIAL_HANDLE = "SOCIAL_HANDLE"
    REGISTRATION_NUMBER = "REGISTRATION_NUMBER"
    BANK_ACCOUNT = "BANK_ACCOUNT"
    UPI_ID = "UPI_ID"
    PHONE_NUMBER = "PHONE_NUMBER"
    EMAIL = "EMAIL"
    OTHER = "OTHER"


#: Entity types that identify a *named party* whose existence or status a later
#: phase may want to verify against an authoritative record.
IDENTITY_ENTITY_TYPES: frozenset[EntityType] = frozenset(
    {
        EntityType.PERSON,
        EntityType.COMPANY,
        EntityType.ORGANIZATION,
        EntityType.REGULATOR,
        EntityType.BROKER,
        EntityType.INVESTMENT_ADVISER,
    }
)

#: Entity types that are payment destinations. Their presence is a strong signal
#: in later payment analysis.
PAYMENT_ENTITY_TYPES: frozenset[EntityType] = frozenset(
    {EntityType.BANK_ACCOUNT, EntityType.UPI_ID, EntityType.PHONE_NUMBER}
)


class Entity(BaseModel):
    """One extracted entity, located in the original text.

    Attributes:
        id: Deterministic per-extraction id, e.g. ``entity_001``.
        name: The entity as written in the input.
        entity_type: Which entity family this belongs to.
        normalized_name: Conservative normalised form used for matching and
            de-duplication (lower-cased, legal suffixes expanded, known regulator
            acronyms expanded). When normalisation is uncertain the original
            name is preserved.
        confidence: **Extraction** confidence in ``[0, 1]`` — how sure the
            extractor is that this mention is an entity of this type. It is
            *not* a legitimacy or truth assessment.
        evidence_span: Exact offsets of ``name`` in the original input.
        metadata: Extra attributes, e.g. ``scheme`` for a URL or ``host`` for a
            domain. Never contains invented values.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    entity_type: EntityType
    normalized_name: str
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Confidence that this mention is an entity of this type. "
            "This is an extraction confidence, NOT a legitimacy assessment."
        ),
    )
    evidence_span: EvidenceSpan
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _name_matches_span(self) -> Entity:
        """Keep ``name`` and ``evidence_span`` consistent, and a stable key."""
        if self.name != self.evidence_span.text:
            raise ValueError("entity name must equal evidence_span.text")
        if not self.normalized_name:
            raise ValueError("normalized_name must not be empty")
        return self

    @property
    def key(self) -> tuple[str, str]:
        """Return the de-duplication key: type plus normalised name."""
        return (self.entity_type.value, self.normalized_name)


#: Legal suffixes expanded during normalisation. Expansion only, never addition:
#: "Pvt. Ltd." becomes "private limited", and nothing is inferred about the
#: entity that was not written in the input.
_LEGAL_SUFFIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(pvt)\.?\s*(ltd)\.?\b", re.IGNORECASE), r"private limited"),
    (re.compile(r"\b(private)\s+(limited|ltd)\.?\b", re.IGNORECASE), r"private limited"),
    (re.compile(r"\b(ltd)\.?\b", re.IGNORECASE), "limited"),
    (re.compile(r"\b(llp)\b", re.IGNORECASE), "limited liability partnership"),
    (re.compile(r"\b(inc)\.?\b", re.IGNORECASE), "incorporated"),
    (re.compile(r"\b(corp)\.?\b", re.IGNORECASE), "corporation"),
    (re.compile(r"\b(ltda)\b", re.IGNORECASE), "limited"),
)

#: Regulator acronyms expanded during normalisation. A curated map only — an
#: unknown acronym is left untouched rather than guessed at.
REGULATOR_EXPANSIONS: dict[str, str] = {
    "sebi": "securities and exchange board of india",
    "rbi": "reserve bank of india",
    "nse": "national stock exchange of india",
    "bse": "bombay stock exchange",
    "mca": "ministry of corporate affairs",
    "irdai": "insurance regulatory and development authority of india",
    "nfra": "national financial reporting authority",
    "pfrda": "pension fund regulatory and development authority",
    "sfio": "securities fraud investigation office",
    "nbfc": "non banking financial company",
}

#: Punctuation removed when normalising a name. ``@``, ``.`` and ``-`` are
#: **kept**: they are structural in UPI ids, emails, domains and handles, so
#: stripping them would merge distinct identifiers (``a.b@x`` vs ``ab@x``).
_PUNCTUATION = re.compile(r"[^\w\s&.@-]+", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")
#: Structural punctuation that must not survive at the edges of a name.
_EDGE_PUNCTUATION = re.compile(r"^[\s.\-@&]+|[\s.\-@&]+$")


def normalize_entity_name(name: str) -> str:
    """Normalise an entity name conservatively.

    Steps: Unicode NFC, lower-case, expand known legal suffixes, expand known
    regulator acronyms, drop punctuation that carries no identity, collapse
    whitespace.

    Nothing is invented. An unrecognised token is preserved as written (only
    lower-cased), so an uncertain normalisation keeps the original information.

    Args:
        name: The entity name as written in the input.

    Returns:
        The normalised form used for matching and de-duplication.
    """
    import unicodedata

    text = unicodedata.normalize("NFC", name).strip().lower()
    if not text:
        return name.strip().lower()

    for pattern, replacement in _LEGAL_SUFFIXES:
        text = pattern.sub(replacement, text)

    expanded = REGULATOR_EXPANSIONS.get(text.strip())
    if expanded:
        text = expanded

    text = _PUNCTUATION.sub(" ", text)
    text = _WHITESPACE.sub(" ", text)
    text = _EDGE_PUNCTUATION.sub("", text).strip()
    return text or name.strip().lower()


__all__ = [
    "IDENTITY_ENTITY_TYPES",
    "PAYMENT_ENTITY_TYPES",
    "REGULATOR_EXPANSIONS",
    "Entity",
    "EntityType",
    "normalize_entity_name",
]
