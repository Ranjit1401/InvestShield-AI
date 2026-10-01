"""Deterministic red-flag detection engine (Phase 1).

The engine is pure code: no LLM, no network, no randomness. Given the same text
and settings it always returns the same findings, which is what makes red-flag
detection auditable and testable (D-008).

Ordering contract
-----------------
Results are sorted deterministically by:

1. ``severity`` descending (CRITICAL -> LOW),
2. ``weight`` descending,
3. ``code`` ascending (alphabetical, so ties never depend on dict order).

Deduplication contract
----------------------
One logical finding per :class:`~app.schemas.red_flags.RedFlagCode`. When several
patterns of the same rule match, the **strongest** occurrence becomes
``evidence_span``/``matched_text`` (lowest detector priority, then longest match,
then earliest position) and the rest are preserved in ``additional_spans``.

The engine detects *language and indicators only*. It never decides whether a
claim is true — that is the Verification Agent's job in Phase 4 (D-006).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.schemas.red_flags import EvidenceSpan, RedFlag, RedFlagCode
from app.services.red_flag_rules import ALL_RULES, RULES_BY_CODE, RedFlagRule

logger = get_logger(__name__)


@dataclass(frozen=True)
class _Candidate:
    """A raw detection before rule metadata is attached.

    Attributes:
        start: Match start offset in the submitted text.
        end: Match end offset in the submitted text.
        reason: Plain-language reason from the detector that fired.
        priority: Detector priority; lower means a stronger signal.
    """

    start: int
    end: int
    reason: str
    priority: int

    @property
    def span(self) -> tuple[int, int]:
        """Return the ``(start, end)`` offsets."""
        return (self.start, self.end)

    @property
    def sort_key(self) -> tuple[int, int, int]:
        """Return the ordering key used to pick the primary evidence span."""
        return (self.priority, -(self.end - self.start), self.start)


class RedFlagEngine:
    """Detects red-flag indicators in submitted investment content.

    Args:
        settings: Optional settings override, mainly for tests. Weights and
            numeric thresholds are read from it.

    Example:
        >>> engine = RedFlagEngine()
        >>> [f.code.value for f in engine.detect("We guarantee 35% monthly returns.")]
        ['UNREALISTIC_RETURN', 'GUARANTEED_RETURN']
    """

    def __init__(self, settings: Settings | None = None, rules: tuple[RedFlagRule, ...] | None = None) -> None:
        """Initialise the engine.

        Args:
            settings: Optional settings override.
            rules: Optional rule subset, mainly for focused tests.
        """
        self._settings = settings or get_settings()
        self._rules: tuple[RedFlagRule, ...] = rules or ALL_RULES

    @property
    def settings(self) -> Settings:
        """The settings this engine was built with."""
        return self._settings

    @property
    def rules(self) -> tuple[RedFlagRule, ...]:
        """The active rule catalogue."""
        return self._rules

    def weight_for(self, code: RedFlagCode) -> int:
        """Return the effective weight for a rule code.

        Args:
            code: The red-flag code.

        Returns:
            The configured weight, falling back to the rule's default when the
            setting is missing or invalid.
        """
        rule = RULES_BY_CODE[code]
        value = getattr(self._settings, rule.weight_attr, rule.default_weight)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return rule.default_weight

    def detect(self, text: str | None) -> list[RedFlag]:
        """Detect red flags in ``text``.

        Args:
            text: Submitted content. ``None`` or blank input yields no findings.

        Returns:
            Deterministically sorted, deduplicated red flags. An empty list is a
            valid and meaningful result: no indicator pattern matched. It does
            not mean the content is safe.
        """
        if not text or not text.strip():
            return []

        candidates: dict[RedFlagCode, list[_Candidate]] = defaultdict(list)
        for rule in self._rules:
            for detector in rule.detectors:
                try:
                    matches = detector.find(text, self._settings)
                except Exception:  # pragma: no cover - a broken rule must not kill the scan
                    logger.exception("Red-flag detector failed", extra={"rule": rule.code.value})
                    continue
                priority = int(getattr(detector, "priority", 50))
                for match in matches:
                    if match.end > match.start:
                        candidates[rule.code].append(
                            _Candidate(match.start, match.end, match.reason, priority)
                        )

        flags = [
            self._build_flag(code, candidates[code], text)
            for code in sorted(candidates, key=lambda item: item.value)
            if candidates[code]
        ]
        flags.sort(key=lambda flag: flag.sort_key)
        return flags

    def detect_codes(self, text: str | None) -> set[RedFlagCode]:
        """Return only the set of codes detected, without evidence detail.

        Args:
            text: Submitted content.

        Returns:
            Set of detected red-flag codes.
        """
        return {flag.code for flag in self.detect(text)}

    def _build_flag(self, code: RedFlagCode, matches: list[_Candidate], text: str) -> RedFlag:
        """Turn raw matches for one rule into a single deduplicated finding.

        Args:
            code: The rule code.
            matches: All candidate matches for that rule.
            text: The original submitted text, used to slice verbatim spans.

        Returns:
            One :class:`~app.schemas.red_flags.RedFlag`.
        """
        rule = RULES_BY_CODE[code]

        ordered: list[_Candidate] = []
        seen: set[tuple[int, int]] = set()
        for candidate in sorted(matches, key=lambda item: item.sort_key):
            if candidate.span in seen:
                continue
            seen.add(candidate.span)
            ordered.append(candidate)

        primary = ordered[0]
        primary_span = _span(primary, text)
        additional = [_span(item, text) for item in ordered[1:]]

        return RedFlag(
            code=rule.code,
            name=rule.name,
            description=rule.description,
            severity=rule.severity,
            weight=self.weight_for(rule.code),
            matched_text=primary_span.text,
            evidence_span=primary_span,
            rule_reason=primary.reason,
            additional_spans=additional,
        )


def _span(candidate: _Candidate, text: str) -> EvidenceSpan:
    """Slice the verbatim evidence span for a candidate.

    Args:
        candidate: The candidate match.
        text: The original submitted text.

    Returns:
        An :class:`~app.schemas.red_flags.EvidenceSpan` whose ``text`` is exactly
        ``text[start:end]``.
    """
    return EvidenceSpan(start=candidate.start, end=candidate.end, text=text[candidate.start : candidate.end])


__all__ = ["RedFlagEngine"]
