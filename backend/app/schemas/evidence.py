"""Evidence schemas (Phase 5, §1).

The conceptual model this phase formalises:

    CLAIM → EVIDENCE → SOURCE

Phase 4 answered *"can external sources establish this claim?"*. That answer is
worthless to a user on its own — "UNVERIFIED, no matching registration found" is
a claim about the system's work, not about the world. This phase supplies the
missing half: for every claim, **what actual retrieved text exists, where did it
come from, and what did Phase 4 make of it**.

The Evidence Engine makes no determination of its own. It never reads a
`VerificationStatus` and reacts by inventing something, never promotes a page to
"authoritative" on its own opinion, and never assigns a risk. Every field here
is either a copy of a Phase 3 `SearchResult` field or a value derived from
Phase 4's comparison output (D-020, D-023).

Three invariants are enforced by the schema itself:

- **Provenance is mandatory.** An `EvidenceItem` cannot exist without a
  `claim_id`, a `source`, a `result_id`, a non-empty `excerpt` and a retrieval
  timestamp. There is no "evidence, trust me" constructor.
- **Excerpts are traceable, not authored.** `excerpt_origin` names the
  `SearchResult` field the text was copied from (`snippet` or `title`). Nothing
  is summarised, paraphrased or stitched together, because the moment an excerpt
  is rewritten, it is no longer something the user can check.
- **Counts are derived.** `EvidenceResponse` recomputes its own counts, so they
  cannot drift from the items they describe.

`EvidenceRelation` is deliberately a *relationship*, not a verdict. `SUPPORTS`
means a claim-relevant authoritative record was read as supporting the claim;
it is never a truth claim, and it is never inferred from the absence of
evidence (D-021, D-023).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.schemas.claims import ClaimType
from app.schemas.search import SOURCE_PRIORITY, SourceType
from app.schemas.verification import SourceTier, VerificationStatus
from app.services.capabilities import utc_now

#: Prefix for evidence ids. Distinguishable at a glance from Phase 4's `src_`
#: (source document) and `res_` (search result) identifiers.
EVIDENCE_ID_PREFIX = "ev_"

#: Hex characters kept from the evidence digest. Matches Phase 4's digest length
#: so all three id families read the same way in logs and reports.
EVIDENCE_ID_DIGEST_LENGTH = 12

#: Phases after which an `EvidenceItem` may carry no excerpt. An evidence item
#: whose entire content is a summary has stopped being evidence.
ALLOWED_EXCERPT_ORIGINS = frozenset({"snippet", "title"})


class EvidenceType(str, Enum):
    """What kind of record an evidence item points at.

    Deliberately a *category of record*, not a statement about the claim. The
    list is the smallest one that stays useful: it mirrors Phase 4's
    authoritative tiers and collapses everything else into `SEARCH_RESULT`.

    The longer taxonomy sometimes proposed for this phase — with members such as
    `CLAIM_SUPPORT`, `CLAIM_CONTRADICTION` and `CONTEXTUAL` — was rejected: those
    encode exactly what `EvidenceRelation` and `EvidenceRelevance` already carry.
    Recording the same fact twice guarantees the two copies eventually disagree,
    and a report that says "type: contradiction, relationship: supports" is worse
    than no type at all (D-023).
    """

    #: Phase 4 tier 1 — a primary regulator's own record (SEBI, RBI, IRDAI, …).
    REGULATORY_RECORD = "REGULATORY_RECORD"
    #: Phase 4 tier 2 — a government body that is not a named regulator.
    GOVERNMENT_RECORD = "GOVERNMENT_RECORD"
    #: Phase 4 tier 3 — a recognised exchange.
    EXCHANGE_RECORD = "EXCHANGE_RECORD"
    #: Phase 4 tier 4 — a body known to speak officially about itself.
    OFFICIAL_ENTITY_SOURCE = "OFFICIAL_ENTITY_SOURCE"
    #: Anything else retrieved, including news, blogs and general web pages.
    #: May be excellent context; can never settle a claim.
    SEARCH_RESULT = "SEARCH_RESULT"


class EvidenceRelation(str, Enum):
    """How one retrieved document relates to one claim.

    Neutral by construction. No member states that a claim is true or false, and
    none of them may be assigned from the *absence* of information in a
    document (D-021, D-023).
    """

    #: Phase 4 read a claim-relevant authoritative record as supporting the claim.
    SUPPORTS = "SUPPORTS"
    #: Phase 4 read a claim-relevant authoritative record as directly conflicting
    #: with the claim. The only route to a `CONTRADICTED` verification status.
    CONTRADICTS = "CONTRADICTS"
    #: The document is about the claimed entity but says nothing about the claim.
    IDENTITY_REFERENCE = "IDENTITY_REFERENCE"
    #: The document comes from a relevant authority but concerns a *similarly
    #: named* party, so it informs the reader without establishing anything.
    CONTEXT = "CONTEXT"
    #: No relationship to the claim could be established at all.
    MENTIONS = "MENTIONS"


class EvidenceRelevance(str, Enum):
    """How directly an evidence item bears on its claim.

    Describes *closeness*, not weight and not risk. `HIGH` means the document
    addresses the claim about the correct entity; `LOW` means it was retrieved
    while looking and is included for completeness.

    This is never a risk score, and it is never summed into one (D-007).
    """

    #: About the exact entity *and* speaking to the claim (Phase 4 stance found).
    HIGH = "HIGH"
    #: About the exact entity, silent on the claim.
    MEDIUM = "MEDIUM"
    #: Retrieved while searching; included so the reader sees what was looked at.
    LOW = "LOW"


#: Sort rank per relevance, most directly relevant first. Used for deterministic
#: ordering only.
RELEVANCE_RANK: dict[EvidenceRelevance, int] = {
    EvidenceRelevance.HIGH: 0,
    EvidenceRelevance.MEDIUM: 1,
    EvidenceRelevance.LOW: 2,
}

#: Sort rank per relationship, most probative first. Used for deterministic
#: ordering only; it is not a credibility score.
RELATION_RANK: dict[EvidenceRelation, int] = {
    EvidenceRelation.SUPPORTS: 0,
    EvidenceRelation.CONTRADICTS: 1,
    EvidenceRelation.IDENTITY_REFERENCE: 2,
    EvidenceRelation.CONTEXT: 3,
    EvidenceRelation.MENTIONS: 4,
}


class EvidenceSource(BaseModel):
    """The document an evidence item was read from.

    A source is a real, retrieved `SearchResult` promoted to a named object. Its
    classification is never recomputed here: `source_type` is Phase 3's
    `classify_domain()` output and `source_tier` is Phase 4's registry result,
    both carried through unchanged so that one URL cannot be tiered two different
    ways by two phases (D-018, D-023).

    Attributes:
        source_id: Phase 4's ``src_`` id, `sha256(canonical_url)[:12]`. Stable
            across claims, queries and runs, so the same document is the same
            source everywhere.
        result_id: Phase 4's ``res_`` id for the individual result. Distinct
            from `source_id` because "the document" and "the result that
            addressed this claim" are different questions.
        url: The URL exactly as the provider returned it.
        canonical_url: Phase 3's canonical form, used for de-duplication.
        domain: Registrable hostname from Phase 3's `extract_domain()`.
        title: Result title as published.
        source_type: Phase 3 publisher category.
        source_tier: Phase 4 authority tier.
        retrieved_at: When the provider returned this result. Copied from the
            `SearchResult`; never re-stamped, so the recorded time is the real
            retrieval time.
        position: 1-based provider rank, used as a stable tie-breaker.
    """

    model_config = ConfigDict(frozen=True)

    source_id: str = Field(min_length=1)
    result_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    canonical_url: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_type: SourceType = SourceType.UNKNOWN
    source_tier: SourceTier = SourceTier.UNKNOWN
    retrieved_at: datetime = Field(default_factory=utc_now)
    position: int = Field(default=1, ge=1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source_priority(self) -> int:
        """Phase 3's customary authority rank for this publisher category.

        Derived, never stored: a second copy of this number could disagree with
        `SOURCE_PRIORITY` and quietly become a competing priority system.
        """
        return SOURCE_PRIORITY[self.source_type]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_authoritative(self) -> bool:
        """Whether Phase 4 considers this publisher able to settle a claim.

        Tiers 1-3 only. `False` does not make a source worthless — it makes it
        context rather than proof.
        """
        from app.schemas.verification import AUTHORITATIVE_TIERS

        return self.source_tier in AUTHORITATIVE_TIERS


class EvidenceItem(BaseModel):
    """One traceable piece of retrieved text bearing on one claim.

    Attributes:
        id: Stable ``ev_`` id, derived from claim, source, excerpt origin and
            relationship — never from a counter or a clock, so rebuilding the
            same evidence twice yields the same id.
        claim_id: The claim this item bears on. Always present: evidence is
            meaningless without the statement it is about.
        verification_status: Phase 4's status for that claim, carried so a reader
            (or a later report) can see which decision this explains. Evidence
            never changes it.
        evidence_type: Record category, derived from the source tier.
        relationship: How this document relates to the claim.
        relevance: How directly it bears on the claim.
        excerpt: Text copied verbatim from the result. Never summarised.
        excerpt_origin: Which `SearchResult` field `excerpt` was copied from.
        source: The document the excerpt came from, with full provenance.
        matched_cue: Phase 4's cue that produced the relationship, when there was
            one. Kept so a reader can check *why* a document was read as
            supporting or contradicting.
        provider_query: The Phase 3 query that surfaced this result, when known.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    verification_status: VerificationStatus = VerificationStatus.INSUFFICIENT_EVIDENCE
    claim_type: ClaimType = ClaimType.OTHER
    evidence_type: EvidenceType = EvidenceType.SEARCH_RESULT
    relationship: EvidenceRelation = EvidenceRelation.MENTIONS
    relevance: EvidenceRelevance = EvidenceRelevance.LOW
    excerpt: str = Field(min_length=1)
    excerpt_origin: str = Field(default="snippet")
    source: EvidenceSource
    matched_cue: str | None = None
    provider_query: str | None = None

    @model_validator(mode="after")
    def _validate_provenance(self) -> EvidenceItem:
        """Reject any evidence that could not have come from a real result.

        The checks are cheap and the failure they prevent is the worst available
        to this product: an item that looks like evidence but cannot be traced
        back to a document a provider actually returned.
        """
        if self.excerpt_origin not in ALLOWED_EXCERPT_ORIGINS:
            raise ValueError(f"excerpt_origin must be one of {sorted(ALLOWED_EXCERPT_ORIGINS)}")
        if not self.excerpt.strip():
            raise ValueError("an evidence item must carry a non-empty excerpt")
        if not self.id.startswith(EVIDENCE_ID_PREFIX):
            raise ValueError(f"evidence id must start with {EVIDENCE_ID_PREFIX!r}")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source_id(self) -> str:
        """Shorthand for `self.source.source_id`."""
        return self.source.source_id

    @computed_field  # type: ignore[prop-decorator]
    @property
    def result_id(self) -> str:
        """Shorthand for `self.source.result_id`."""
        return self.source.result_id

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        """Shorthand for `self.source.url`, for convenient display."""
        return self.source.url

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_proof(self) -> bool:
        """Whether this item speaks to the claim rather than beside it.

        Proof here means "an authoritative, identity-matched, claim-relevant
        record that says something about the claim" — nothing about safety,
        fraud or the merits of an investment (D-020, D-023).
        """
        return (
            self.relevance is EvidenceRelevance.HIGH
            and self.relationship
            in (EvidenceRelation.SUPPORTS, EvidenceRelation.CONTRADICTS)
        )

    @property
    def trace_path(self) -> str:
        """Human-readable `claim -> evidence -> result -> source` chain.

        ASCII arrows, so the string is safe to write to a log, a CSV export or a
        console without an encoding error.
        """
        return (
            f"{self.claim_id} -> {self.id} -> {self.source.result_id} -> "
            f"{self.source.source_id} ({self.source.url})"
        )


class EvidenceResponse(BaseModel):
    """All evidence for one claim, with counts and limitations.

    An empty `evidence` tuple is a legitimate, meaningful result. It is what a
    claim looks like when the search could not run, found nothing, or was never
    applicable — and in every one of those cases the honest answer is no
    evidence, not a manufactured placeholder (D-023).

    Attributes:
        claim_id: The claim these items belong to.
        verification_status: Phase 4's status for that claim.
        verification_reason_code: Phase 4's structured reason, carried so the
            evidence can always be read next to the reason it explains.
        evidence: Items in deterministic order.
        sources: Distinct sources behind those items, in the same order.
        warnings: Limitations, e.g. a failed search or a partial result set.
        built_at: When this bundle was assembled. Never used for ordering, and
            never part of an evidence id.
    """

    model_config = ConfigDict(frozen=True)

    claim_id: str = Field(min_length=1)
    verification_status: VerificationStatus = VerificationStatus.INSUFFICIENT_EVIDENCE
    verification_reason_code: str = ""
    evidence: tuple[EvidenceItem, ...] = ()
    sources: tuple[EvidenceSource, ...] = ()
    warnings: tuple[str, ...] = ()
    built_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _validate_containment(self) -> EvidenceResponse:
        """Every item must belong to this claim, and cite a listed source.

        Without this, a response could quietly mix evidence from another claim
        and remain internally consistent — the kind of bug that produces a
        report nobody can audit.
        """
        source_ids = {source.source_id for source in self.sources}
        for item in self.evidence:
            if item.claim_id != self.claim_id:
                raise ValueError(
                    f"evidence {item.id} belongs to claim {item.claim_id}, "
                    f"not {self.claim_id}"
                )
            if item.source.source_id not in source_ids:
                raise ValueError(
                    f"evidence {item.id} cites source {item.source.source_id}, "
                    "which is not listed in sources"
                )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def evidence_count(self) -> int:
        """Number of evidence items."""
        return len(self.evidence)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def supports_count(self) -> int:
        """Number of items that support the claim."""
        return _count_relation(self.evidence, EvidenceRelation.SUPPORTS)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def contradicts_count(self) -> int:
        """Number of items that directly conflict with the claim."""
        return _count_relation(self.evidence, EvidenceRelation.CONTRADICTS)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def context_count(self) -> int:
        """Number of items that inform without establishing anything."""
        return len(self.evidence) - self.supports_count - self.contradicts_count

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source_count(self) -> int:
        """Number of distinct sources behind the evidence."""
        return len(self.sources)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def proof_count(self) -> int:
        """Number of items that actually speak to the claim.

        Zero for any inconclusive verification, and zero whenever the claim was
        never externally verifiable — which is the point: proof is rarer than
        results.
        """
        return sum(1 for item in self.evidence if item.is_proof)


class EvidenceBundleResponse(BaseModel):
    """Evidence for a batch of claims.

    Attributes:
        responses: One `EvidenceResponse` per claim, in input order.
        warnings: Batch-level limitations.
        evidence_count: Total items across all claims.
        claims_with_evidence: How many claims have at least one item.
        claims_without_evidence: How many have none. Tracked explicitly because
            "we found nothing" must stay visible rather than vanish.
    """

    model_config = ConfigDict(frozen=True)

    responses: tuple[EvidenceResponse, ...] = ()
    warnings: tuple[str, ...] = ()
    built_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _validate_unique_claims(self) -> EvidenceBundleResponse:
        """One response per claim; a duplicated claim id would double-count."""
        claim_ids = [response.claim_id for response in self.responses]
        duplicates = {claim_id for claim_id in claim_ids if claim_ids.count(claim_id) > 1}
        if duplicates:
            raise ValueError(f"duplicate responses for claim(s): {sorted(duplicates)}")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def evidence_count(self) -> int:
        """Total number of evidence items in the bundle."""
        return sum(response.evidence_count for response in self.responses)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def claims_with_evidence(self) -> int:
        """How many claims have at least one evidence item."""
        return sum(1 for response in self.responses if response.evidence)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def claims_without_evidence(self) -> int:
        """How many claims have no evidence at all."""
        return sum(1 for response in self.responses if not response.evidence)


def _count_relation(items: tuple[EvidenceItem, ...], relation: EvidenceRelation) -> int:
    """Count items with a given relationship."""
    return sum(1 for item in items if item.relationship is relation)


__all__ = [
    "ALLOWED_EXCERPT_ORIGINS",
    "EVIDENCE_ID_DIGEST_LENGTH",
    "EVIDENCE_ID_PREFIX",
    "RELATION_RANK",
    "RELEVANCE_RANK",
    "EvidenceBundleResponse",
    "EvidenceItem",
    "EvidenceRelation",
    "EvidenceRelevance",
    "EvidenceResponse",
    "EvidenceSource",
    "EvidenceType",
]
