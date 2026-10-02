"""Weight table, band assignment and score arithmetic (Phase 6).

This module holds every number the risk assessment depends on, and nothing else.
There is no hidden threshold logic: `RiskThresholds` is snapshotted onto the
assessment so a stored report shows the configuration that produced its own level
(D-007).

The score is a **bounded heuristic indicator sum**, not a probability:

```
raw_score = Σ factor.contribution
risk_score = min(raw_score, ceiling)
risk_level = band(risk_score)
```

The cap is a ceiling, never a rescaling. A document that trips a hundred
indicators scores `100`, not a proportionally larger number, and `100` does not
mean "100% chance of anything" (D-025).
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.schemas.risk import (
    RiskLevel,
    RiskThresholds,
    RiskWeights,
)
from app.schemas.red_flags import RedFlagCode
from app.services.red_flag_rules import RULES_BY_CODE


def weights_from_settings(settings: Settings | None = None) -> RiskWeights:
    """Build the weight snapshot from configuration.

    Red-flag weights are read through Phase 1's own `RULES_BY_CODE` table, using
    each rule's `weight_attr`, so there is exactly one weight per red-flag rule in
    the whole system and one function that resolves it. Reconstructing setting
    names here as ``f"risk_weight_{code.value.lower()}"`` would create a second,
    silently divergent mapping — a rule whose setting is not named after its code
    would resolve to `0` and understate risk without any error (D-007).

    Args:
        settings: Configuration to read. Defaults to the cached application
            settings.

    Returns:
        A `RiskWeights` snapshot to store on the assessment.
    """
    resolved = settings or get_settings()
    weights: dict[RedFlagCode, int] = {}
    for code in RedFlagCode:
        rule = RULES_BY_CODE.get(code)
        if rule is None:
            # A code with no Phase 1 rule cannot be detected, so it can never
            # become a factor. Recording it as 0 keeps the snapshot complete
            # rather than leaving a gap a reader would have to notice.
            weights[code] = 0
            continue
        value = getattr(resolved, rule.weight_attr, rule.default_weight)
        try:
            weights[code] = max(0, int(value))
        except (TypeError, ValueError):
            weights[code] = rule.default_weight
    return RiskWeights(
        red_flag_weights=weights,
        contradicted_claim=int(resolved.risk_weight_contradicted_claim),
        unverified_claim=int(resolved.risk_weight_unverified_claim),
        insufficient_evidence=int(resolved.risk_weight_insufficient_evidence),
    )


def thresholds_from_settings(settings: Settings | None = None) -> RiskThresholds:
    """Build the band snapshot from configuration.

    Args:
        settings: Configuration to read. Defaults to the cached application
            settings.

    Returns:
        A `RiskThresholds` snapshot. Raises `ValueError` from the schema when the
        configured bands are not strictly increasing — a score must never belong
        to two levels.
    """
    resolved = settings or get_settings()
    return RiskThresholds(
        medium_max=int(resolved.risk_band_medium_max),
        high_max=int(resolved.risk_band_high_max),
        critical_max=int(resolved.risk_band_critical_max),
        ceiling=int(resolved.risk_score_ceiling),
    )


def band_for(score: int, thresholds: RiskThresholds) -> RiskLevel:
    """Return the band a score falls in.

    Bounds are inclusive: `0..medium_max` is `LOW`, `medium_max+1..high_max` is
    `MEDIUM`, and so on. A score at or above `critical_max + 1` is `CRITICAL`,
    which is the only region the ceiling can fall into.

    Args:
        score: A score already capped at `thresholds.ceiling`.
        thresholds: The band boundaries in force.

    Returns:
        The `RiskLevel` for this score.
    """
    if score <= thresholds.medium_max:
        return RiskLevel.LOW
    if score <= thresholds.high_max:
        return RiskLevel.MEDIUM
    if score <= thresholds.critical_max:
        return RiskLevel.HIGH
    return RiskLevel.CRITICAL


def score_from_contributions(contributions: tuple[int, ...]) -> int:
    """Sum factor contributions into the raw, uncapped indicator score.

    Args:
        contributions: One value per factor, already de-duplicated.

    Returns:
        The raw sum. Never negative; a malformed negative contribution is
        treated as zero rather than reducing the score, because no factor may
        lower risk in this phase (D-020).
    """
    return sum(max(0, value) for value in contributions)


def apply_ceiling(raw_score: int, thresholds: RiskThresholds) -> int:
    """Cap a raw score at the configured ceiling.

    Args:
        raw_score: The uncapped sum.
        thresholds: The band boundaries in force.

    Returns:
        `min(raw_score, ceiling)`.
    """
    return max(0, min(raw_score, thresholds.ceiling))


def level_for_score(
    raw_score: int, thresholds: RiskThresholds
) -> tuple[int, RiskLevel]:
    """Return the capped score and its band together.

    Args:
        raw_score: The uncapped sum of contributions.
        thresholds: The band boundaries in force.

    Returns:
        ``(risk_score, risk_level)``. The two are always derived from the same
        number, so a report can never show a score and a level that disagree.
    """
    capped = apply_ceiling(raw_score, thresholds)
    return capped, band_for(capped, thresholds)


__all__ = [
    "apply_ceiling",
    "band_for",
    "level_for_score",
    "score_from_contributions",
    "thresholds_from_settings",
    "weights_from_settings",
]