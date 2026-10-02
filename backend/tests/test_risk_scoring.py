"""Tests for Phase 6 weight resolution, band assignment and score arithmetic.

Four properties matter here, and each corresponds to a way a score could mislead:

- **One weight table.** Every red-flag weight is resolved through Phase 1's own
  ``RULES_BY_CODE`` / ``weight_attr``, so a rule cannot end up with two different
  weights depending on which stage asked.
- **Bounds are inclusive and contiguous.** ``medium_max`` is the last LOW score,
  not the first MEDIUM score, and every score from 0 to the ceiling has exactly one
  level.
- **The cap is a ceiling, not a rescaling.** A document tripping a hundred
  indicators scores ``ceiling``, not something proportionally larger, so a score is
  comparable between investigations no matter how much was found.
- **Nothing can subtract.** A negative contribution is floored at zero, because
  this phase accumulates documented indicators and does not net them against each
  other (D-020).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.schemas.red_flags import RedFlagCode
from app.schemas.risk import RiskLevel, RiskThresholds
from app.services.red_flag_engine import RedFlagEngine
from app.services.red_flag_rules import ALL_RULES, RULES_BY_CODE
from app.services.risk import (
    apply_ceiling,
    band_for,
    level_for_score,
    score_from_contributions,
    thresholds_from_settings,
    weights_from_settings,
)

#: Every risk-related configuration field Phase 6 depends on: the fifteen Phase 1
#: red-flag weights, the three verification weights Phase 6 added, the three band
#: bounds and the score ceiling. All of them must be settable from the
#: environment (D-007), which means all of them must appear in `.env.example`.
RISK_SETTING_FIELDS: tuple[str, ...] = (
    *(f"risk_weight_{code.value.lower()}" for code in RedFlagCode),
    "risk_weight_contradicted_claim",
    "risk_weight_unverified_claim",
    "risk_weight_insufficient_evidence",
    "risk_band_medium_max",
    "risk_band_high_max",
    "risk_band_critical_max",
    "risk_score_ceiling",
)


# -- Weight resolution ---------------------------------------------------


class TestWeightsFromSettings:
    def test_every_red_flag_code_resolves_a_positive_weight(self) -> None:
        """A code silently resolving 0 would understate risk with no error."""
        weights = weights_from_settings()
        unresolved = [
            code.value for code in RedFlagCode if weights.weight_for(code) <= 0
        ]
        assert unresolved == []

    def test_reads_the_same_weight_phase_one_reads(self) -> None:
        """One weight per rule, or two stages disagree about the same finding."""
        settings = Settings()
        weights = weights_from_settings(settings)
        engine = RedFlagEngine(settings)
        for code in RedFlagCode:
            assert weights.weight_for(code) == engine.weight_for(code), code.value

    def test_resolves_through_phase_ones_own_rule_table(self) -> None:
        """Weight lookup must not reconstruct setting names by string munging."""
        settings = Settings()
        weights = weights_from_settings(settings)
        for rule in ALL_RULES:
            assert weights.weight_for(rule.code) == int(
                getattr(settings, rule.weight_attr, rule.default_weight)
            )

    def test_every_code_in_the_taxonomy_has_a_phase_one_rule(self) -> None:
        """A code with no rule cannot be detected, so it must not be scored."""
        assert set(RULES_BY_CODE) == set(RedFlagCode)

    def test_reflects_a_settings_override(self) -> None:
        settings = Settings(risk_weight_guaranteed_return=33)
        assert weights_from_settings(settings).weight_for(
            RedFlagCode.GUARANTEED_RETURN
        ) == 33

    def test_falls_back_to_the_rule_default_when_the_setting_is_invalid(self) -> None:
        """A bad config value must not crash the pipeline or zero the weight.

        `Settings` validates its own integers, so this is defence in depth: the
        same guard exists in Phase 1's `RedFlagEngine.weight_for`, and a future
        settings type that tolerates a bad value must not turn one bad weight
        into a `TypeError` mid-assessment.
        """

        class BadlyTypedSettings(Settings):
            """Settings whose one weight is a string, bypassing the int check."""

            risk_weight_guaranteed_return: str = "not-a-number"  # type: ignore[assignment]

        weights = weights_from_settings(BadlyTypedSettings())
        default = RULES_BY_CODE[RedFlagCode.GUARANTEED_RETURN].default_weight
        assert weights.weight_for(RedFlagCode.GUARANTEED_RETURN) == default

    def test_clamps_a_negative_setting_to_zero(self) -> None:
        settings = Settings(risk_weight_guaranteed_return=-5)
        assert (
            weights_from_settings(settings).weight_for(RedFlagCode.GUARANTEED_RETURN)
            == 0
        )

    def test_insufficient_evidence_weighs_nothing_by_default(self) -> None:
        """Not being able to check something is not a finding (D-006)."""
        assert weights_from_settings().insufficient_evidence == 0

    def test_verification_weights_come_from_settings(self) -> None:
        settings = Settings(
            risk_weight_contradicted_claim=40,
            risk_weight_unverified_claim=5,
        )
        weights = weights_from_settings(settings)
        assert weights.contradicted_claim == 40
        assert weights.unverified_claim == 5

    def test_contradiction_outweighs_a_gap_in_the_record(self) -> None:
        """An authoritative conflict is stronger than a search that found nothing."""
        weights = weights_from_settings()
        assert weights.contradicted_claim > weights.unverified_claim

    def test_is_deterministic(self) -> None:
        assert weights_from_settings() == weights_from_settings()


# -- Thresholds ----------------------------------------------------------


class TestThresholdsFromSettings:
    def test_reads_the_configured_bands(self) -> None:
        thresholds = thresholds_from_settings(Settings())
        assert thresholds.medium_max == 20
        assert thresholds.high_max == 50
        assert thresholds.critical_max == 90
        assert thresholds.ceiling == 100

    def test_the_ceiling_sits_above_the_top_band(self) -> None:
        """Otherwise CRITICAL would be the only score the ceiling could produce."""
        thresholds = thresholds_from_settings()
        assert thresholds.critical_max < thresholds.ceiling

    def test_rejects_a_configuration_with_overlapping_bands(self) -> None:
        """A score must never belong to two levels at once."""
        with pytest.raises(ValueError):
            thresholds_from_settings(
                Settings(risk_band_medium_max=60, risk_band_high_max=20)
            )


# -- Band assignment -----------------------------------------------------


class TestBandFor:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (0, RiskLevel.LOW),
            (1, RiskLevel.LOW),
            (20, RiskLevel.LOW),
            (21, RiskLevel.MEDIUM),
            (50, RiskLevel.MEDIUM),
            (51, RiskLevel.HIGH),
            (90, RiskLevel.HIGH),
            (91, RiskLevel.CRITICAL),
            (100, RiskLevel.CRITICAL),
        ],
    )
    def test_bounds_are_inclusive_at_the_top_of_each_band(
        self, score: int, expected: RiskLevel
    ) -> None:
        """`medium_max` is the last LOW score, not the first MEDIUM score."""
        thresholds = RiskThresholds(
            medium_max=20, high_max=50, critical_max=90, ceiling=100
        )
        assert band_for(score, thresholds) is expected

    def test_every_score_from_zero_to_the_ceiling_has_exactly_one_level(self) -> None:
        thresholds = RiskThresholds(
            medium_max=20, high_max=50, critical_max=90, ceiling=100
        )
        assert [band_for(score, thresholds) for score in range(101)] == [
            band_for(score, thresholds) for score in range(101)
        ]

    def test_levels_are_monotonic_in_the_score(self) -> None:
        """More documented indicators must never produce a lower level."""
        thresholds = RiskThresholds(
            medium_max=20, high_max=50, critical_max=90, ceiling=100
        )
        ranks = {
            RiskLevel.LOW: 0,
            RiskLevel.MEDIUM: 1,
            RiskLevel.HIGH: 2,
            RiskLevel.CRITICAL: 3,
        }
        observed = [ranks[band_for(s, thresholds)] for s in range(101)]
        assert observed == sorted(observed)

    def test_a_ceiling_sized_document_is_critical(self) -> None:
        """The only score the ceiling can land in must be the top band."""
        thresholds = RiskThresholds(
            medium_max=20, high_max=50, critical_max=90, ceiling=100
        )
        assert band_for(thresholds.ceiling, thresholds) is RiskLevel.CRITICAL

    def test_honours_a_reconfigured_band_table(self) -> None:
        thresholds = RiskThresholds(
            medium_max=0, high_max=10, critical_max=20, ceiling=50
        )
        assert band_for(0, thresholds) is RiskLevel.LOW
        assert band_for(1, thresholds) is RiskLevel.MEDIUM
        assert band_for(50, thresholds) is RiskLevel.CRITICAL


# -- Score arithmetic ----------------------------------------------------


class TestScoreFromContributions:
    def test_sums_contributions(self) -> None:
        assert score_from_contributions((10, 20, 5)) == 35

    def test_an_empty_bundle_scores_zero(self) -> None:
        assert score_from_contributions(()) == 0

    def test_de_duplicated_factors_do_not_raise_the_score(self) -> None:
        assert score_from_contributions((25, 0, 0)) == 25

    def test_a_negative_contribution_cannot_reduce_the_score(self) -> None:
        """No finding may lower the risk score (D-020)."""
        assert score_from_contributions((30, -25)) == 30

    def test_all_negative_contributions_still_score_zero(self) -> None:
        assert score_from_contributions((-10, -20)) == 0


class TestApplyCeiling:
    def test_a_score_below_the_ceiling_is_unchanged(self) -> None:
        thresholds = RiskThresholds(
            medium_max=20, high_max=50, critical_max=90, ceiling=100
        )
        assert apply_ceiling(42, thresholds) == 42

    def test_a_score_above_the_ceiling_is_clamped_not_scaled(self) -> None:
        thresholds = RiskThresholds(
            medium_max=20, high_max=50, critical_max=90, ceiling=100
        )
        assert apply_ceiling(250, thresholds) == 100

    def test_two_documents_far_past_the_ceiling_score_identically(self) -> None:
        """This is what makes one score comparable with another."""
        thresholds = RiskThresholds(
            medium_max=20, high_max=50, critical_max=90, ceiling=100
        )
        assert apply_ceiling(150, thresholds) == apply_ceiling(900, thresholds)

    def test_a_negative_raw_score_is_floored_at_zero(self) -> None:
        thresholds = RiskThresholds(
            medium_max=20, high_max=50, critical_max=90, ceiling=100
        )
        assert apply_ceiling(-5, thresholds) == 0


class TestLevelForScore:
    def test_returns_the_capped_score_and_its_band_together(self) -> None:
        thresholds = RiskThresholds(
            medium_max=20, high_max=50, critical_max=90, ceiling=100
        )
        assert level_for_score(25, thresholds) == (25, RiskLevel.MEDIUM)

    def test_score_and_level_are_always_derived_from_the_same_number(self) -> None:
        """A report can never show a score and a level that disagree."""
        thresholds = RiskThresholds(
            medium_max=20, high_max=50, critical_max=90, ceiling=100
        )
        for raw in range(0, 260, 7):
            score, level = level_for_score(raw, thresholds)
            assert score == min(raw, thresholds.ceiling)
            assert level is band_for(score, thresholds)

    def test_bands_the_capped_value_not_the_raw_one(self) -> None:
        """A raw 200 is still capped to 100, and 100 is CRITICAL either way."""
        thresholds = RiskThresholds(
            medium_max=20, high_max=50, critical_max=90, ceiling=100
        )
        assert level_for_score(200, thresholds) == (100, RiskLevel.CRITICAL)


class TestEnvExampleMirror:
    """D-007 requires every weight and band to be overridable from the environment.

    A `Settings` field with no `.env.example` entry is still configurable in code
    but not in a deployment, and nothing else in the suite would notice. These
    tests keep the two in step.
    """

    ENV_EXAMPLE = (
        Path(__file__).resolve().parents[2] / ".env.example"
    )

    def declared_env_names(self) -> set[str]:
        """Return every uncommented ``KEY=value`` name in `.env.example`."""
        names: set[str] = set()
        for line in self.ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            names.add(stripped.split("=", 1)[0].strip())
        return names

    def test_the_env_example_exists(self) -> None:
        assert self.ENV_EXAMPLE.is_file(), self.ENV_EXAMPLE

    @pytest.mark.parametrize("field", RISK_SETTING_FIELDS)
    def test_every_risk_setting_is_mirrored(self, field: str) -> None:
        assert field.upper() in self.declared_env_names(), field

    @pytest.mark.parametrize("field", RISK_SETTING_FIELDS)
    def test_the_mirrored_value_matches_the_default(self, field: str) -> None:
        """A stale value in the example file is worse than a missing one."""
        expected = getattr(Settings(_env_file=None), field)
        actual: int | None = None
        for line in self.ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{field.upper()}="):
                actual = int(stripped.split("=", 1)[1])
                break
        assert actual == expected, f"{field}: .env.example says {actual}, default is {expected}"

    def test_no_env_entry_is_left_without_a_settings_field(self) -> None:
        """Catches a renamed or deleted setting still sitting in the example."""
        known = set(Settings.model_fields)
        for name in sorted(self.declared_env_names()):
            assert name.lower() in known, name
