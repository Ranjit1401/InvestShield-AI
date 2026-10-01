"""Typed result schemas for deterministic red-flag detection (Phase 1).

These are API/domain contracts, not database models (D-010). The wording of
every field is user-facing: raw regex patterns and other implementation details
must never surface in a report.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class Severity(str, Enum):
    """How serious a single indicator is, independent of its weight."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


#: Explicit ordering used for deterministic sorting. CRITICAL sorts first.
SEVERITY_ORDER: dict[Severity, int] = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
}


def severity_rank(severity: Severity) -> int:
    """Return the sort rank of a severity (lower sorts first).

    Args:
        severity: The severity to rank.

    Returns:
        Integer rank, highest severity first.
    """
    return SEVERITY_ORDER[severity]


class RedFlagCode(str, Enum):
    """Stable identifiers for the 15 red-flag rules.

    These codes are part of the API contract and of the persisted schema. Never
    rename one without a migration and a documentation update.
    """

    GUARANTEED_RETURN = "GUARANTEED_RETURN"
    UNREALISTIC_RETURN = "UNREALISTIC_RETURN"
    URGENCY_PRESSURE = "URGENCY_PRESSURE"
    FAKE_REGULATORY_CLAIM = "FAKE_REGULATORY_CLAIM"
    UNVERIFIED_ADVISER = "UNVERIFIED_ADVISER"
    SUSPICIOUS_URL = "SUSPICIOUS_URL"
    THIRD_PARTY_PAYMENT = "THIRD_PARTY_PAYMENT"
    APK_DOWNLOAD = "APK_DOWNLOAD"
    TELEGRAM_INVESTMENT_GROUP = "TELEGRAM_INVESTMENT_GROUP"
    WHATSAPP_INVESTMENT_GROUP = "WHATSAPP_INVESTMENT_GROUP"
    BORROW_TO_INVEST = "BORROW_TO_INVEST"
    WITHDRAWAL_FEE = "WITHDRAWAL_FEE"
    ACCOUNT_ACTIVATION_FEE = "ACCOUNT_ACTIVATION_FEE"
    FAKE_PROFIT_SCREENSHOT = "FAKE_PROFIT_SCREENSHOT"
    IMPERSONATION = "IMPERSONATION"


class EvidenceSpan(BaseModel):
    """Exact location of a detection inside the submitted text.

    Attributes:
        start: Inclusive character offset into the original input string.
        end: Exclusive character offset into the original input string.
        text: The verbatim substring ``text[start:end]``.
    """

    model_config = ConfigDict(frozen=True)

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str

    @model_validator(mode="after")
    def _validate_span(self) -> EvidenceSpan:
        if self.end <= self.start:
            raise ValueError("evidence span end must be greater than start")
        return self


class RedFlag(BaseModel):
    """One logical red-flag finding, deduplicated per :class:`RedFlagCode`.

    A single finding aggregates every place in the text where the rule fired;
    ``evidence_span``/``matched_text`` point at the strongest occurrence and
    ``additional_spans`` preserves the rest.

    Attributes:
        code: Stable rule identifier.
        name: Human-readable indicator name.
        description: What this indicator means in plain language.
        severity: Intrinsic seriousness of the indicator.
        weight: Configurable heuristic weight. **Not** a probability.
        matched_text: Verbatim substring that triggered the rule.
        evidence_span: Exact offsets of ``matched_text`` in the input.
        rule_reason: Why this text was flagged, in plain language.
        additional_spans: Other occurrences of the same rule, if any.
    """

    model_config = ConfigDict(frozen=True)

    code: RedFlagCode
    name: str
    description: str
    severity: Severity
    weight: int = Field(ge=0, description="Heuristic indicator weight, not a probability.")
    matched_text: str
    evidence_span: EvidenceSpan
    rule_reason: str
    additional_spans: list[EvidenceSpan] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sync_matched_text(self) -> RedFlag:
        if self.matched_text != self.evidence_span.text:
            raise ValueError("matched_text must equal evidence_span.text")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def occurrence_count(self) -> int:
        """Total number of places this rule fired, including the primary span."""
        return len(self.additional_spans) + 1

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sort_key(self) -> tuple[int, int, str]:
        """Deterministic ordering key: severity, then weight, then code."""
        return (severity_rank(self.severity), -self.weight, self.code.value)


__all__ = [
    "SEVERITY_ORDER",
    "EvidenceSpan",
    "RedFlag",
    "RedFlagCode",
    "Severity",
    "severity_rank",
]
