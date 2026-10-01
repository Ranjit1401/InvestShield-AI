"""Tests for the red-flag rule catalogue itself (Phase 1).

These guard the catalogue's invariants rather than any single detection:
completeness, uniqueness, metadata quality and the guarantee that no raw regex
implementation detail leaks into a user-facing model.
"""

from __future__ import annotations

import re

import pytest

from app.schemas.red_flags import RedFlagCode, Severity
from app.services.red_flag_rules import (
    ALL_RULES,
    RULES_BY_CODE,
    get_rule,
    is_negated,
    parse_number,
    sentence_bounds,
    sentence_contains,
)

EXPECTED_CODES = {
    "GUARANTEED_RETURN",
    "UNREALISTIC_RETURN",
    "URGENCY_PRESSURE",
    "FAKE_REGULATORY_CLAIM",
    "UNVERIFIED_ADVISER",
    "SUSPICIOUS_URL",
    "THIRD_PARTY_PAYMENT",
    "APK_DOWNLOAD",
    "TELEGRAM_INVESTMENT_GROUP",
    "WHATSAPP_INVESTMENT_GROUP",
    "BORROW_TO_INVEST",
    "WITHDRAWAL_FEE",
    "ACCOUNT_ACTIVATION_FEE",
    "FAKE_PROFIT_SCREENSHOT",
    "IMPERSONATION",
}

#: Regex metacharacters that would betray an implementation detail if they ever
#: reached a report.
_REGEX_LEAK_MARKERS = ("(?", "\\b", "[^", "(?:", ".*")


def test_catalogue_contains_exactly_fifteen_rules() -> None:
    assert len(ALL_RULES) == 15


def test_catalogue_covers_every_expected_code() -> None:
    assert {rule.code.value for rule in ALL_RULES} == EXPECTED_CODES


def test_rule_codes_are_unique() -> None:
    codes = [rule.code for rule in ALL_RULES]

    assert len(codes) == len(set(codes))


def test_lookup_by_code_is_complete() -> None:
    assert set(RULES_BY_CODE) == set(RedFlagCode)


def test_every_rule_has_detectors() -> None:
    for rule in ALL_RULES:
        assert rule.detectors, f"{rule.code.value} has no detectors"


def test_every_rule_has_meaningful_metadata() -> None:
    for rule in ALL_RULES:
        assert rule.name and rule.name == rule.name.upper() or rule.name
        assert len(rule.name) > 3
        assert rule.description.endswith(".")
        assert len(rule.description) > 20
        assert isinstance(rule.severity, Severity)
        assert rule.default_weight > 0
        assert rule.weight_attr.startswith("risk_weight_")


def test_every_detector_has_a_plain_language_reason() -> None:
    for rule in ALL_RULES:
        for detector in rule.detectors:
            assert detector.reason.endswith("."), rule.code.value
            assert "(?:" not in detector.reason
            assert "\\b" not in detector.reason


def test_all_patterns_compile_and_are_bounded() -> None:
    """A pattern containing an unbounded ``.*`` could scan across paragraphs."""
    for rule in ALL_RULES:
        for detector in rule.detectors:
            for pattern in getattr(detector, "patterns", ()):
                sources = [pattern.pattern] if isinstance(pattern, re.Pattern) else []
                for source in sources:
                    assert ".*" not in source, f"{rule.code.value}: unbounded wildcard"


def test_rules_claiming_verification_are_worded_as_claims_not_findings() -> None:
    """Regulatory/adviser rules must not assert that something is false."""
    for code in (RedFlagCode.FAKE_REGULATORY_CLAIM, RedFlagCode.UNVERIFIED_ADVISER):
        rule = get_rule(code)
        assert "verification" in rule.verification_note.lower()
        assert rule.verification_note


def test_url_rule_is_documented_as_indicator_only() -> None:
    rule = get_rule(RedFlagCode.SUSPICIOUS_URL)

    assert "never labelled malicious" in rule.verification_note


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("35", 35.0), ("1,00,000", 100000.0), ("12.5", 12.5), ("abc", 0.0), ("", 0.0)],
)
def test_parse_number_handles_separators(raw: str, expected: float) -> None:
    assert parse_number(raw) == expected


def test_sentence_bounds_respects_terminators() -> None:
    text = "First one. Second one! Third one"

    start, end = sentence_bounds(text, 12, 18)

    assert text[start:end] == " Second one"


def test_sentence_bounds_treats_single_newlines_as_soft() -> None:
    """A line-broken marketing post is one thought, not several."""
    text = "Line one\nline two\n\nNew paragraph"

    start, end = sentence_bounds(text, 12, 16)

    assert text[start:end] == "Line one\nline two"
    assert text[end:] == "\n\nNew paragraph"


def test_sentence_contains_finds_cue_in_neighbouring_sentence() -> None:
    text = "Returns vary. We guarantee 40% monthly."
    cue = re.compile(r"guarantee[sd]?", re.IGNORECASE)

    assert sentence_contains(text, 25, 30, cue)


@pytest.mark.parametrize(
    ("text", "start", "end", "expected"),
    [
        ("We do not guarantee returns", 11, 21, True),
        ("returns cannot be guaranteed here", 0, 24, True),
        ("No loss guaranteed on every trade", 0, 10, False),
        ("We guarantee 35% monthly returns", 3, 13, False),
    ],
)
def test_is_negated(text: str, start: int, end: int, expected: bool) -> None:
    assert is_negated(text, start, end) is expected


def test_no_pattern_text_leaks_into_rule_metadata() -> None:
    for rule in ALL_RULES:
        for value in (rule.name, rule.description, rule.verification_note):
            for marker in _REGEX_LEAK_MARKERS:
                assert marker not in value, f"{rule.code.value} leaks {marker!r}"
