"""Risk assessment schemas (Phase 6).

Verification answers *can this claim be established?*. Evidence answers *what
exactly was retrieved?*. This phase answers the third and final question: **how
many documented risk indicators are present, how significant are they, and what
factors produced the resulting level?**

Three properties are enforced by the models here rather than by convention:

- **Every contribution is traceable.** A `RiskFactor` carries the red-flag, claim,
  evidence and source ids it came from, so "why did the score increase?" is
  answerable from the factor alone.
- **Counts are derived.** Every number on `RiskAssessment` except `risk_score`
  is recomputed from the factors, so a report can never show counts that disagree
  with the breakdown beneath them.
- **The score is not a probability.** `risk_score` is a bounded
  heuristic-indicator sum. A score of 80 does **not** mean an 80% chance of
  anything. No field here may be named, typed or documented as a probability of
  fraud, of loss, or of truth (D-006, D-007, D-020, D-025).

`factor_type` is deliberately a union of two *existing* vocabularies rather than
a new taxonomy. A factor derived from a Phase 1 red flag carries that rule's own
`RedFlagCode`, and a factor derived from a Phase 4 verification result carries a
`VerificationFactorType`. No risk-specific copy of the red-flag names exists, so
the two lists cannot drift apart (D-009 principle: one answer per question).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.schemas.claims import ClaimType
from app.schemas.red_flags import RedFlagCode, Severity
from app.schemas.verification import VerificationStatus
from app.services.capabilities import utc_now

#: Prefix for red-flag identifiers, derived in Phase 6. Phase 1's `RedFlag` has
#: no id of its own — it is deduplicated per code — so Phase 6 derives a stable
#: one from the code and the exact span it fired on. Same convention as Phase 4's
#: `src_`/`res_` and Phase 5's `ev_`.
RED_FLAG_ID_PREFIX = "rf_"

#: Prefix for risk-factor identifiers.
RISK_FACTOR_ID_PREFIX = "rsk_"

#: Hex characters kept from a risk digest, matching Phases 4 and 5.
RISK_ID_DIGEST_LENGTH = 12


class RiskLevel(str, Enum):
    """How many documented risk indicators were found, and how significant.

    A level describes **the investigated content and the available evidence**. It
    is not a judgement about the moral or legal status of any person or company,
    and it is never derived from the absence of evidence (D-006, D-025).
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


#: Sort rank per level, most severe first. Used for deterministic ordering only.
RISK_LEVEL_ORDER: dict[RiskLevel, int] = {
    RiskLevel.CRITICAL: 0,
    RiskLevel.HIGH: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.LOW: 3,
}


class VerificationFactorType(str, Enum):
    """Risk factors that come from Phase 4 verification rather than Phase 1.

    A deliberately tiny list. Each member maps to exactly one Phase 4 status that
    is materially relevant to investment safety:

    | Member | Phase 4 status | Why it is a risk factor at all |
    | --- | --- | --- |
    | `CONTRADICTED_CLAIM` | `CONTRADICTED` | An authoritative record directly disputes a material claim — the strongest external finding the pipeline can produce |
    | `UNVERIFIED_CLAIM` | `UNVERIFIED` | A search completed and found no confirmation for a material claim |
    | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` | Verification could not complete; this is **uncertainty**, not a finding, and its default weight is `0` |

    There is deliberately **no** `VERIFIED_CLAIM` member. A claim confirmed by an
    authoritative record is not a risk indicator, and no member may be added to
    encode a risk *reduction*: this phase accumulates documented indicators and
    does not net them against each other (D-020, D-025).
    """

    CONTRADICTED_CLAIM = "CONTRADICTED_CLAIM"
    UNVERIFIED_CLAIM = "UNVERIFIED_CLAIM"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class RiskFactorOrigin(str, Enum):
    """Which pipeline stage produced a factor."""

    #: Phase 1 red-flag detection. The factor is primary; evidence and
    #: verification attach to it as supporting context.
    RED_FLAG = "RED_FLAG"
    #: Phase 4 claim verification. The factor exists because a claim's status
    #: bears on the investment.
    VERIFICATION = "VERIFICATION"


class RiskFactor(BaseModel):
    """One documented risk indicator, with the provenance that produced it.

    Exactly one factor is emitted per *underlying signal*. A guaranteed-return
    promise appears in this bundle once — as a `GUARANTEED_RETURN` red-flag
    factor carrying its claim and evidence ids — never once per stage that
    noticed it (D-025).

    Attributes:
        id: Stable ``rsk_`` id derived from origin, type and the ids it cites, so
            rebuilding the same assessment yields the same id.
        origin: The stage that produced this factor.
        factor_type: A Phase 1 `RedFlagCode` or a Phase 4 `VerificationFactorType`.
        label: Short human-readable indicator name.
        description: What this indicator means, in plain language.
        reason: Why *this* factor exists here, naming the specific finding.
        source: Fixed-template provenance phrase, e.g. ``"red flag rf_1a2b3c4d5e6f"``.
        severity: Intrinsic seriousness of the indicator.
        weight: The configured heuristic weight. **Not** a probability.
        contribution: What this factor actually added to the score. Equal to
            `weight`, or ``0`` when the signal was already counted by another
            factor.
        absorbed_into: The ``rsk_`` id of the factor that already counts this
            signal, when this one contributes ``0`` because of de-duplication.
        claim_ids: Phase 2 claim ids this factor bears on.
        red_flag_ids: Phase 6-derived ids of the red flags behind it.
        evidence_ids: Phase 5 ``ev_`` ids quoting retrieved text.
        source_ids: Phase 4/5 ``src_`` ids of the documents cited.
        verification_statuses: Phase 4 statuses attached as supporting context.
            Populated on a red-flag factor when a verification result describes
            the *same underlying signal* and was therefore not scored separately.
        is_uncertainty: True when the factor records that something could not be
            established, rather than that something was found.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    origin: RiskFactorOrigin
    factor_type: RedFlagCode | VerificationFactorType
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    source: str = Field(min_length=1)
    severity: Severity = Severity.LOW
    weight: int = Field(ge=0, description="Heuristic weight, never a probability.")
    contribution: int = Field(ge=0, description="What this factor added to the score.")
    absorbed_into: str | None = None
    claim_ids: tuple[str, ...] = ()
    red_flag_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    verification_statuses: tuple[VerificationStatus, ...] = ()
    is_uncertainty: bool = False

    @model_validator(mode="after")
    def _validate_factor(self) -> RiskFactor:
        """Reject a factor whose numbers and provenance cannot both be true.

        The `contribution <= weight` rule is what makes "do not double count"
        checkable rather than aspirational: a factor can never contribute more
        than the weight declared for its type, and never more than once.
        """
        if self.contribution > self.weight:
            raise ValueError(
                f"contribution ({self.contribution}) must not exceed weight "
                f"({self.weight}) for factor {self.id}"
            )
        if self.absorbed_into is not None and self.contribution != 0:
            raise ValueError(
                "a factor absorbed into another must contribute 0; double "
                "counting is the failure this prevents"
            )
        if self.absorbed_into == self.id:
            raise ValueError("a factor cannot be absorbed into itself")
        if not self.id.startswith(RISK_FACTOR_ID_PREFIX):
            raise ValueError(f"risk factor id must start with {RISK_FACTOR_ID_PREFIX!r}")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_scoring(self) -> bool:
        """Whether this factor added anything to the score.

        False for a de-duplicated factor. Both kinds stay in the bundle, so the
        report can show *why* a contradicted claim contributed nothing under a
        red flag that already counted the same signal.
        """
        return self.contribution > 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_evidence_backed(self) -> bool:
        """Whether this factor cites at least one retrievable document."""
        return bool(self.evidence_ids)

    @property
    def verification_status(self) -> VerificationStatus | None:
        """Phase 4 status behind a verification factor, or `None` for red flags."""
        if self.origin is not RiskFactorOrigin.VERIFICATION:
            return None
        return _VERIFICATION_STATUS_BY_FACTOR.get(self.factor_type)


#: Maps a verification factor type to the Phase 4 status that produces it. Used
#: for display only; the status itself is never inferred, only looked up.
_VERIFICATION_STATUS_BY_FACTOR: dict[
    VerificationFactorType, VerificationStatus
] = {
    VerificationFactorType.CONTRADICTED_CLAIM: VerificationStatus.CONTRADICTED,
    VerificationFactorType.UNVERIFIED_CLAIM: VerificationStatus.UNVERIFIED,
    VerificationFactorType.INSUFFICIENT_EVIDENCE: VerificationStatus.INSUFFICIENT_EVIDENCE,
}


class RiskWeights(BaseModel):
    """The weight table actually used, snapshotted onto the assessment.

    A reader asking "why is this 68?" needs to see the configuration in effect
    at the time, not the configuration as it stands today. Snapshotting it is
    what keeps a stored report honest (D-007).
    """

    model_config = ConfigDict(frozen=True)

    #: Keyed by `RedFlagCode` rather than by a string, so a lookup can never miss
    #: because of key casing. `getattr` on a hand-typed `"fake_regulatory_claim"`
    #: against a lookup of `RedFlagCode.FAKE_REGULATORY_CLAIM` is exactly the kind
    #: of silent-zero that would silently understate risk.
    red_flag_weights: dict[RedFlagCode, int]
    contradicted_claim: int = Field(ge=0)
    unverified_claim: int = Field(ge=0)
    insufficient_evidence: int = Field(ge=0)

    def weight_for(self, factor_type: RedFlagCode | VerificationFactorType) -> int:
        """Return the configured weight for a factor type.

        Args:
            factor_type: A Phase 1 red-flag code or a verification factor type.

        Returns:
            The configured weight, or ``0`` for a code with no configuration.
        """
        if isinstance(factor_type, RedFlagCode):
            return int(self.red_flag_weights.get(factor_type, 0))
        if factor_type is VerificationFactorType.CONTRADICTED_CLAIM:
            return self.contradicted_claim
        if factor_type is VerificationFactorType.UNVERIFIED_CLAIM:
            return self.unverified_claim
        return self.insufficient_evidence


class RiskThresholds(BaseModel):
    """The band boundaries actually used.

    These are **product heuristics**. They are not empirically validated, they
    are not derived from any dataset, and no score at any level is a probability
    of anything (D-025).
    """

    model_config = ConfigDict(frozen=True)

    medium_max: int = Field(ge=0, description="Upper bound (inclusive) of LOW.")
    high_max: int = Field(ge=0, description="Upper bound (inclusive) of MEDIUM.")
    critical_max: int = Field(ge=0, description="Upper bound (inclusive) of HIGH.")
    ceiling: int = Field(ge=1, description="The score is capped at this value.")

    @model_validator(mode="after")
    def _validate_ordering(self) -> RiskThresholds:
        """Bands must be strictly increasing, or a score has two levels."""
        if not self.medium_max < self.high_max < self.critical_max:
            raise ValueError(
                "risk thresholds must satisfy medium_max < high_max < critical_max "
                f"(got {self.medium_max}, {self.high_max}, {self.critical_max})"
            )
        if self.critical_max >= self.ceiling:
            raise ValueError(
                f"critical_max ({self.critical_max}) must be below the score "
                f"ceiling ({self.ceiling})"
            )
        return self


class RiskAssessment(BaseModel):
    """The complete, explainable risk assessment for one investigation.

    The score is a **transparent heuristic indicator of documented risk factors**.
    It is not a probability of fraud, of financial loss, or of the investment
    failing, and it must never be presented as one (D-025).

    Every number below except `risk_score` and `raw_score` is recomputed from
    `factors`, so the breakdown and the counts cannot disagree.

    Attributes:
        risk_score: The bounded indicator score in `[0, ceiling]`. What the bands
            are applied to. **Not** a probability.
        raw_score: The uncapped sum of contributions, retained so a reader can
            see that the score was clamped rather than scaled.
        risk_level: The band the score falls in.
        factors: Every emitted factor, including de-duplicated ones that
            contributed ``0``, so the breakdown is complete.
        relevant_claims: Claims of a type that bears on investment safety.
        assessed_claims: Relevant claims that carry a Phase 4 verification result.
        weights: The weight table used, snapshotted.
        thresholds: The band boundaries used, snapshotted.
        warnings: Factual limitations. Never alarmist, never advisory.
        assessed_at: When this assessment was produced. Never used in any id.
    """

    model_config = ConfigDict(frozen=True)

    risk_score: int = Field(ge=0)
    raw_score: int = Field(ge=0)
    risk_level: RiskLevel = RiskLevel.LOW
    factors: tuple[RiskFactor, ...] = ()
    relevant_claims: int = Field(default=0, ge=0)
    assessed_claims: int = Field(default=0, ge=0)
    evidence_coverage: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Share of relevant assessed claims for which retrievable, "
            "authoritative supporting text was assembled. A transparency "
            "measure, NOT a confidence or an accuracy score."
        ),
    )
    analysis_completeness: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Share of the three per-claim analyses (extract, verify, assemble "
            "evidence) that were actually performed for relevant claims. NOT a "
            "probability of anything."
        ),
    )
    weights: RiskWeights
    thresholds: RiskThresholds
    warnings: tuple[str, ...] = ()
    assessed_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _validate_assessment(self) -> RiskAssessment:
        """Reject an assessment whose parts cannot all be true at once.

        Ordered most-specific first, so a malformed assessment reports the actual
        defect rather than whichever arithmetic check happens to trip on it.
        """
        if not self.factors and (self.risk_score or self.raw_score):
            raise ValueError("a zero-factor assessment must score 0")
        if self.assessed_claims > self.relevant_claims:
            raise ValueError("assessed_claims cannot exceed relevant_claims")
        if self.raw_score != sum(factor.contribution for factor in self.factors):
            raise ValueError("raw_score must equal the sum of factor contributions")
        if self.risk_score != min(self.raw_score, self.thresholds.ceiling):
            raise ValueError("risk_score must be the raw score capped at the ceiling")
        if not any(factor.is_scoring for factor in self.factors) and self.risk_score:
            # Unreachable given the two checks above — a nonzero risk_score
            # implies a nonzero raw_score, which implies a positive contribution.
            # Kept as an explicit statement of that reasoning: the invariant
            # "a score above 0 was produced by at least one factor" is the one
            # that stops a future refactor from inventing a score from nothing.
            raise ValueError(
                "a score above 0 requires at least one factor that contributed"
            )
        if SCORE_NOT_A_PROBABILITY not in self.warnings:
            # Appended rather than rejected: the caveat is not optional, but an
            # assessment that reached validation without it has a fixable defect,
            # and silently withholding the score would be the worse failure.
            object.__setattr__(
                self, "warnings", self.warnings + (SCORE_NOT_A_PROBABILITY,)
            )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_factors(self) -> int:
        """Number of factors emitted, including de-duplicated ones."""
        return len(self.factors)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def scoring_factors(self) -> int:
        """Number of factors that actually contributed to the score."""
        return sum(1 for factor in self.factors if factor.is_scoring)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def evidence_backed_factors(self) -> int:
        """Number of scoring factors citing at least one retrieved document."""
        return sum(1 for factor in self.factors if factor.is_evidence_backed)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def uncertainty_factors(self) -> int:
        """Number of factors recording that something could not be established."""
        return sum(1 for factor in self.factors if factor.is_uncertainty)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def deduplicated_factors(self) -> int:
        """Number of factors absorbed into another factor as the same signal."""
        return sum(1 for factor in self.factors if factor.absorbed_into is not None)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def red_flag_factors(self) -> int:
        """Number of Phase 1 red-flag factors emitted."""
        return sum(1 for factor in self.factors if factor.origin is RiskFactorOrigin.RED_FLAG)

    @property
    def factor_by_id(self) -> dict[str, RiskFactor]:
        """Every emitted factor, keyed by its stable ``rsk_`` id."""
        return {factor.id: factor for factor in self.factors}

    @property
    def contributing_factors(self) -> tuple[RiskFactor, ...]:
        """Only the factors that added to the score, in emission order."""
        return tuple(factor for factor in self.factors if factor.is_scoring)


#: Emitted on every assessment, so no report can present a score without its
#: meaning attached to it.
#:
#: Held here rather than in the service because it is a **schema invariant**, not
#: a service output: `RiskAssessment` appends it itself during validation, so an
#: assessment built by any route — the service, a test, a future Phase 7
#: rehydration of stored data — carries the caveat. A caller cannot forget to add
#: it, and a stored report cannot lose it. Fixed wording, never generated
#: (D-022, D-025).
SCORE_NOT_A_PROBABILITY = (
    "The risk score is a transparent heuristic indicator of documented risk "
    "factors. It is not a probability of fraud, of financial loss, or of the "
    "investment failing, and it is not a recommendation to invest or not invest."
)


#: Claim types that bear on investment safety, and so whose verification status
#: may produce a risk factor.
#:
#: Built from Phase 2's own vocabularies rather than restated, so a change to
#: `VERIFIABLE_CLAIM_TYPES` or `PROMISE_CLAIM_TYPES` cannot leave this list
#: silently out of date.
#:
#: `COMPANY_CLAIM` is deliberately removed from `VERIFIABLE_CLAIM_TYPES` for this
#: purpose. "Our office is open on Monday" is a `COMPANY_CLAIM`, and an
#: unverifiable statement about opening hours says nothing about investment risk.
#: `PAYMENT_INSTRUCTION` is added: where the money goes is squarely material.
RISK_RELEVANT_CLAIM_TYPES: frozenset[ClaimType] = frozenset(
    {
        ClaimType.REGULATORY_STATUS,
        ClaimType.GUARANTEE_CLAIM,
        ClaimType.RETURN_PROMISE,
        ClaimType.PROFIT_PROMISE,
        ClaimType.PERFORMANCE_CLAIM,
        ClaimType.CREDENTIAL_CLAIM,
        ClaimType.OWNERSHIP_CLAIM,
        ClaimType.AFFILIATION_CLAIM,
        ClaimType.WITHDRAWAL_CLAIM,
        ClaimType.PAYMENT_INSTRUCTION,
    }
)


__all__ = [
    "RED_FLAG_ID_PREFIX",
    "RISK_FACTOR_ID_PREFIX",
    "RISK_ID_DIGEST_LENGTH",
    "RISK_LEVEL_ORDER",
    "RISK_RELEVANT_CLAIM_TYPES",
    "SCORE_NOT_A_PROBABILITY",
    "RiskAssessment",
    "RiskFactor",
    "RiskFactorOrigin",
    "RiskLevel",
    "RiskThresholds",
    "RiskWeights",
    "VerificationFactorType",
]