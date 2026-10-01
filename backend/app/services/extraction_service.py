"""Extraction service (Phase 2, §1/§18/§19).

    Raw Text
       -> Normalization (reversible offset map)
       -> Claim extraction  (LLM, one structured call, plus deterministic)
       -> Entity extraction (LLM, merged with deterministic identifiers)
       -> Claim <-> entity linking
       -> ExtractionResult

Guarantees this service upholds:

* **One structured LLM call per input** (§26). Claims and entities are extracted
  together in a single request, never one call per sentence or entity.
* **Spans always index the original text.** Normalised offsets are mapped back
  through :class:`~app.services.text_normalization.NormalizedText`, so
  ``original[start:end] == text`` holds for every claim and entity.
* **No hallucination.** An LLM-supplied claim or entity that cannot be located
  in the input is dropped and recorded in `processing_warnings` (§16).
* **No verification.** This service emits no verdict of any kind; the schema has
  no field for one (§17, D-006).
* **Honest mode reporting.** `extraction_mode` states whether claims came from
  the LLM, the deterministic fallback, or a mix (§19).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator

from app.core.config import Settings
from app.core.logging import get_logger
from app.prompts.extraction import (
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_SYSTEM_PROMPT,
    build_extraction_prompt,
)
from app.schemas.claims import VERIFIABLE_CLAIM_TYPES, Claim, ClaimType
from app.schemas.common import EvidenceSpan, make_span, span_slice_matches
from app.schemas.entities import IDENTITY_ENTITY_TYPES, Entity, EntityType, normalize_entity_name
from app.schemas.extraction import (
    ClaimEntityLink,
    ExtractionMode,
    ExtractionResult,
    RelationshipType,
)
from app.services.claim_extractor import RawClaim, extract_claims, sentence_spans
from app.services.entity_extractor import RawEntity, build_entity, extract_entities
from app.services.llm_service import LLMService
from app.services.text_normalization import NormalizedText, normalize_text

logger = get_logger(__name__)


class LLMClaim(BaseModel):
    """One claim as returned by the structured LLM call.

    Attributes:
        text: Claim text, expected to be verbatim from the input.
        claim_type: Claim family chosen by the model.
        entity_names: Names of entities the claim refers to, as written.
        confidence: The model's extraction confidence.
    """

    text: str
    claim_type: ClaimType
    entity_names: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)

    @field_validator("text")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """Reject blank claim text."""
        if not value or not value.strip():
            raise ValueError("claim text must not be empty")
        return value.strip()


class LLMEntity(BaseModel):
    """One entity as returned by the structured LLM call.

    Attributes:
        name: Entity name, expected to be verbatim from the input.
        entity_type: Entity family chosen by the model.
        confidence: The model's extraction confidence.
    """

    name: str
    entity_type: EntityType
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)

    @field_validator("name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """Reject blank entity names."""
        if not value or not value.strip():
            raise ValueError("entity name must not be empty")
        return value.strip()


class LLMStructuredExtraction(BaseModel):
    """The schema the LLM must satisfy for one extraction call."""

    claims: list[LLMClaim] = Field(default_factory=list)
    entities: list[LLMEntity] = Field(default_factory=list)


@dataclass
class _AlignedClaim:
    """A claim aligned to normalised offsets, pending final span mapping."""

    start: int
    end: int
    claim_type: ClaimType
    confidence: float
    signals: tuple[ClaimType, ...]
    is_complete_sentence: bool
    entity_names: tuple[str, ...] = ()


@dataclass
class _AlignedEntity:
    """An entity aligned to normalised offsets, pending final span mapping."""

    start: int
    end: int
    entity_type: EntityType
    confidence: float
    metadata: dict[str, str | int | float | bool] = field(default_factory=dict)


class ExtractionService:
    """Produces a structured :class:`ExtractionResult` from raw text.

    Args:
        settings: Optional settings override.
        llm: Optional `LLMService` override. Tests inject a fake provider here;
            nothing else in the codebase constructs a provider.
    """

    def __init__(self, settings: Settings | None = None, llm: LLMService | None = None) -> None:
        self._settings = settings
        self._llm = llm or LLMService(settings=settings)

    @property
    def llm(self) -> LLMService:
        """The LLM gateway used for extraction."""
        return self._llm

    def extract(self, raw_text: str | None) -> ExtractionResult:
        """Extract claims and entities from raw text.

        Args:
            raw_text: The original submitted text.

        Returns:
            A fully populated :class:`ExtractionResult`. Never raises; a failure
            in any stage degrades into a warning plus whatever was extracted.
        """
        started = time.perf_counter()
        warnings: list[str] = []

        if not raw_text or not raw_text.strip():
            return ExtractionResult(
                extraction_mode=ExtractionMode.FALLBACK,
                source_text=raw_text or "",
                processing_warnings=("Input text was empty; nothing to extract.",),
            )

        normalized = normalize_text(raw_text)
        warnings.extend(normalized.warnings)

        deterministic_claims = extract_claims(normalized)
        deterministic_entities = extract_entities(normalized)

        llm_claims, llm_entities, llm_warnings = self._extract_with_llm(normalized)
        warnings.extend(llm_warnings)

        claims = self._merge_claims(deterministic_claims, llm_claims, warnings)
        entities = self._merge_entities(deterministic_entities, llm_entities, normalized)

        mode = self._resolve_mode(llm_claims, llm_entities, llm_warnings)

        final_claims = self._build_claims(claims, normalized)
        relationships = self._link(final_claims, entities)

        with_entities = tuple(
            Claim(
                id=claim.id,
                text=claim.text,
                claim_type=claim.claim_type,
                confidence=claim.confidence,
                evidence_span=claim.evidence_span,
                entity_ids=self._entity_ids_for(claim.id, relationships),
                is_complete_sentence=claim.is_complete_sentence,
                signals=claim.signals,
                metadata=claim.metadata,
            )
            for claim in final_claims
        )

        result = ExtractionResult(
            claims=with_entities,
            entities=entities,
            relationships=relationships,
            extraction_mode=mode,
            prompt_version=EXTRACTION_PROMPT_VERSION,
            model=self._model_for(mode),
            source_text=normalized.original,
            normalized_text=normalized.text,
            processing_warnings=tuple(warnings),
        )

        self._log_summary(result, time.perf_counter() - started)
        return result

    # -- stages ---------------------------------------------------------

    def _extract_with_llm(
        self, normalized: NormalizedText
    ) -> tuple[list[_AlignedClaim], list[_AlignedEntity], list[str]]:
        """Run one structured LLM extraction call and align its output.

        Args:
            normalized: Normalised text to send.

        Returns:
            ``(claims, entities, warnings)``. All three are empty/empty/no-warn
            when the LLM is unavailable.
        """
        if not self._llm.available:
            reason = "not configured" if not self._llm.model else "unavailable"
            return (
                [],
                [],
                [
                    f"LLM extraction is {reason}; deterministic extraction was used. "
                    "Claim and entity coverage is limited to pattern-based extraction."
                ],
            )

        prompt, truncation_warning = build_extraction_prompt(normalized.text)
        warnings = [truncation_warning] if truncation_warning else []

        result = self._llm.structured_generate(
            prompt,
            LLMStructuredExtraction,
            schema_name="investment_extraction",
            system=EXTRACTION_SYSTEM_PROMPT,
        )
        if not result.ok or result.data is None:
            warnings.append(
                f"LLM extraction unavailable ({result.error_code or 'unknown'}); "
                "deterministic extraction was used instead."
            )
            return [], [], warnings

        claims: list[_AlignedClaim] = []
        for item in result.data.claims:
            aligned = self._align_text(item.text, normalized)
            if aligned is None:
                warnings.append(
                    "A model-supplied claim could not be located in the input and was discarded "
                    "to avoid reporting content that was not stated."
                )
                continue
            start, end = aligned
            claims.append(
                _AlignedClaim(
                    start=start,
                    end=end,
                    claim_type=item.claim_type,
                    confidence=round(min(1.0, max(0.0, item.confidence)), 2),
                    signals=(item.claim_type,),
                    is_complete_sentence=self._is_complete_sentence(normalized.text, start, end),
                    entity_names=tuple(item.entity_names),
                )
            )

        entities: list[_AlignedEntity] = []
        for item in result.data.entities:
            aligned = self._align_text(item.name, normalized)
            if aligned is None:
                warnings.append(
                    "A model-supplied entity could not be located in the input and was discarded "
                    "to avoid reporting entities that were not mentioned."
                )
                continue
            start, end = aligned
            entities.append(
                _AlignedEntity(
                    start=start,
                    end=end,
                    entity_type=item.entity_type,
                    confidence=round(min(1.0, max(0.0, item.confidence)), 2),
                )
            )

        return claims, entities, warnings

    def _merge_claims(
        self,
        deterministic: list[RawClaim],
        llm: list[_AlignedClaim],
        warnings: list[str],
    ) -> list[_AlignedClaim]:
        """Combine deterministic and LLM claims, preferring wider coverage.

        A claim present in both sources is kept once, keeping the deterministic
        span (which is exactly known) and the LLM's entity hints.

        Args:
            deterministic: Claims from the deterministic extractor.
            llm: Claims from the structured LLM call.
            warnings: Warning sink, appended to in place.

        Returns:
            Merged claims in source order.
        """
        merged: dict[tuple[int, int], _AlignedClaim] = {}
        for raw in deterministic:
            key = (raw.start, raw.end)
            merged[key] = _AlignedClaim(
                start=raw.start,
                end=raw.end,
                claim_type=raw.claim_type,
                confidence=raw.confidence,
                signals=raw.signals,
                is_complete_sentence=raw.is_complete_sentence,
            )

        for claim in llm:
            key = (claim.start, claim.end)
            existing = merged.get(key)
            if existing is None:
                merged[key] = claim
                continue
            if claim.entity_names and not existing.entity_names:
                existing.entity_names = claim.entity_names
            # Deterministic typing is more precise than model typing when both
            # describe the same span.
            if existing.claim_type is ClaimType.OTHER:
                existing.claim_type = claim.claim_type

        if deterministic and llm and not any(
            (claim.start, claim.end) in merged for claim in llm
        ):  # pragma: no cover - defensive
            warnings.append("Model claims could not be reconciled with deterministic extraction.")

        return sorted(merged.values(), key=lambda item: (item.start, item.end))

    def _merge_entities(
        self,
        deterministic: list[RawEntity],
        llm: list[_AlignedEntity],
        normalized: NormalizedText,
    ) -> tuple[Entity, ...]:
        """Combine deterministic and LLM entities, de-duplicating by type + name.

        The same entity mentioned more than once yields a single entity, located
        at its first mention. Later claims still link to it by name.

        Args:
            deterministic: Entities from the deterministic extractor.
            llm: Entities from the structured LLM call.
            normalized: Normalised text, used to slice verbatim spans.

        Returns:
            De-duplicated entities in source order, with original-text spans.
        """
        candidates: list[_AlignedEntity] = [
            _AlignedEntity(
                start=raw.start,
                end=raw.end,
                entity_type=raw.entity_type,
                confidence=raw.confidence,
                metadata=dict(raw.metadata),
            )
            for raw in deterministic
        ]
        candidates.extend(llm)
        candidates.sort(key=lambda item: (item.start, item.end, -item.confidence))

        built: list[Entity] = []
        seen: set[tuple[str, str]] = set()
        for index, item in enumerate(candidates, start=1):
            original_start, original_end = normalized.to_original(item.start, item.end)
            if original_end <= original_start:
                continue
            try:
                span = make_span(normalized.original, original_start, original_end)
            except ValueError:  # pragma: no cover - defensive
                continue

            name = span.text
            normalized_name = normalize_entity_name(name) or normalize_entity_name(
                normalized.text[item.start : item.end]
            )
            key = (item.entity_type.value, normalized_name)
            if not normalized_name or key in seen:
                continue
            seen.add(key)

            built.append(
                Entity(
                    id=f"entity_{len(built) + 1:03d}",
                    name=name,
                    entity_type=item.entity_type,
                    normalized_name=normalized_name,
                    confidence=round(min(1.0, max(0.0, item.confidence)), 2),
                    evidence_span=span,
                    metadata=dict(item.metadata),
                )
            )

        return tuple(built)

    def _build_claims(
        self,
        claims: list[_AlignedClaim],
        normalized: NormalizedText,
    ) -> tuple[Claim, ...]:
        """Map aligned claims onto the original text and assign deterministic ids.

        Args:
            claims: Claims in normalised coordinates.
            normalized: The normalised text.

        Returns:
            Claims whose spans index the original text exactly.
        """
        built: list[Claim] = []
        for item in claims:
            original_start, original_end = normalized.to_original(item.start, item.end)
            if original_end <= original_start:
                continue
            try:
                span: EvidenceSpan = make_span(normalized.original, original_start, original_end)
            except ValueError:
                continue
            if not span_slice_matches(normalized.original, span):  # pragma: no cover - defensive
                continue
            built.append(
                Claim(
                    id=f"claim_{len(built) + 1:03d}",
                    text=span.text,
                    claim_type=item.claim_type,
                    confidence=round(min(1.0, max(0.0, item.confidence)), 2),
                    evidence_span=span,
                    is_complete_sentence=item.is_complete_sentence,
                    signals=item.signals,
                    metadata=claim_metadata(span.text),
                )
            )
        return tuple(built)

    def _link(
        self, claims: tuple[Claim, ...], entities: tuple[Entity, ...]
    ) -> tuple[ClaimEntityLink, ...]:
        """Link claims to the entities they reference.

        An entity is linked when its span falls inside the claim's span, or when
        its name appears in the claim text. The earliest-mentioned identity
        entity of an identity-type claim is marked as the claim's subject.

        Args:
            claims: Built claims.
            entities: Built entities.

        Returns:
            Unique claim-to-entity links.
        """
        links: dict[tuple[str, str], ClaimEntityLink] = {}
        for claim in claims:
            start, end = claim.evidence_span.start, claim.evidence_span.end
            claim_text = claim.text.lower()
            identity_hits: list[tuple[int, str]] = []

            for entity in entities:
                entity_start = entity.evidence_span.start
                contained = start <= entity_start and entity.evidence_span.end <= end
                if not contained and entity.name.lower() not in claim_text:
                    continue
                links[(claim.id, entity.id)] = ClaimEntityLink(
                    claim_id=claim.id, entity_id=entity.id, relationship=RelationshipType.MENTIONS
                )
                if entity.entity_type in IDENTITY_ENTITY_TYPES:
                    identity_hits.append((entity_start, entity.id))

            if identity_hits and claim.claim_type in VERIFIABLE_CLAIM_TYPES:
                subject_id = min(identity_hits)[1]
                links[(claim.id, subject_id)] = ClaimEntityLink(
                    claim_id=claim.id, entity_id=subject_id, relationship=RelationshipType.SUBJECT
                )

        return tuple(sorted(links.values(), key=lambda item: (item.claim_id, item.entity_id)))

    @staticmethod
    def _entity_ids_for(claim_id: str, links: tuple[ClaimEntityLink, ...]) -> tuple[str, ...]:
        """Return the entity ids linked to a claim."""
        return tuple(link.entity_id for link in links if link.claim_id == claim_id)

    # -- helpers --------------------------------------------------------

    def _align_text(self, value: str, normalized: NormalizedText) -> tuple[int, int] | None:
        """Locate model-supplied text inside the normalised input.

        Tries an exact match, a case-insensitive match, and finally a
        whitespace-collapsed match. Returns ``None`` when the text cannot be
        found, so the caller can drop it rather than report an unlocatable span.

        Args:
            value: Text supplied by the model.
            normalized: The normalised input.

        Returns:
            ``(start, end)`` in normalised coordinates, or ``None``.
        """
        candidate = value.strip()
        if not candidate:
            return None
        text = normalized.text

        found = text.find(candidate)
        if found != -1:
            return (found, found + len(candidate))

        lowered_text = text.lower()
        found = lowered_text.find(candidate.lower())
        if found != -1:
            return (found, found + len(candidate))

        loose_map = _loose_index_map(text)
        loose_text = "".join(text[start:end] for start, end in loose_map)
        found = loose_text.find(_collapse(candidate))
        if found == -1:
            # Retry with punctuation trimmed from both ends.
            trimmed = candidate.rstrip(".,;:!?").lstrip(".,;:!?")
            if trimmed and trimmed != candidate:
                found = loose_text.find(_collapse(trimmed))
        if found == -1:
            return None
        return _loose_span_to_normalized(loose_map, found, found + len(_collapse(candidate)))

    @staticmethod
    def _is_complete_sentence(text: str, start: int, end: int) -> bool:
        """Return True when a span covers a whole sentence."""
        return any((start, end) == span for span in sentence_spans(text))

    @staticmethod
    def _resolve_mode(
        llm_claims: list[_AlignedClaim],
        llm_entities: list[_AlignedEntity],
        llm_warnings: list[str],
    ) -> ExtractionMode:
        """Decide the reported extraction mode.

        Args:
            llm_claims: Aligned LLM claims.
            llm_entities: Aligned LLM entities.
            llm_warnings: Warnings produced by the LLM stage.

        Returns:
            The mode to report.
        """
        llm_failed = any(
            "LLM extraction unavailable" in warning or "LLM extraction is" in warning
            for warning in llm_warnings
        )
        if llm_failed:
            return ExtractionMode.FALLBACK
        if not llm_claims and not llm_entities:
            return ExtractionMode.PARTIAL
        # Some model output survived and some had to be discarded: the result is
        # a mix of both sources, and reporting it as purely LLM would overstate
        # what the model actually contributed.
        if any("A model-supplied" in warning for warning in llm_warnings):
            return ExtractionMode.PARTIAL
        return ExtractionMode.LLM

    def _model_for(self, mode: ExtractionMode) -> str | None:
        """Return the model name to record, or ``None`` in pure fallback mode."""
        if mode is ExtractionMode.FALLBACK:
            return None
        return self._llm.model

    @staticmethod
    def _log_summary(result: ExtractionResult, duration_ms: float) -> None:
        """Log extraction outcome without logging the user's content."""
        logger.info(
            "Extraction complete",
            extra={
                "input_chars": len(result.source_text),
                "extraction_mode": result.extraction_mode.value,
                "provider": "llm" if result.extraction_mode is not ExtractionMode.FALLBACK else "fallback",
                "model": result.model,
                "prompt_version": result.prompt_version,
                "claims": len(result.claims),
                "entities": len(result.entities),
                "relationships": len(result.relationships),
                "warnings": len(result.processing_warnings),
                "duration_ms": int(duration_ms),
            },
        )


def _collapse(value: str) -> str:
    """Collapse whitespace runs so two spellings of the same text compare equal."""
    return " ".join(value.split())


#: Monetary amounts written in the claim text. Recorded on the claim because the
#: entity taxonomy deliberately has no "money" type: an amount is a property of
#: a claim, not a named party.
_MONEY = re.compile(r"(?:₹|\$|€|£|\brs\.?\s*|\binr\s*)\s?[\d,]+(?:\.\d+)?(?:\s?(?:lakh|lakhs|crore|crores|k))?", re.IGNORECASE)
_PERCENT = re.compile(r"\b\d+(?:\.\d+)?\s?%")
_PERIOD = re.compile(
    r"\b(?:monthly|weekly|daily|quarterly|annually|per\s+annum|per\s+month|per\s+year|"
    r"daily|monthly|annual|inr\s*\d+\s*days?)\b",
    re.IGNORECASE,
)


def claim_metadata(text: str) -> dict[str, str]:
    """Extract value-bearing details from a claim's own text.

    Only values literally present in the claim are recorded; nothing is
    inferred. Money amounts live here rather than in the entity list because
    the entity taxonomy is reserved for named parties, instruments and
    identifiers.

    Args:
        text: The claim text.

    Returns:
        A metadata mapping with any of ``monetary_amounts``, ``percentages``
        and ``periods`` present in the text.
    """
    metadata: dict[str, str] = {}
    amounts = [match.group(0).strip() for match in _MONEY.finditer(text)]
    if amounts:
        metadata["monetary_amounts"] = ", ".join(dict.fromkeys(amounts))
    percentages = [match.group(0).strip() for match in _PERCENT.finditer(text)]
    if percentages:
        metadata["percentages"] = ", ".join(dict.fromkeys(percentages))
    periods = [match.group(0).strip() for match in _PERIOD.finditer(text)]
    if periods:
        metadata["periods"] = ", ".join(dict.fromkeys(periods))
    return metadata


def _loose_index_map(text: str) -> list[tuple[int, int]]:
    """Return whitespace-delimited ``(start, end)`` segments of `text`."""
    segments: list[tuple[int, int]] = []
    index = 0
    length = len(text)
    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break
        start = index
        while index < length and not text[index].isspace():
            index += 1
        segments.append((start, index))
    return segments


def _loose_span_to_normalized(
    segments: list[tuple[int, int]], loose_start: int, loose_end: int
) -> tuple[int, int] | None:
    """Convert a span in loose text back to normalised coordinates."""
    if loose_start >= loose_end or not segments:
        return None
    start = segments[loose_start][0]
    end = segments[loose_end - 1][1]
    return (start, end)


def build_extraction_service(settings: Settings | None = None) -> ExtractionService:
    """Construct an extraction service using configured providers.

    Args:
        settings: Optional settings override.

    Returns:
        A ready-to-use :class:`ExtractionService`.
    """
    return ExtractionService(settings=settings, llm=LLMService(settings=settings))


__all__ = [
    "ExtractionService",
    "LLMClaim",
    "LLMEntity",
    "LLMStructuredExtraction",
    "build_extraction_service",
]
