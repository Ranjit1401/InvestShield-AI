"""Investigation persistence (Phase 9).

The repository's one job is to make a finished `InvestigationState` **the same
object** after a round trip through the database. It does that by returning an
`InvestigationState` rather than a bespoke result type, so the Phase 8 adapter —
`serialize_investigation` — is reused verbatim for both a live run and a
retrieved one. There is therefore no second code path that could shape a
retrieved investigation differently from a fresh one, which is the only way to
guarantee `GET /api/investigations/{id}` agrees with the `POST` that stored it.

Four rules govern the mapping.

- **Store the value, never a restatement of it.** Enums are written as their
  `.value` and rebuilt through the same enum, JSON bags are written verbatim, and
  every computed field is *recomputed by the model* rather than persisted. A
  stored `source_priority` would be a second `SOURCE_PRIORITY` table; a stored
  `is_proof` would be a second definition of proof.

- **Never re-derive an id.** `ev_`, `rf_`, `rsk_`, `claim_`, `entity_`, `src_`
  and `inv_` ids are all content digests minted by earlier phases. Re-running any
  of those derivations here would be a second implementation that could disagree
  with the first, and a disagreement would mean an id in a stored investigation
  no longer matches the id a reader recomputes. They are written and read back.

- **Order is data.** The state carries *tuples*, and Phase 5's evidence ordering
  is a deterministic function the database cannot reproduce. Every ordered
  collection therefore carries an explicit `sequence` column.

- **One transaction, or nothing.** A run is stored whole or not at all. A
  half-written investigation that reports `COMPLETED` would be worse than one
  that was never written, because the API would serve it as if it were real.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.adapters import investigation_status
from app.core.logging import get_logger
from app.graph.state import (
    GraphError,
    GraphWarning,
    InvestigationState,
    TimelineEvent,
)
from app.models.investigation import (
    ClaimEntityLinkRow,
    ClaimRow,
    EntityRow,
    EvidenceItemRow,
    EvidenceResponseRow,
    InvestigationErrorRow,
    InvestigationRow,
    InvestigationWarningRow,
    RedFlagRow,
    RiskAssessmentRow,
    RiskFactorRow,
    SourceRow,
    TimelineEventRow,
    VerificationResultRow,
)
from app.schemas.claims import Claim
from app.schemas.common import EvidenceSpan
from app.schemas.entities import Entity
from app.schemas.evidence import (
    EvidenceItem,
    EvidenceResponse,
    EvidenceSource,
)
from app.schemas.extraction import ClaimEntityLink, ExtractionResult
from app.schemas.ocr import ImageSource
from app.schemas.pdf import PdfSource
from app.schemas.red_flags import RedFlag
from app.schemas.risk import RiskAssessment, RiskFactor
from app.schemas.url import UrlSource
from app.schemas.verification import VerificationResponse, VerificationResult
__all__ = ["InvestigationRepository", "InvestigationSummary", "NotFound"]

logger = get_logger(__name__)


class NotFound(Exception):
    """No stored investigation carries the requested public id."""


@dataclass(frozen=True)
class InvestigationSummary:
    """One row of the history endpoint.

    A summary, not an investigation. It answers "what did I run, and did it
    work?" without loading twelve tables per row — which is why the counts are
    denormalised onto `investigations` rather than aggregated per request.

    Attributes:
        investigation_id: The Phase 7 public id.
        status: Persisted run status.
        input_type: What kind of input was submitted.
        current_stage: The last stage that ran.
        started_at, completed_at: Run timing, both stored.
        created_at: When the row was written. This, not `started_at`, is what the
            history is ordered by: two runs of the same content share a public id
            and may start in either order relative to when they finished.
        counts: The per-kind object counts.
    """

    investigation_id: str
    status: str
    input_type: str
    current_stage: str
    started_at: Any
    completed_at: Any
    created_at: Any
    claim_count: int
    entity_count: int
    red_flag_count: int
    verification_count: int
    source_count: int
    evidence_count: int
    factor_count: int


def _enum_value(value: Any) -> str:
    """Return the stored form of an enum member, or the value unchanged.

    Args:
        value: An enum member, a plain string, or `None`.

    Returns:
        The `.value` for an enum member, the string itself, or `""` for `None`.
    """
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def _as_tuple(value: Any) -> tuple[Any, ...]:
    """Return an iterable as a tuple, treating `None` as empty.

    Args:
        value: Any state value that may be absent or already a sequence.

    Returns:
        A tuple, so the loader and the writer agree on one shape.
    """
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    return tuple(value)


def _source_metadata_for(state: InvestigationState) -> dict[str, Any] | None:
    """Render a URL submission's provenance for storage, or `None`.

    Phase 12 stores provenance as one JSON blob rather than a set of columns,
    because nothing joins to it, filters on it or aggregates it — it is only ever
    read whole and shown beside the investigation it belongs to.

    Args:
        state: The finished state.

    Returns:
        The `UrlSource` rendered in JSON mode, or `None` for a run that had no
        URL. Absent is stored as SQL `NULL` rather than an empty object so a
        `TEXT` run is distinguishable from a URL run that somehow had none.
    """
    source = state.get("url_source")
    if source is None:
        return None
    payload = source.model_dump(mode="json")
    return _model_fields(type(source), payload)


def _image_metadata_for(state: InvestigationState) -> dict[str, Any] | None:
    """Render an image submission's provenance for storage, or `None`.

    The image modality's answer to :func:`_source_metadata_for`, for the
    same reason and in the same form: one JSON blob, because nothing
    joins to it, filters on it or aggregates it. It lives in its own
    `image_metadata` column rather than sharing `source_metadata`,
    because the two provenance records are different models and a run
    carries at most one of them.

    Args:
        state: The finished state.

    Returns:
        The `ImageSource` rendered in JSON mode, or `None` for a run
        that had no image. Absent is stored as SQL `NULL` rather than
        an empty object, so a `TEXT` or URL run is distinguishable from
        an image run that somehow had none.
    """
    source = state.get("image_source")
    if source is None:
        return None
    payload = source.model_dump(mode="json")
    return _model_fields(type(source), payload)


def _pdf_metadata_for(state: InvestigationState) -> dict[str, Any] | None:
    """Render a PDF submission's provenance for storage, or `None`.

    The PDF modality's answer to :func:`_image_metadata_for`, for
    the same reason and in the same form: one JSON blob, because
    nothing joins to it, filters on it or aggregates it. It lives in
    its own `pdf_metadata` column rather than sharing
    `source_metadata`, because the provenance records are different
    models and a run carries at most one of them.

    Args:
        state: The finished state.

    Returns:
        The `PdfSource` rendered in JSON mode, or `None` for a run
        that had no PDF. Absent is stored as SQL `NULL` rather than
        an empty object, so a `TEXT` or URL run is distinguishable
        from a PDF run that somehow had none.
    """
    source = state.get("pdf_source")
    if source is None:
        return None
    payload = source.model_dump(mode="json")
    return _model_fields(type(source), payload)


def _model_fields(model: type, payload: dict[str, Any]) -> dict[str, Any]:
    """Drop keys a Pydantic model does not declare.

    Used for every JSON bag. Rejecting unknown keys instead would turn a schema
    change into a read failure on historical rows, and silently keeping them
    would store columns nothing reads. Pydantic's own `extra` configuration
    decides which of those two is right per model, and this helper lets it.

    Args:
        model: The Pydantic model the payload will be validated against.
        payload: Candidate field values.

    Returns:
        Only the keys the model declares.
    """
    allowed = set(model.model_fields)
    return {key: value for key, value in payload.items() if key in allowed}


class InvestigationRepository:
    """Reads and writes whole investigations.

    The class holds no engine and no session of its own. It is constructed per
    request around the session the dependency provided, which keeps the
    `app.state` injection that Phase 8 established intact and makes the
    repository trivially testable against a temporary SQLite file.
    """

    def __init__(self, session: Session) -> None:
        """Bind the repository to one session.

        Args:
            session: A request-scoped :class:`~sqlalchemy.orm.Session`. The
                repository never commits on its own behalf of the caller except
                inside :meth:`save`, which is the single write path.
        """
        self._session = session

    @property
    def session(self) -> Session:
        """The session this repository was built around."""
        return self._session

    def commit(self) -> None:
        """Commit the current transaction.

        Exposed rather than left to callers to reach the session for, so the
        transaction boundary is stated at the call site that owns it. The write
        path is a single explicit commit after a whole run has been assembled.
        """
        self._session.commit()

    def rollback(self) -> None:
        """Discard the current transaction.

        Called on a failed write. Rolling back is what makes "stored whole or
        not at all" true: without it a constraint violation partway through a run
        would leave the rows that did insert visible to the next read.
        """
        self._session.rollback()

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def save(self, state: InvestigationState) -> InvestigationRow:
        """Persist one finished investigation in a single transaction.

        A new row is always written, even when a row with the same public id
        already exists. `investigation_id_for` is a digest of the *submitted
        content*, so re-running the same text is a genuine second event, and
        overwriting the first would erase the only evidence that the
        investigation was ever run twice (D-040).

        Args:
            state: The final state returned by `run_investigation`.

        Returns:
            The stored :class:`~app.models.investigation.InvestigationRow`,
            flushed so its surrogate key and `created_at` are populated.

        Raises:
            IntegrityError: The state violated a uniqueness rule. Left to
                propagate so a partial write cannot be mistaken for a success.
        """
        row = InvestigationRow(
            public_id=str(state.get("investigation_id") or ""),
            content_hash=str(state.get("investigation_id") or ""),
            input_type=_enum_value(state.get("input_type")),
            raw_input=str(state.get("raw_input") or ""),
            extracted_text=str(state.get("extracted_text") or ""),
            language="en",
            source_metadata=_source_metadata_for(state),
            image_metadata=_image_metadata_for(state),
            pdf_metadata=_pdf_metadata_for(state),
            status=investigation_status(
                _as_tuple(state.get("errors")), _as_tuple(state.get("timeline"))
            ).value,
            current_stage=str(state.get("current_stage") or ""),
            extraction_mode=_enum_value(state.get("extraction_mode")) or None,
            started_at=state.get("started_at"),
            completed_at=state.get("completed_at"),
        )

        extraction = state.get("extraction")
        if isinstance(extraction, ExtractionResult):
            row.prompt_version = extraction.prompt_version or None
            row.model_name = extraction.model or None
            row.extraction_source_text = extraction.source_text
            row.extraction_normalized_text = extraction.normalized_text
            row.extraction_warnings = list(extraction.processing_warnings)

        batch_verification = state.get("verification")
        if isinstance(batch_verification, VerificationResponse):
            row.verification_warnings = list(batch_verification.warnings)

        claims = _as_tuple(state.get("claims"))
        entities = _as_tuple(state.get("entities"))
        red_flags = _as_tuple(state.get("red_flags"))
        verifications = _as_tuple(state.get("verification_results"))
        evidence = _as_tuple(state.get("evidence"))
        warnings = _as_tuple(state.get("warnings"))
        errors = _as_tuple(state.get("errors"))
        timeline = _as_tuple(state.get("timeline"))
        links = _as_tuple(state.get("claim_entity_links"))

        row.claim_count = len(claims)
        row.entity_count = len(entities)
        row.red_flag_count = len(red_flags)
        row.verification_count = len(verifications)
        row.source_count = sum(len(response.sources) for response in evidence)
        row.evidence_count = sum(len(response.evidence) for response in evidence)
        assessment = state.get("risk_assessment")
        row.factor_count = len(assessment.factors) if isinstance(assessment, RiskAssessment) else 0

        self._session.add(row)

        for position, claim in enumerate(claims):
            row.claims.append(self._claim_row(claim, position))
        for position, entity in enumerate(entities):
            row.entities.append(self._entity_row(entity, position))
        for position, link in enumerate(links):
            row.claim_entity_links.append(self._link_row(link, position))
        for position, flag in enumerate(red_flags):
            row.red_flags.append(self._red_flag_row(flag, position))
        for position, result in enumerate(verifications):
            row.verification_results.append(self._verification_row(result, position))
        for position, warning in enumerate(warnings):
            row.warnings.append(self._warning_row(warning, position))
        for position, error in enumerate(errors):
            row.errors.append(self._error_row(error, position))
        for position, event in enumerate(timeline):
            row.timeline.append(self._timeline_row(event, position))

        self._write_evidence(row, evidence)

        if isinstance(assessment, RiskAssessment):
            row.risk_assessment = self._assessment_row(assessment)
            for position, factor in enumerate(assessment.factors):
                row.risk_factors.append(self._factor_row(factor, position))

        self._session.flush()
        return row

    def _claim_row(self, claim: Claim, position: int) -> ClaimRow:
        """Map one Phase 2 claim to a row."""
        return ClaimRow(
            sequence=position,
            claim_id=claim.id,
            claim_text=claim.text,
            claim_type=_enum_value(claim.claim_type),
            confidence=claim.confidence,
            span_start=claim.evidence_span.start,
            span_end=claim.evidence_span.end,
            span_text=claim.evidence_span.text,
            entity_ids=list(claim.entity_ids),
            is_complete_sentence=claim.is_complete_sentence,
            signals=list(claim.signals),
            metadata_=dict(claim.metadata),
        )

    def _entity_row(self, entity: Entity, position: int) -> EntityRow:
        """Map one Phase 2 entity to a row."""
        return EntityRow(
            sequence=position,
            entity_id=entity.id,
            name=entity.name,
            entity_type=_enum_value(entity.entity_type),
            normalized_name=entity.normalized_name,
            confidence=entity.confidence,
            span_start=entity.evidence_span.start,
            span_end=entity.evidence_span.end,
            span_text=entity.evidence_span.text,
            metadata_=dict(entity.metadata),
        )

    def _link_row(self, link: ClaimEntityLink, position: int) -> ClaimEntityLinkRow:
        """Map one Phase 2 claim-to-entity relationship to a row.

        The link table is written from `state["claim_entity_links"]`, which the
        graph populates from `ExtractionResult.relationships`. It is never
        reconstructed from `Claim.entity_ids`, because doing so would invent a
        `MENTIONS` relationship for every pair the extractor happened to record
        with a different verb.
        """
        return ClaimEntityLinkRow(
            sequence=position,
            claim_id=link.claim_id,
            entity_id=link.entity_id,
            relationship_type=_enum_value(link.relationship),
        )

    def _red_flag_row(self, flag: RedFlag, position: int) -> RedFlagRow:
        """Map one Phase 1 red flag to a row.

        `span_start`, `span_end` and `matched_text` are stored because
        `red_flag_id_for` hashes exactly those three values. A flag row missing
        any of them could not be assigned the id Phase 6 cited.
        """
        return RedFlagRow(
            sequence=position,
            code=_enum_value(flag.code),
            name=flag.name,
            description=flag.description,
            severity=_enum_value(flag.severity),
            weight=flag.weight,
            matched_text=flag.matched_text,
            span_start=flag.evidence_span.start,
            span_end=flag.evidence_span.end,
            span_text=flag.evidence_span.text,
            rule_reason=flag.rule_reason,
            additional_spans=[
                {"start": span.start, "end": span.end, "text": span.text}
                for span in flag.additional_spans
            ],
        )

    def _verification_row(
        self, result: VerificationResult, position: int
    ) -> VerificationResultRow:
        """Map one Phase 4 verification result to a row."""
        return VerificationResultRow(
            sequence=position,
            claim_id=result.claim_id,
            claim_type=_enum_value(result.claim_type),
            status=_enum_value(result.status),
            reason=result.reason,
            reason_code=result.reason_code,
            confidence=result.confidence,
            source_ids=list(result.source_ids),
            matched_result_ids=list(result.matched_result_ids),
            queries=list(result.queries),
            warnings=list(result.warnings),
        )

    def _write_evidence(
        self, row: InvestigationRow, responses: tuple[EvidenceResponse, ...]
    ) -> None:
        """Write every evidence response, its items and their sources.

        Sources are collected across the whole investigation and written once,
        deduplicated on `(source_id, result_id)`. Two claims citing the same
        document *and* the same result share a row; two claims citing the same
        document under two different results do not, because their `ev_` ids
        differ and their provenance differs.
        """
        seen: dict[tuple[str, str], SourceRow] = {}

        for position, response in enumerate(responses):
            response_row = EvidenceResponseRow(
                sequence=position,
                claim_id=response.claim_id,
                verification_status=_enum_value(response.verification_status),
                verification_reason_code=response.verification_reason_code,
                source_refs_json=[
                    [source.source_id, source.result_id] for source in response.sources
                ],
                warnings=list(response.warnings),
                built_at=response.built_at,
            )
            row.evidence_responses.append(response_row)

            for item_position, item in enumerate(response.evidence):
                key = (item.source.source_id, item.source.result_id)
                if key not in seen:
                    source_row = self._source_row(item.source)
                    seen[key] = source_row
                    row.sources.append(source_row)
                response_row.items.append(
                    self._evidence_row(item, item_position, row)
                )

    def _source_row(self, source: EvidenceSource) -> SourceRow:
        """Map one Phase 5 `EvidenceSource` to a row."""
        return SourceRow(
            source_id=source.source_id,
            result_id=source.result_id,
            url=source.url,
            canonical_url=source.canonical_url,
            domain=source.domain,
            title=source.title,
            source_type=_enum_value(source.source_type),
            source_tier=_enum_value(source.source_tier),
            position=source.position,
            retrieved_at=source.retrieved_at,
        )

    def _evidence_row(
        self, item: EvidenceItem, position: int, investigation: InvestigationRow
    ) -> EvidenceItemRow:
        """Map one Phase 5 evidence item to a row.

        The parent is passed explicitly. An item reaches the `investigations`
        table through its response, and SQLAlchemy sets a foreign key only from
        the relationship that actually references it — appending the item to
        `response_row.items` populates `evidence_response_id` and leaves
        `investigation_id` null.
        """
        return EvidenceItemRow(
            investigation=investigation,
            sequence=position,
            evidence_id=item.id,
            claim_id=item.claim_id,
            source_id=item.source.source_id,
            result_id=item.source.result_id,
            claim_type=_enum_value(item.claim_type),
            verification_status=_enum_value(item.verification_status),
            evidence_type=_enum_value(item.evidence_type),
            relationship_=_enum_value(item.relationship),
            relevance=_enum_value(item.relevance),
            excerpt=item.excerpt,
            excerpt_origin=item.excerpt_origin,
            matched_cue=item.matched_cue,
            provider_query=item.provider_query,
        )

    def _assessment_row(self, assessment: RiskAssessment) -> RiskAssessmentRow:
        """Map one Phase 6 risk assessment to a row.

        The caveat is stored verbatim. It is the string that stops `risk_score`
        being read as a probability, so paraphrasing or dropping it on the way
        into the database would undo D-025 the moment an assessment was read back
        from a stored run.
        """
        return RiskAssessmentRow(
            risk_score=assessment.risk_score,
            raw_score=assessment.raw_score,
            risk_level=_enum_value(assessment.risk_level),
            relevant_claims=assessment.relevant_claims,
            assessed_claims=assessment.assessed_claims,
            evidence_coverage=assessment.evidence_coverage,
            analysis_completeness=assessment.analysis_completeness,
            weights_json=assessment.weights.model_dump(mode="json"),
            thresholds_json=assessment.thresholds.model_dump(mode="json"),
            warnings_json=list(assessment.warnings),
            assessed_at=assessment.assessed_at,
        )

    def _factor_row(self, factor: RiskFactor, position: int) -> RiskFactorRow:
        """Map one Phase 6 risk factor to a row.

        De-duplicated factors are stored, not filtered. They contribute zero and
        name the factor that absorbed them; dropping them would conceal that a
        claim was contradicted, which is the failure D-026 exists to prevent.
        """
        return RiskFactorRow(
            factor_id=factor.id,
            sequence=position,
            origin=_enum_value(factor.origin),
            factor_type=_enum_value(factor.factor_type),
            label=factor.label,
            description=factor.description,
            reason=factor.reason,
            source=factor.source,
            severity=_enum_value(factor.severity),
            weight=factor.weight,
            contribution=factor.contribution,
            absorbed_into=factor.absorbed_into,
            claim_ids=list(factor.claim_ids),
            red_flag_ids=list(factor.red_flag_ids),
            evidence_ids=list(factor.evidence_ids),
            source_ids=list(factor.source_ids),
            verification_statuses=[_enum_value(item) for item in factor.verification_statuses],
            is_uncertainty=factor.is_uncertainty,
        )

    def _timeline_row(self, event: TimelineEvent, position: int) -> TimelineEventRow:
        """Map one Phase 7 timeline event to a row."""
        return TimelineEventRow(
            sequence=position,
            stage=_enum_value(event.stage),
            status=_enum_value(event.status),
            message=event.message,
            at=event.at,
        )

    def _warning_row(self, warning: GraphWarning, position: int) -> InvestigationWarningRow:
        """Map one Phase 7 warning to a row.

        `error_type` is dropped. A warning is something a client may read, and
        Phase 8 already established that the log-side diagnostic does not cross
        that boundary; storing it would put it one round trip from coming back.
        """
        return InvestigationWarningRow(
            sequence=position,
            code=warning.code,
            stage=_enum_value(warning.stage),
            message=warning.message,
        )

    def _error_row(self, error: GraphError, position: int) -> InvestigationErrorRow:
        """Map one Phase 7 error to a row.

        `error_type` is kept — it diagnoses our own defect and an exception class
        name is not a secret. Nothing else from the exception is: no message, no
        stack, no DSN.
        """
        return InvestigationErrorRow(
            sequence=position,
            code=error.code,
            stage=_enum_value(error.stage),
            message=error.message,
            error_type=error.error_type,
        )

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def load(self, public_id: str) -> InvestigationState:
        """Rebuild the full `InvestigationState` for one stored investigation.

        The most recently stored run wins. `investigation_id_for` is a content
        digest, so a re-run of the same text shares a public id; returning the
        newest row is what makes `GET /api/investigations/{id}` agree with the
        `POST` the client just made. The full run history remains available
        through :meth:`list_page`.

        Args:
            public_id: The Phase 7 `inv_` id a client already holds.

        Returns:
            A state ready to hand to `serialize_investigation` unchanged.

        Raises:
            NotFound: No stored run carries that public id.
        """
        row = self._latest(public_id)
        if row is None:
            raise NotFound(public_id)
        return self._to_state(row)

    def find(self, public_id: str) -> InvestigationState | None:
        """Like :meth:`load`, but return `None` instead of raising.

        Args:
            public_id: The Phase 7 `inv_` id to look for.

        Returns:
            The reconstructed state, or `None` when nothing is stored.
        """
        try:
            return self.load(public_id)
        except NotFound:
            return None

    def _latest(self, public_id: str) -> InvestigationRow | None:
        """Return the newest stored row for a public id, or `None`."""
        statement = (
            select(InvestigationRow)
            .where(InvestigationRow.public_id == public_id)
            .order_by(InvestigationRow.created_at.desc(), InvestigationRow.id.desc())
        )
        return self._session.scalars(statement).first()

    def _to_state(self, row: InvestigationRow) -> InvestigationState:
        """Rehydrate one row graph into the state the graph would have produced.

        Sources are resolved from a single pass over the run's `sources` rows so
        that an evidence item's `source` is the exact result it cited, and an
        evidence response's `sources` tuple is rebuilt in the stored order.
        """
        sources: dict[tuple[str, str], EvidenceSource] = {
            (source.source_id, source.result_id): _source_from_row(source)
            for source in row.sources
        }

        items_by_response: dict[int, list[EvidenceItem]] = {}
        for item in row.evidence_items:
            source = sources[(item.source_id, item.result_id)]
            items_by_response.setdefault(item.evidence_response_id, []).append(
                _evidence_from_row(item, source)
            )

        evidence: list[EvidenceResponse] = []
        for response in row.evidence_responses:
            evidence.append(
                EvidenceResponse(
                    claim_id=response.claim_id,
                    verification_status=response.verification_status,  # type: ignore[arg-type]
                    verification_reason_code=response.verification_reason_code,
                    evidence=tuple(items_by_response.get(response.id, ())),
                    sources=tuple(
                        sources[(ref[0], ref[1])]
                        for ref in response.source_refs_json
                        if (ref[0], ref[1]) in sources
                    ),
                    warnings=tuple(response.warnings),
                    built_at=response.built_at,
                )
            )

        state: InvestigationState = {
            "input_type": row.input_type,
            "raw_input": row.raw_input,
            "extracted_text": row.extracted_text,
            "investigation_id": row.public_id,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
            "current_stage": row.current_stage,
            "claims": tuple(_claim_from_row(claim) for claim in row.claims),
            "entities": tuple(_entity_from_row(entity) for entity in row.entities),
            "claim_entity_links": tuple(_link_from_row(link) for link in row.claim_entity_links),
            "extraction_mode": row.extraction_mode or None,  # type: ignore[typeddict-item]
            "red_flags": tuple(_red_flag_from_row(flag) for flag in row.red_flags),
            "verification_results": tuple(
                _verification_from_row(result) for result in row.verification_results
            ),
            "evidence": tuple(evidence),
            "risk_assessment": _assessment_from_row(row.risk_assessment, row.risk_factors),
            "warnings": tuple(_warning_from_row(warning) for warning in row.warnings),
            "errors": tuple(_error_from_row(error) for error in row.errors),
            "timeline": tuple(_timeline_from_row(event) for event in row.timeline),
        }

        extraction = _extraction_from_row(row, state)
        if extraction is not None:
            state["extraction"] = extraction
        batch = _verification_response_from_row(row, row.verification_results)
        if batch is not None:
            state["verification"] = batch

        # Added only when the row carries one, so a `TEXT` run reloads with
        # exactly the keys it was stored with. Writing `url_source: None`
        # unconditionally would give every reloaded state a key the live state
        # never had, and the determinism test that compares a live run with its
        # reloaded twin would rightly fail on that alone.
        url_source = _url_source_from_row(row)
        if url_source is not None:
            state["url_source"] = url_source

        # The same conditional discipline for the image modality: a URL
        # or TEXT run reloads with no `image_source` key at all, so a
        # reloaded image run is the only state that carries one.
        image_source = _image_source_from_row(row)
        if image_source is not None:
            state["image_source"] = image_source

        # And for the PDF modality: only a reloaded PDF run carries a
        # `pdf_source`, for the same reason.
        pdf_source = _pdf_source_from_row(row)
        if pdf_source is not None:
            state["pdf_source"] = pdf_source

        return state

    def list_page(self, limit: int, offset: int = 0) -> tuple[list[InvestigationSummary], int]:
        """Return one page of run history, newest first, plus the total.

        Ordered by `created_at` rather than `started_at`, and by `id` as a
        tie-break: two runs of identical content started in the same millisecond
        have identical `started_at` and identical `public_id`, and a stable
        ordering is what stops a client paging through a history from seeing the
        same row twice or skipping one.

        Only the `investigations` table is read. The child counts are
        denormalised columns precisely so this endpoint does not aggregate ten
        tables per row, which is what makes "show me everything I ran" a cheap
        query instead of a report.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip, for paging.

        Returns:
            The page of summaries and the total number of stored runs.
        """
        total = self._session.scalar(select(func.count()).select_from(InvestigationRow)) or 0
        statement = (
            select(InvestigationRow)
            .order_by(InvestigationRow.created_at.desc(), InvestigationRow.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = self._session.scalars(statement).all()
        return [_summary_from_row(row) for row in rows], int(total)

    def delete(self, public_id: str) -> bool:
        """Delete every run stored under a public id.

        Deletes all of them, not the newest: a public id is a content digest, so
        removing "the" investigation for a piece of content while leaving its
        re-runs behind would make the id retrievable again with no way to ask for
        that. Child rows go with it through `ON DELETE CASCADE` where the
        backend enforces it and through ORM cascade collection where it does
        not, so a partial tree is never left behind.

        Args:
            public_id: The Phase 7 `inv_` id to remove.

        Returns:
            `True` when at least one run was deleted.
        """
        statement = select(InvestigationRow).where(InvestigationRow.public_id == public_id)
        rows = self._session.scalars(statement).all()
        if not rows:
            return False
        for row in rows:
            self._session.delete(row)
        self._session.flush()
        return True


# ----------------------------------------------------------------------
# Row -> model helpers
#
# Each takes a stored row and rebuilds the Phase 1-6 object it came from by
# handing the column values straight to the original model. Enum fields are
# passed as their stored strings and coerced by Pydantic, so there is exactly
# one place where a `RedFlagCode` becomes a `RedFlagCode`.
# ----------------------------------------------------------------------


def _summary_from_row(row: InvestigationRow) -> InvestigationSummary:
    """Build a history-entry summary from a stored row."""
    return InvestigationSummary(
        investigation_id=row.public_id,
        status=row.status,
        input_type=row.input_type,
        current_stage=row.current_stage,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        claim_count=row.claim_count,
        entity_count=row.entity_count,
        red_flag_count=row.red_flag_count,
        verification_count=row.verification_count,
        source_count=row.source_count,
        evidence_count=row.evidence_count,
        factor_count=row.factor_count,
    )


def _span(start: int, end: int, text: str) -> EvidenceSpan:
    """Rebuild a Phase 1 `EvidenceSpan` from stored offsets."""
    return EvidenceSpan(start=start, end=end, text=text)


def _claim_from_row(row: ClaimRow) -> Claim:
    """Rebuild a Phase 2 claim."""
    return Claim(
        id=row.claim_id,
        text=row.claim_text,
        claim_type=row.claim_type,  # type: ignore[arg-type]
        confidence=row.confidence,
        evidence_span=_span(row.span_start, row.span_end, row.span_text),
        entity_ids=tuple(row.entity_ids),
        is_complete_sentence=row.is_complete_sentence,
        signals=tuple(row.signals),
        metadata=dict(row.metadata_),
    )


def _entity_from_row(row: EntityRow) -> Entity:
    """Rebuild a Phase 2 entity."""
    return Entity(
        id=row.entity_id,
        name=row.name,
        entity_type=row.entity_type,  # type: ignore[arg-type]
        normalized_name=row.normalized_name,
        confidence=row.confidence,
        evidence_span=_span(row.span_start, row.span_end, row.span_text),
        metadata=dict(row.metadata_),
    )


def _link_from_row(row: ClaimEntityLinkRow) -> ClaimEntityLink:
    """Rebuild a Phase 2 claim-to-entity relationship."""
    return ClaimEntityLink(
        claim_id=row.claim_id,
        entity_id=row.entity_id,
        relationship=row.relationship_type,  # type: ignore[arg-type]
    )


def _red_flag_from_row(row: RedFlagRow) -> RedFlag:
    """Rebuild a Phase 1 red flag."""
    return RedFlag(
        code=row.code,  # type: ignore[arg-type]
        name=row.name,
        description=row.description,
        severity=row.severity,  # type: ignore[arg-type]
        weight=row.weight,
        matched_text=row.matched_text,
        evidence_span=_span(row.span_start, row.span_end, row.span_text),
        rule_reason=row.rule_reason,
        additional_spans=tuple(
            _span(item["start"], item["end"], item["text"]) for item in row.additional_spans
        ),
    )


def _verification_from_row(row: VerificationResultRow) -> VerificationResult:
    """Rebuild a Phase 4 verification result.

    `status` is the decision Phase 4 recorded, returned as stored. It is never
    recomputed from the documents now in the database: the comparison that
    produced it happened against a live search, and re-running it here would be
    a second implementation that could disagree (D-020).
    """
    return VerificationResult(
        claim_id=row.claim_id,
        claim_type=row.claim_type,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        reason=row.reason,
        reason_code=row.reason_code,
        confidence=row.confidence,
        source_ids=tuple(row.source_ids),
        matched_result_ids=tuple(row.matched_result_ids),
        queries=tuple(row.queries),
        warnings=tuple(row.warnings),
    )


def _url_source_from_row(row: InvestigationRow) -> UrlSource | None:
    """Rebuild a URL submission's provenance from its stored JSON.

    Returns `None` in three cases that are all normal rather than exceptional:
    a `TEXT` run, which never had one; a run stored before Phase 12, whose row
    predates the column; and a row whose JSON no longer validates against the
    current `UrlSource`.

    That last case is why this returns `None` rather than raising. Provenance is
    descriptive metadata about how a page was fetched — it is not the
    investigation. A row written by an older version of this schema must still
    reload and still serve its claims, evidence and risk assessment; failing the
    whole retrieval because one descriptive field drifted would turn a cosmetic
    schema change into losing access to a stored investigation.

    Args:
        row: The stored run.

    Returns:
        The `UrlSource`, or `None` when the run carries none this version can
        read.
    """
    payload = getattr(row, "source_metadata", None)
    if not isinstance(payload, dict):
        return None
    try:
        return UrlSource.model_validate(payload)
    except ValidationError as exc:
        logger.warning(
            "Stored URL provenance did not match the current schema",
            extra={"public_id": row.public_id, "reason": type(exc).__name__},
        )
        return None


def _image_source_from_row(row: InvestigationRow) -> ImageSource | None:
    """Rebuild an image submission's provenance from its stored JSON.

    The image modality's answer to :func:`_url_source_from_row`, with the
    same contract and the same three normal `None` cases: a run of any
    other input kind, which never had one; a run stored before Phase 13,
    whose row predates the column; and a row whose JSON no longer
    validates against the current `ImageSource`.

    Provenance describes how an image was read, not what the investigation
    found, so a row written by an older schema must still reload and still
    serve its claims, evidence and risk assessment. Failing the whole
    retrieval because one descriptive field drifted would turn a cosmetic
    schema change into losing access to a stored investigation.

    Args:
        row: The stored run.

    Returns:
        The `ImageSource`, or `None` when the run carries none this
        version can read.
    """
    payload = getattr(row, "image_metadata", None)
    if not isinstance(payload, dict):
        return None
    try:
        return ImageSource.model_validate(payload)
    except ValidationError as exc:
        logger.warning(
            "Stored image provenance did not match the current schema",
            extra={"public_id": row.public_id, "reason": type(exc).__name__},
        )
        return None


def _pdf_source_from_row(row: InvestigationRow) -> PdfSource | None:
    """Rebuild a PDF submission's provenance from its stored JSON.

    The PDF modality's answer to :func:`_image_source_from_row`, with
    the same contract and the same three normal `None` cases: a run of
    any other input kind, which never had one; a run stored before
    Phase 14, whose row predates the column; and a row whose JSON no
    longer validates against the current `PdfSource`.

    Provenance describes how a PDF was read, not what the investigation
    found, so a row written by an older schema must still reload and
    still serve its claims, evidence and risk assessment. Failing the
    whole retrieval because one descriptive field drifted would turn a
    cosmetic schema change into losing access to a stored investigation.

    Args:
        row: The stored run.

    Returns:
        The `PdfSource`, or `None` when the run carries none this
        version can read.
    """
    payload = getattr(row, "pdf_metadata", None)
    if not isinstance(payload, dict):
        return None
    try:
        return PdfSource.model_validate(payload)
    except ValidationError as exc:
        logger.warning(
            "Stored PDF provenance did not match the current schema",
            extra={"public_id": row.public_id, "reason": type(exc).__name__},
        )
        return None


def _source_from_row(row: SourceRow) -> EvidenceSource:
    """Rebuild a Phase 5 `EvidenceSource`.

    `source_priority` and `is_authoritative` are not read from storage: they are
    computed fields, and the models recompute them from `source_type` and
    `source_tier` exactly as they did on the way in.
    """
    return EvidenceSource(
        source_id=row.source_id,
        result_id=row.result_id,
        url=row.url,
        canonical_url=row.canonical_url,
        domain=row.domain,
        title=row.title,
        source_type=row.source_type,  # type: ignore[arg-type]
        source_tier=row.source_tier,  # type: ignore[arg-type]
        retrieved_at=row.retrieved_at,
        position=row.position,
    )


def _evidence_from_row(row: EvidenceItemRow, source: EvidenceSource) -> EvidenceItem:
    """Rebuild a Phase 5 evidence item against the source it cited."""
    return EvidenceItem(
        id=row.evidence_id,
        claim_id=row.claim_id,
        verification_status=row.verification_status,  # type: ignore[arg-type]
        claim_type=row.claim_type,  # type: ignore[arg-type]
        evidence_type=row.evidence_type,  # type: ignore[arg-type]
        relationship=row.relationship_,  # type: ignore[arg-type]
        relevance=row.relevance,  # type: ignore[arg-type]
        excerpt=row.excerpt,
        excerpt_origin=row.excerpt_origin,
        source=source,
        matched_cue=row.matched_cue,
        provider_query=row.provider_query,
    )


def _assessment_from_row(
    row: RiskAssessmentRow | None, factors: Sequence[RiskFactorRow]
) -> RiskAssessment | None:
    """Rebuild a Phase 6 risk assessment, or `None` when there was none.

    A run that stopped before Phase 6 has no assessment, and that is reported as
    the absence it is rather than as a zero score.

    The factors are passed in rather than reached for through the ORM back
    reference, so the ordering is the one the loader already established
    (`row.risk_factors`, ordered by `sequence`) and not whatever order a
    relationship happens to produce.

    Rebuilding through the model is what restores the caveat. `RiskAssessment`
    appends `SCORE_NOT_A_PROBABILITY` to `warnings` during validation, so an
    assessment rehydrated from storage carries it whether or not the stored
    warnings list still contains it.
    """
    if row is None:
        return None
    from app.schemas.risk import RiskThresholds, RiskWeights

    return RiskAssessment(
        risk_score=row.risk_score,
        raw_score=row.raw_score,
        risk_level=row.risk_level,  # type: ignore[arg-type]
        weights=RiskWeights(**_model_fields(RiskWeights, row.weights_json)),
        thresholds=RiskThresholds(**_model_fields(RiskThresholds, row.thresholds_json)),
        factors=tuple(_factor_from_row(factor) for factor in factors),
        relevant_claims=row.relevant_claims,
        assessed_claims=row.assessed_claims,
        evidence_coverage=row.evidence_coverage,
        analysis_completeness=row.analysis_completeness,
        warnings=tuple(row.warnings_json),
        assessed_at=row.assessed_at,
    )


def _factor_from_row(row: RiskFactorRow) -> RiskFactor:
    """Rebuild a Phase 6 risk factor."""
    return RiskFactor(
        id=row.factor_id,
        origin=row.origin,  # type: ignore[arg-type]
        factor_type=row.factor_type,  # type: ignore[arg-type]
        label=row.label,
        description=row.description,
        reason=row.reason,
        source=row.source,
        severity=row.severity,  # type: ignore[arg-type]
        weight=row.weight,
        contribution=row.contribution,
        absorbed_into=row.absorbed_into,
        claim_ids=tuple(row.claim_ids),
        red_flag_ids=tuple(row.red_flag_ids),
        evidence_ids=tuple(row.evidence_ids),
        source_ids=tuple(row.source_ids),
        verification_statuses=tuple(row.verification_statuses),  # type: ignore[arg-type]
        is_uncertainty=row.is_uncertainty,
    )


def _timeline_from_row(row: TimelineEventRow) -> TimelineEvent:
    """Rebuild a Phase 7 timeline event."""
    return TimelineEvent(
        stage=row.stage,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        message=row.message,
        at=row.at,
    )


def _warning_from_row(row: InvestigationWarningRow) -> GraphWarning:
    """Rebuild a Phase 7 warning, with no `error_type`.

    The column does not exist, so a retrieved warning reports no exception class
    even where the live run had one. That asymmetry is deliberate and matches
    what Phase 8 already returns over HTTP: the diagnostic is a log concern.
    """
    return GraphWarning(
        code=row.code,
        stage=row.stage,  # type: ignore[arg-type]
        message=row.message,
        error_type=None,
    )


def _error_from_row(row: InvestigationErrorRow) -> GraphError:
    """Rebuild a Phase 7 error."""
    return GraphError(
        code=row.code,
        stage=row.stage,  # type: ignore[arg-type]
        message=row.message,
        error_type=row.error_type,
    )


def _extraction_from_row(
    row: InvestigationRow, state: InvestigationState
) -> ExtractionResult | None:
    """Rebuild the Phase 2 `ExtractionResult` the graph consumed.

    The graph stores Phase 2's result whole and Phase 4 consumes it directly, so
    a retrieved investigation must carry one too — otherwise a reloaded state
    would have claims but no record of the extraction they came from, and the
    `extraction_mode` recorded on the run would describe nothing.

    The claims, entities and relationships are the *same objects* the state
    already holds rather than a second copy. That is deliberate: Phase 2's result
    and the graph's `claims` key are the same findings, and duplicating them
    would make the two able to disagree after a round trip.

    `None` when the run never reached Phase 2, which is a real state for a
    submission the input node refused.
    """
    extraction_mode = state.get("extraction_mode")
    if extraction_mode is None:
        return None
    return ExtractionResult(
        claims=_as_tuple(state.get("claims")),
        entities=_as_tuple(state.get("entities")),
        relationships=_as_tuple(state.get("claim_entity_links")),
        extraction_mode=extraction_mode,
        prompt_version=row.prompt_version or None,
        model=row.model_name or None,
        source_text=row.extraction_source_text,
        normalized_text=row.extraction_normalized_text,
        processing_warnings=tuple(row.extraction_warnings),
    )


def _verification_response_from_row(
    row: InvestigationRow, results: Sequence[VerificationResultRow]
) -> VerificationResponse | None:
    """Rebuild Phase 4's batch `VerificationResponse`.

    The counts are not stored: `VerificationResponse` recomputes them from its
    results during validation, and a stored count that disagreed with the rows
    would be a second implementation of the same arithmetic.

    `None` when the run has no verification results at all, which is what a run
    that stopped before Phase 4 looks like — the same absence the graph reports
    for a stage that never ran.
    """
    if not results:
        return None
    return VerificationResponse(
        results=tuple(_verification_from_row(result) for result in results),
        warnings=tuple(row.verification_warnings),
    )
