"""SQLAlchemy models for one investigation and everything it produced (Phase 9).

The shape here is dictated by the Phase 1-6 schemas, not by the earlier sketch in
`docs/DATABASE_SCHEMA.md`. That document was written before those schemas
existed and several of its columns describe fields the pipeline does not have —
`claims.verification_required`, `entities.mentions`, a unique constraint on
`red_flags.code`, a `reports` table for a report layer that is Phase 10. Building
to the sketch would have meant inventing a second claim, a second entity and a
second risk assessment, each able to disagree with the model it was derived from.

Four rules shaped every table below.

- **Normalise what is referenced, JSON what is only read.** Claims, entities, red
  flags, verification results, sources, evidence, factors and timeline entries are
  rows: they are counted, filtered, joined to, and a reader must be able to walk
  from a risk factor to the claim and document behind it (D-026). Free-form bags
  like `Claim.metadata`, `RiskWeights` and the risk caveat are JSON columns, where
  a table would add joins without adding meaning.

- **Store the object, not a paraphrase of it.** Round-tripping a run must
  reproduce the Phase 1-6 models exactly, including their computed fields. That is
  why `red_flags` stores `span_start`/`span_end` and `matched_text`: Phase 6 mints
  its `rf_` id from precisely those three values, so dropping any of them would
  change the ids a reader could follow.

- **Deduplicate only where duplication is genuinely invisible.** A source seen by
  five claims is one row. The same rule firing on two different spans is two rows,
  because they are two findings with two ids.

- **Never persist anything a user might read that could carry a secret.** Every
  string here comes from a schema field or a fixed message table. There is no
  free-text exception column, no DSN column and no stack-trace column, because the
  safest place to enforce that is not having the column (D-024, and the same
  principle as Phase 8's `probe_database` fix).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import UtcDateTime, utc_now

__all__ = [
    "ClaimEntityLinkRow",
    "ClaimRow",
    "EntityRow",
    "EvidenceItemRow",
    "EvidenceResponseRow",
    "InvestigationErrorRow",
    "InvestigationRow",
    "InvestigationWarningRow",
    "RedFlagRow",
    "RiskAssessmentRow",
    "RiskFactorRow",
    "SourceRow",
    "TimelineEventRow",
    "VerificationResultRow",
]

#: Width for the domain identifiers minted by Phases 4, 5, 6 and 7 (`claim_`,
#: `ev_`, `rf_`, `rsk_`, `src_`, `res_`, `inv_`). Every id observed is 15-20
#: characters; 64 leaves room for a future scheme without silently truncating an
#: id and colliding two distinct findings.
_ID_WIDTH = 64

#: Width for a Phase 1/4/5 enum value, e.g. `TIER_1_PRIMARY_REGULATOR` or
#: `INSUFFICIENT_EVIDENCE`.
_ENUM_WIDTH = 40


class InvestigationRow(Base):
    """One investigation run.

    `id` is the primary key and the only unique identity in this schema.
    `public_id` is the Phase 7 content digest, which is deliberately **not**
    unique: submitting the same content twice produces two runs that are two real
    events, and overwriting the first would destroy the evidence that the
    investigation was ever run twice (D-040).

    Attributes:
        id: Surrogate key. Never exposed over the API.
        public_id: The Phase 7 `investigation_id` a client already holds.
        content_hash: The same digest, isolated so re-run detection and a later
            primary-key migration can use it without touching the API contract.
        status: The Phase 8 `InvestigationStatus`. Denormalised for the list
            endpoint; single-investigation retrieval re-derives it from the
            timeline so D-037's rule has exactly one implementation.
        claim_count and friends: Denormalised counts so the history endpoint does
            not have to aggregate twelve tables per row. Pinned to the child
            tables by a test, because a count that disagrees with its children is
            worse than no count at all.
    """

    __tablename__ = "investigations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(_ID_WIDTH), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(_ID_WIDTH), nullable=False, index=True)

    input_type: Mapped[str] = mapped_column(String(16), nullable=False)
    raw_input: Mapped[str] = mapped_column(Text, nullable=False, default="")
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")

    # Phase 12's `UrlSource` for a URL submission, as JSON, and `None` for every
    # other input kind.
    #
    # JSON rather than columns because it is provenance metadata that is only
    # ever read as a whole: nothing joins to it, filters on it or aggregates it,
    # and the page title, resolved addresses and HTTP status have no use outside
    # being shown alongside the investigation. It is nullable because `TEXT` runs
    # simply do not have one, and a sentinel row would be a fiction.
    source_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Phase 13's `ImageSource` for an image submission, as JSON, and
    # `None` for every other input kind. A column of its own rather
    # than a share of `source_metadata`, because the two provenance
    # records are different models with no common shape and a run is
    # a URL run or an image run, never both: one column each means the
    # loader validates each blob against the model it came from, with
    # nothing to disambiguate.
    image_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    current_stage: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    # Phase 2's `ExtractionResult`, denormalised. The graph stores the result
    # whole and Phase 4 consumes it directly, so a retrieved investigation has to
    # carry one too — a state with claims but no extraction would have an
    # `extraction_mode` describing nothing.
    #
    # `extraction_source_text` and `extraction_normalized_text` are both kept
    # even though `extracted_text` is usually one of them: the graph sets
    # `extracted_text = normalized_text or raw_input`, so it is the *fallback*,
    # not the value, and a run whose normalizer changed the text would otherwise
    # reload with the normalized string where the original belonged.
    extraction_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    extraction_source_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    extraction_normalized_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    extraction_warnings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Phase 4's batch-level `VerificationResponse.warnings`. The per-claim results
    # are rows in `verification_results`; these are the limitations that applied
    # to the batch as a whole, which no single result carries.
    verification_warnings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    claim_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    red_flag_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verification_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    factor_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    started_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utc_now, index=True
    )

    claims: Mapped[list["ClaimRow"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ClaimRow.sequence",
    )
    entities: Mapped[list["EntityRow"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="EntityRow.sequence",
    )
    claim_entity_links: Mapped[list["ClaimEntityLinkRow"]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan", lazy="selectin"
    )
    red_flags: Mapped[list["RedFlagRow"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="RedFlagRow.sequence",
    )
    verification_results: Mapped[list["VerificationResultRow"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="VerificationResultRow.sequence",
    )
    sources: Mapped[list["SourceRow"]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan", lazy="selectin"
    )
    evidence_responses: Mapped[list["EvidenceResponseRow"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="EvidenceResponseRow.sequence",
    )
    evidence_items: Mapped[list["EvidenceItemRow"]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan", lazy="selectin"
    )
    risk_assessment: Mapped["RiskAssessmentRow | None"] = relationship(
        back_populates="investigation", cascade="all, delete-orphan", lazy="selectin", uselist=False
    )
    risk_factors: Mapped[list["RiskFactorRow"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="RiskFactorRow.sequence",
    )
    timeline: Mapped[list["TimelineEventRow"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="TimelineEventRow.sequence",
    )
    warnings: Mapped[list["InvestigationWarningRow"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="InvestigationWarningRow.sequence",
    )
    errors: Mapped[list["InvestigationErrorRow"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="InvestigationErrorRow.sequence",
    )

    __table_args__ = (
        # Newest-first is the only ordering the history endpoint needs, and
        # `ix_investigations_status_created` serves the status-filtered variant.
        # The plain `created_at` index is already emitted by `index=True` above,
        # so no second one is declared here.
        Index("ix_investigations_status_created", "status", "created_at"),
    )


class ClaimRow(Base):
    """One extracted claim, stored as Phase 2's `Claim`.

    `entity_ids` is stored verbatim alongside the `claim_entity_links` rows
    because both exist in the Phase 2 schema. They agree today, and the link
    table is the queryable one; keeping the claim's own field means a run
    round-trips exactly rather than being silently reconciled on read.
    """

    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claim_id: Mapped[str] = mapped_column(String(_ID_WIDTH), nullable=False, index=True)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(_ENUM_WIDTH), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    span_start: Mapped[int] = mapped_column(Integer, nullable=False)
    span_end: Mapped[int] = mapped_column(Integer, nullable=False)
    span_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    entity_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_complete_sentence: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    signals: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)

    investigation: Mapped[InvestigationRow] = relationship(back_populates="claims")

    __table_args__ = (
        UniqueConstraint("investigation_id", "claim_id", name="uq_claims_investigation_claim"),
        # Phase 2 emits claims in source order and the API returns them that way.
        # `span_start` is *usually* that order but not guaranteed to be distinct,
        # so the position is recorded rather than inferred.
        Index("ix_claims_investigation_sequence", "investigation_id", "sequence"),
    )


class EntityRow(Base):
    """One extracted entity, stored as Phase 2's `Entity`.

    `metadata` is a JSON column because its values are heterogeneous by
    construction — `str | int | float | bool` — so a column per key would be an
    unbounded schema driven by extraction rather than by meaning.
    """

    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entity_id: Mapped[str] = mapped_column(String(_ID_WIDTH), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(_ENUM_WIDTH), nullable=False, index=True)
    normalized_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    span_start: Mapped[int] = mapped_column(Integer, nullable=False)
    span_end: Mapped[int] = mapped_column(Integer, nullable=False)
    span_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)

    investigation: Mapped[InvestigationRow] = relationship(back_populates="entities")

    __table_args__ = (
        UniqueConstraint("investigation_id", "entity_id", name="uq_entities_investigation_entity"),
        Index("ix_entities_investigation_sequence", "investigation_id", "sequence"),
    )


class ClaimEntityLinkRow(Base):
    """Phase 2's `ClaimEntityLink`, as a real relationship rather than strings.

    A table and not a comma-joined list because the question "which claims name
    this entity?" is the first thing a reader of an investigation asks, and
    answering it from a string would mean scanning every claim row in Python.
    """

    __tablename__ = "claim_entity_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claim_id: Mapped[str] = mapped_column(String(_ID_WIDTH), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(_ID_WIDTH), nullable=False, index=True)
    relationship_type: Mapped[str] = mapped_column(String(32), nullable=False, default="MENTIONS")

    investigation: Mapped[InvestigationRow] = relationship(back_populates="claim_entity_links")

    __table_args__ = (
        UniqueConstraint(
            "investigation_id",
            "claim_id",
            "entity_id",
            "relationship_type",
            name="uq_claim_entity_links_triple",
        ),
    )


class RedFlagRow(Base):
    """One Phase 1 red flag.

    Deliberately **not** unique on `(investigation_id, code)`. The earlier
    schema sketch said "one row per distinct indicator", which is wrong: Phase 1
    emits a separate `RedFlag` for each span a rule fires on, each with its own
    `rf_` id, and Phase 6 cites those ids individually in `RiskFactor`. Collapsing
    them onto one row would destroy findings the risk trace points at.

    `sequence` preserves the order the engine returned them in, which is
    deterministic (`sort_key`) but not sorted, and which the API returns.
    """

    __tablename__ = "red_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    code: Mapped[str] = mapped_column(String(_ENUM_WIDTH), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    matched_text: Mapped[str] = mapped_column(Text, nullable=False)
    span_start: Mapped[int] = mapped_column(Integer, nullable=False)
    span_end: Mapped[int] = mapped_column(Integer, nullable=False)
    span_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rule_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    additional_spans: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    investigation: Mapped[InvestigationRow] = relationship(back_populates="red_flags")

    __table_args__ = (
        # Span is part of the identity because `red_flag_id_for` hashes it. The
        # constraint is what makes two concurrent runs of the same content fail
        # loudly instead of writing a duplicate flag (D-042).
        UniqueConstraint(
            "investigation_id", "code", "span_start", "span_end", name="uq_red_flags_span"
        ),
        Index("ix_red_flags_investigation_sequence", "investigation_id", "sequence"),
    )


class VerificationResultRow(Base):
    """One Phase 4 `VerificationResult`.

    The decision is stored, never re-derived. `status` was chosen by Phase 4's
    decision engine from the documents it actually read; recomputing it here from
    the same rows would be a second implementation that could disagree, and a
    disagreement would read as "the database says this claim was verified" (D-020).
    """

    __tablename__ = "verification_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claim_id: Mapped[str] = mapped_column(String(_ID_WIDTH), nullable=False, index=True)
    claim_type: Mapped[str] = mapped_column(String(_ENUM_WIDTH), nullable=False, default="OTHER")
    status: Mapped[str] = mapped_column(String(_ENUM_WIDTH), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reason_code: Mapped[str] = mapped_column(String(_ENUM_WIDTH), nullable=False, default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    matched_result_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    queries: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    warnings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    investigation: Mapped[InvestigationRow] = relationship(
        back_populates="verification_results"
    )

    __table_args__ = (
        UniqueConstraint("investigation_id", "claim_id", name="uq_verification_claim"),
        # The batch order Phase 4 produced, which `RiskService` consumes positionally.
        Index("ix_verification_investigation_sequence", "investigation_id", "sequence"),
        Index("ix_verification_status", "investigation_id", "status"),
    )


class SourceRow(Base):
    """One Phase 5 `EvidenceSource` — a document *as one search returned it*.

    `canonical_url` is stored exactly as Phase 3/5 computed it and is never
    re-derived here. A second canonicalisation implementation is exactly the kind
    of duplicate logic this project has refused three times already, and a URL
    that normalises differently here would fork the `src_` ids Phase 4 derived
    from the first normalisation (D-023).

    The key is `(source_id, result_id)`, not `source_id` alone. The two identify
    different things: `source_id` is the document (`sha256(canonical_url)`) and is
    stable everywhere, while `result_id` is the individual provider result that
    addressed one claim. A provider that returns the same document for two claims
    produces two results and therefore two rows, and Phase 5 mints `ev_` ids from
    **both** ids. Keying on `source_id` alone would force one of those results to
    borrow the other's `result_id`, and every evidence item citing it would then
    round-trip to provenance that was never retrieved.
    """

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[str] = mapped_column(String(_ID_WIDTH), nullable=False, index=True)
    result_id: Mapped[str] = mapped_column(String(_ID_WIDTH), nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_type: Mapped[str] = mapped_column(String(24), nullable=False, default="UNKNOWN", index=True)
    source_tier: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN", index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    retrieved_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    investigation: Mapped[InvestigationRow] = relationship(back_populates="sources")

    __table_args__ = (
        UniqueConstraint(
            "investigation_id", "source_id", "result_id", name="uq_sources_investigation_result"
        ),
    )


class EvidenceResponseRow(Base):
    """One Phase 5 `EvidenceResponse` — the per-claim envelope.

    Needed as a row of its own because `EvidenceResponse` carries fields that
    belong to no single item: the claim's verification status, the reason code,
    the build time, and the **ordered** `sources` list.

    `source_refs_json` stores that list as `[source_id, result_id]` pairs, in the
    order Phase 5 built it. It is a reference, not a copy — the rows themselves
    live in `sources` — and it cannot be replaced by a join, because the
    relationship is a selection: `distinct_sources` keeps the *first* result seen
    for each document within a claim, while `sources` holds every result. Deriving
    the response's list from the table would change which `result_id` a retrieved
    document is reported under.
    """

    __tablename__ = "evidence_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claim_id: Mapped[str] = mapped_column(String(_ID_WIDTH), nullable=False, index=True)
    verification_status: Mapped[str] = mapped_column(String(_ENUM_WIDTH), nullable=False)
    verification_reason_code: Mapped[str] = mapped_column(String(_ENUM_WIDTH), nullable=False, default="")
    source_refs_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    warnings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    built_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    investigation: Mapped[InvestigationRow] = relationship(
        back_populates="evidence_responses"
    )
    items: Mapped[list["EvidenceItemRow"]] = relationship(
        back_populates="response",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="EvidenceItemRow.sequence",
    )

    __table_args__ = (
        UniqueConstraint("investigation_id", "claim_id", name="uq_evidence_responses_claim"),
        Index("ix_evidence_responses_investigation_sequence", "investigation_id", "sequence"),
    )


class EvidenceItemRow(Base):
    """One Phase 5 `EvidenceItem`.

    `claim_id` is not nullable and `source_id`/`result_id` are not nullable
    together with it. Evidence that cannot be attributed to a statement, or that
    has no retrievable source, is not evidence — and a schema that permits either
    would quietly accept rows the in-memory `EvidenceResponse` would have rejected
    (D-006, D-023).

    `sequence` is per response, not per investigation: ordering is a property of
    the bundle Phase 5 assembled for one claim.
    """

    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_response_id: Mapped[int] = mapped_column(
        ForeignKey("evidence_responses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_id: Mapped[str] = mapped_column(String(_ID_WIDTH), nullable=False, index=True)
    claim_id: Mapped[str] = mapped_column(String(_ID_WIDTH), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(_ID_WIDTH), nullable=False, index=True)
    result_id: Mapped[str] = mapped_column(String(_ID_WIDTH), nullable=False, index=True)
    claim_type: Mapped[str] = mapped_column(String(_ENUM_WIDTH), nullable=False, default="OTHER")
    verification_status: Mapped[str] = mapped_column(String(_ENUM_WIDTH), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # `relationship_` in Python, `relationship` in the database: the attribute
    # name Phase 5 uses is also the name of the SQLAlchemy function, and a column
    # of that name shadows it for the rest of the class body.
    relationship_: Mapped[str] = mapped_column(
        "relationship", String(24), nullable=False, index=True
    )
    relevance: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt_origin: Mapped[str] = mapped_column(String(8), nullable=False, default="snippet")
    matched_cue: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_query: Mapped[str | None] = mapped_column(Text, nullable=True)

    investigation: Mapped[InvestigationRow] = relationship(back_populates="evidence_items")
    response: Mapped[EvidenceResponseRow] = relationship(back_populates="items")

    __table_args__ = (
        UniqueConstraint("investigation_id", "evidence_id", name="uq_evidence_investigation_item"),
        # Composite rather than the single-column `claim_id` index: every real
        # query is already scoped to one investigation, so the pair is the useful
        # key. The single-column index is still emitted for `claim_id`.
        Index("ix_evidence_investigation_claim", "investigation_id", "claim_id"),
        Index("ix_evidence_investigation_source", "investigation_id", "source_id"),
    )


class RiskAssessmentRow(Base):
    """Phase 6's `RiskAssessment`, one per investigation.

    Separate from `investigations` because it does not exist on every run — a
    stopped run must report no score at all — and because its shape is the
    richest in the system.

    `risk_score` and `raw_score` are both kept. When a document trips more
    indicators than the ceiling allows, the two differ, and storing only the
    capped value would hide that a clamp was applied.

    There is deliberately **no `caveat` column**. The caveat that stops
    `risk_score` being read as a probability is `SCORE_NOT_A_PROBABILITY`, and
    `RiskAssessment` appends it to `warnings` itself during validation. Storing
    it separately would create a second copy of a string the schema already
    owns, free to drift from the one the model emits; keeping it only in
    `warnings_json` means rehydrating the assessment through the model
    reproduces the caveat exactly, by construction rather than by discipline
    (D-022, D-025).
    """

    __tablename__ = "risk_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_score: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    relevant_claims: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assessed_claims: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_coverage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    analysis_completeness: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    weights_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    thresholds_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    warnings_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    assessed_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    investigation: Mapped[InvestigationRow] = relationship(back_populates="risk_assessment")


class RiskFactorRow(Base):
    """One Phase 6 `RiskFactor`.

    De-duplicated factors are stored, not filtered. They contribute zero and name
    the factor that absorbed them; dropping them at write time would conceal that
    a claim was contradicted, which is the specific failure D-026 forbids.

    `claim_ids`, `red_flag_ids`, `evidence_ids`, `source_ids` and
    `verification_statuses` are JSON lists of ids rather than association tables:
    they are the trace *outward* from the factor, never queried inward from a
    claim or a flag, so a join table per id space would be five tables serving no
    query.
    """

    __tablename__ = "risk_factors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    factor_id: Mapped[str] = mapped_column(String(_ID_WIDTH), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    origin: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    factor_type: Mapped[str] = mapped_column(String(_ENUM_WIDTH), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="")
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="LOW")
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contribution: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    absorbed_into: Mapped[str | None] = mapped_column(String(_ID_WIDTH), nullable=True)
    claim_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    red_flag_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    evidence_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    verification_statuses: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_uncertainty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    investigation: Mapped[InvestigationRow] = relationship(back_populates="risk_factors")

    __table_args__ = (
        UniqueConstraint("investigation_id", "factor_id", name="uq_risk_factors_factor"),
        Index("ix_risk_factors_investigation_sequence", "investigation_id", "sequence"),
    )


class TimelineEventRow(Base):
    """One Phase 7 `TimelineEvent`.

    `sequence` is required because two stages can emit events in the same
    millisecond, and "which of these came first" is metadata a UI needs to
    render a strip rather than an unordered list. Ordering by timestamp alone
    would be a tie broken arbitrarily by the database.
    """

    __tablename__ = "timeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    investigation: Mapped[InvestigationRow] = relationship(back_populates="timeline")

    __table_args__ = (
        UniqueConstraint("investigation_id", "sequence", name="uq_timeline_sequence"),
    )


class InvestigationWarningRow(Base):
    """One Phase 7 `GraphWarning` — a limitation, not a failure.

    `error_type` is deliberately absent. A warning is something a user may read
    through the API, and an exception class name in front of one is a diagnostic
    that belongs in the log. Phase 8 already established that boundary by
    dropping the field on the way out; storing it would only put it one round trip
    away from being reintroduced.
    """

    __tablename__ = "investigation_warnings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")

    investigation: Mapped[InvestigationRow] = relationship(back_populates="warnings")

    __table_args__ = (
        Index("ix_warnings_investigation_sequence", "investigation_id", "sequence"),
    )


class InvestigationErrorRow(Base):
    """One Phase 7 `GraphError` — a failure that ended the run.

    `error_type` is stored because an error diagnoses **our** defect for an
    operator, and an exception class name is not a secret. What is never stored is
    the message: driver text routinely embeds a DSN, so `str(exc)` has no place
    in a row that a future API may render. This is the same rule Phase 8 applied
    to `probe_database`, applied again where a table column would otherwise make
    the leak trivially easy to reintroduce.
    """

    __tablename__ = "investigation_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)

    investigation: Mapped[InvestigationRow] = relationship(back_populates="errors")

    __table_args__ = (
        Index("ix_errors_investigation_sequence", "investigation_id", "sequence"),
    )