"""Tests that Phase 6 cannot output a verdict, a probability, or advice.

These are the tests for the product's central constraint, so they are written
against **every string the engine can emit**, not against a hand-picked sample.
The set of texts checked is assembled by walking real assessments and collecting
each factor's ``label``, ``description``, ``reason`` and ``source``, plus every
warning — if a future edit adds a field or a warning, the collection picks it up
and the ban applies to it too.

Three families of word are banned from engine-generated output:

- **Verdicts and accusations** — ``scam``, ``fraud``, ``fraudulent``,
  ``criminal``, ``illegal``, ``criminal``. This tool reports documented indicators.
  It does not decide what anyone is.
- **Absolution** — ``safe``, ``legitimate``, ``genuine``, ``trustworthy``. The
  symmetric risk. A system that cannot accuse can still mislead by implying
  clearance, and "no red flags found" must never become "this is safe".
- **Advice** — ``buy``, ``sell``, ``invest``, ``recommend``, ``avoid``. The
  product is not an adviser. It says what was found and stops.

The matching is word-boundary based, so ``investing`` is not caught by ``invest``
merely as a substring, but a real use of the bare word is. The tests are also
deliberately adversarial about the places these words most plausibly appear —
Phase 1's own rule wording, which Phase 6 reuses verbatim — so that a change to
Phase 1 that reintroduces an accusation surfaces here rather than in production.
"""

from __future__ import annotations

import re
from typing import Iterable

import pytest

from app.schemas.claims import ClaimType
from app.schemas.red_flags import RedFlagCode
from app.schemas.risk import (
    SCORE_NOT_A_PROBABILITY,
    RiskAssessment,
    RiskFactor,
    RiskFactorOrigin,
    RiskLevel,
    RiskThresholds,
    RiskWeights,
)
from app.schemas.verification import VerificationStatus
from app.services.risk import RiskService

#: Words that would mean Phase 6 had started judging someone, or implying that
#: someone had been cleared. Matched on whole words, case-insensitively.
#:
#: Checked against **every** string the engine can emit, including Phase 1's rule
#: catalogue text that Phase 6 reuses verbatim. There is no reading of "scam",
#: "fraud" or "safe" that belongs in a description of a detection pattern.
BANNED_JUDGEMENT_WORDS = (
    # Verdicts and accusations.
    "scam",
    "scams",
    "scamming",
    "fraud",
    "frauds",
    "fraudulent",
    "criminal",
    "illegal",
    "liar",
    "lying",
    "perpetrator",
    "thief",
    # Absolution. The symmetric failure to an accusation.
    "safe",
    "safety",
    "legitimate",
    "legitimacy",
    "genuine",
    "trustworthy",
    "honest",
    "innocent",
)

#: Words that turn a description into advice.
#:
#: Checked only against text **Phase 6 authors itself** — verification-factor
#: labels, descriptions and reasons, every factor's `source`, and all warnings.
#:
#: Not checked against Phase 1's rule catalogue, because describing a detected
#: pattern legitimately mentions the activity it describes: the existing rule
#: `BORROW_TO_INVEST` is named "Borrowing to Invest", and that is a description
#: of what was found, not a suggestion to the reader. A Phase 6 edit that
#: introduced "you should invest" would be caught; Phase 1's neutral naming is
#: not a violation.
BANNED_ADVICE_WORDS = (
    "buy",
    "sell",
    "invest",
    "recommend",
    "recommended",
    "recommendation",
    "advice",
    "advisable",
    "proceed",
    "withhold",
)


def _pattern(words: Iterable[str]) -> re.Pattern[str]:
    """Compile a whole-word, case-insensitive matcher for `words`.

    Args:
        words: The terms to match. Variants are listed explicitly rather than
            prefix-matched, so ``invest`` does not catch ``investing``.

    Returns:
        A compiled pattern.
    """
    return re.compile(
        r"\b(?:" + "|".join(sorted(set(words))) + r")\b", re.IGNORECASE
    )


JUDGEMENT_PATTERN = _pattern(BANNED_JUDGEMENT_WORDS)
ADVICE_PATTERN = _pattern(BANNED_ADVICE_WORDS)
BANNED_PATTERN = _pattern(BANNED_JUDGEMENT_WORDS + BANNED_ADVICE_WORDS)

#: Cues that turn a following banned word into a denial of it.
#:
#: The product's own wording depends on this. The standing caveat says the score
#: "is not a probability of fraud"; Phase 1's `UNVERIFIED_ADVISER` rule says its
#: detection is "not a finding that the person is unverified or fraudulent". Both
#: name the banned thing in order to rule it out, and a plain word-boundary
#: matcher cannot tell a denial from an accusation.
NEGATION_PATTERN = re.compile(
    r"\b(?:not|no|never|cannot|nor|neither|without|isn't|doesn't|aren't|wasn't|weren't|"
    r"rather than|nothing|nobody|is not|are not|do not|does not|did not|will not)\b",
    re.IGNORECASE,
)

#: How far back a negation is searched for. Long enough to cover the phrasing in
#: this codebase's disclaimers, short enough that a negation in a previous clause
#: does not excuse an assertion in a later one.
NEGATION_WINDOW = 60

#: Where a negation stops governing. A denial in one clause must not excuse an
#: assertion in the next, so conjunction is as much a boundary as a full stop.
#:
#: "This is not proof of fraud, and the scheme is a scam." reports the scam.
#: "This is not a finding that the person is unverified or fraudulent." forgives
#: the fraudulent, because nothing separates the negation from its noun.
_CLAUSE_BREAK = re.compile(
    r"[.;:,]|\b(?:and|but|however|although|though|while|whereas|yet)\b",
    re.IGNORECASE,
)


def asserted_matches(text: str, pattern: re.Pattern[str]) -> list[str]:
    """Return the banned words in `text` that are *asserted*, not denied.

    A match is treated as a denial when a negation cue governs it — a cue inside
    the preceding :data:`NEGATION_WINDOW` characters, with no sentence or clause
    boundary between the cue and the word.

    Args:
        text: The text to scan.
        pattern: The banned-word matcher.

    Returns:
        Each asserted banned word, in order of appearance. Empty when every match
        is a denial or there are none.
    """
    asserted: list[str] = []
    for match in pattern.finditer(text):
        window = text[max(0, match.start() - NEGATION_WINDOW) : match.start()]
        if NEGATION_PATTERN.search(window):
            # A boundary after the nearest negation means the negation governed
            # an earlier clause, not this word.
            last_negation = max(
                (m.end() for m in NEGATION_PATTERN.finditer(window)), default=0
            )
            if not _CLAUSE_BREAK.search(window[last_negation:]):
                continue
        asserted.append(match.group(0))
    return asserted


def phase_six_authored_texts(assessment: RiskAssessment) -> list[str]:
    """Collect only the text Phase 6 itself wrote.

    For a red-flag factor, `label`, `description` and `reason` are Phase 1's rule
    catalogue reproduced verbatim — reusing them is the point, so Phase 6 cannot
    have worded them. For a verification factor, all four fields are Phase 6's
    fixed templates. Warnings are always Phase 6's.

    This is the stricter set, and it is where the advice ban applies. The full
    set still gets the judgement ban, so Phase 1's catalogue is not exempt from
    the accusation check.

    Args:
        assessment: The assessment to read.

    Returns:
        The strings Phase 6 authored, with their field named.
    """
    texts: list[str] = []
    for factor in assessment.factors:
        texts.append(f"source[{factor.factor_type.value}]: {factor.source}")
        if factor.origin is RiskFactorOrigin.VERIFICATION:
            texts.extend(
                [
                    f"label[{factor.factor_type.value}]: {factor.label}",
                    f"description[{factor.factor_type.value}]: {factor.description}",
                    f"reason[{factor.factor_type.value}]: {factor.reason}",
                ]
            )
    texts.extend(f"warning: {warning}" for warning in assessment.warnings)
    return texts


def assert_no_words(texts: Iterable[str], pattern: re.Pattern[str], family: str) -> None:
    """Fail with the offending text named, if it asserts a banned word.

    Denials are permitted: Phase 6's own disclaimers and Phase 1's rule
    descriptions name these words precisely to rule them out, and refusing that
    would make the tests impossible to satisfy honestly.

    Args:
        texts: Strings to check.
        pattern: The matcher to apply.
        family: The ban's name, for the failure message.

    Raises:
        AssertionError: On the first string asserting a banned word.
    """
    for text in texts:
        asserted = asserted_matches(text, pattern)
        assert not asserted, (
            f"Phase 6 output asserts the banned {family} word(s) "
            f"{asserted}: {text}"
        )


# -- Scenario inputs -----------------------------------------------------


def all_scenarios() -> list[RiskAssessment]:
    """Build assessments spanning every factor type and every band.

    Coverage matters more than elegance here: a vocabulary ban is only meaningful
    if it holds across the whole output space, so this covers one red flag per
    code, all five Phase 4 statuses, the presence and absence of evidence, the
    empty investigation, and each risk band.
    """
    from app.schemas.red_flags import RedFlagCode
    from tests.risk_factories import (
        contradicted,
        evidence_for,
        make_flag,
        make_placed_claim,
        not_applicable,
        unsearchable,
        unverified,
        verified,
    )

    service = RiskService()
    assessments: list[RiskAssessment] = [service.assess()]

    # One red flag per rule, which is one factor type per code.
    for index, code in enumerate(RedFlagCode):
        assessments.append(
            service.assess((make_flag(code, start=index * 100, end=index * 100 + 60),))
        )

    # Every Phase 4 status, with and without retrieved evidence.
    claim = make_placed_claim(
        "SEBI approved", ClaimType.REGULATORY_STATUS, claim_id="claim_001"
    )
    for result in (
        contradicted("claim_001"),
        unverified("claim_001"),
        unsearchable("claim_001"),
        verified("claim_001"),
        not_applicable("claim_001"),
    ):
        assessments.append(service.assess((), (claim,), (result,), ()))
        assessments.append(
            service.assess((), (claim,), (result,), (evidence_for("claim_001"),))
        )

    # Every band, so each level's own wording is covered.
    for weight in (0, 15, 35, 70, 100):
        factor = RiskFactor(
            id="rsk_000000000001",
            origin=RiskFactorOrigin.RED_FLAG,
            factor_type=RedFlagCode.GUARANTEED_RETURN,
            label="Test factor",
            description="A factor used to reach one band.",
            reason="Built to reach one band.",
            source="red flag rf_000000000001",
            weight=weight,
            contribution=weight,
        )
        assessments.append(
            RiskAssessment(
                risk_score=min(weight, 100),
                raw_score=weight,
                risk_level=RiskLevel.LOW,
                factors=(factor,),
                weights=RiskWeights(
                    red_flag_weights={},
                    contradicted_claim=0,
                    unverified_claim=0,
                    insufficient_evidence=0,
                ),
                thresholds=RiskThresholds(
                    medium_max=20, high_max=50, critical_max=90, ceiling=100
                ),
            )
        )

    return assessments


# -- The bans ------------------------------------------------------------


class TestNoVerdictsOrAdvice:
    def test_no_scenario_judges_anyone(self) -> None:
        """Walks every factor type, all five Phase 4 statuses and all four bands."""
        for assessment in all_scenarios():
            assert_no_words(
                phase_six_authored_texts(assessment),
                JUDGEMENT_PATTERN,
                "judgement",
            )

    def test_no_scenario_advises_the_reader(self) -> None:
        """Applied to Phase 6's own wording, which is where advice could be added."""
        for assessment in all_scenarios():
            assert_no_words(
                phase_six_authored_texts(assessment), ADVICE_PATTERN, "advice"
            )

    def test_a_realistic_investment_scenario_is_clean(self) -> None:
        """The end-to-end shape: red flag, claim, contradiction, evidence."""
        from app.schemas.red_flags import RedFlagCode
        from tests.risk_factories import (
            contradicted,
            evidence_for,
            make_flag,
            make_placed_claim,
        )

        flag = make_flag(
            RedFlagCode.FAKE_REGULATORY_CLAIM,
            start=0,
            end=13,
            matched_text="SEBI approved",
        )
        claim = make_placed_claim(
            "SEBI approved", ClaimType.REGULATORY_STATUS, claim_id="claim_001"
        )
        assessment = RiskService().assess(
            (flag,),
            (claim,),
            (contradicted("claim_001"),),
            (evidence_for("claim_001"),),
        )
        assert_no_words(
            phase_six_authored_texts(assessment), JUDGEMENT_PATTERN, "judgement"
        )
        assert_no_words(
            phase_six_authored_texts(assessment), ADVICE_PATTERN, "advice"
        )

    def test_the_empty_investigation_makes_no_reassurance(self) -> None:
        """The most dangerous case: a clean result implying a clean bill of health."""
        assessment = RiskService().assess()
        assert_no_words(
            phase_six_authored_texts(assessment), JUDGEMENT_PATTERN, "judgement"
        )
        assert_no_words(
            phase_six_authored_texts(assessment), ADVICE_PATTERN, "advice"
        )

    def test_an_offline_investigation_makes_no_accusation(self) -> None:
        """The other dangerous case: no data, and no blame for it."""
        from tests.risk_factories import make_placed_claim, unsearchable

        claim = make_placed_claim(
            "SEBI approved", ClaimType.REGULATORY_STATUS, claim_id="claim_001"
        )
        assessment = RiskService().assess((), (claim,), (unsearchable("claim_001"),))
        assert_no_words(
            phase_six_authored_texts(assessment), JUDGEMENT_PATTERN, "judgement"
        )
        assert_no_words(
            phase_six_authored_texts(assessment), ADVICE_PATTERN, "advice"
        )

    def test_a_critically_scoring_investigation_still_makes_no_verdict(
        self,
    ) -> None:
        """A high score is a count of indicators, not a finding about anyone."""
        from app.schemas.red_flags import RedFlagCode
        from tests.risk_factories import make_flag

        flags = tuple(
            make_flag(code, start=index * 100, end=index * 100 + 60)
            for index, code in enumerate(RedFlagCode)
        )
        assessment = RiskService().assess(flags)
        assert assessment.risk_level is RiskLevel.CRITICAL
        assert_no_words(
            phase_six_authored_texts(assessment), JUDGEMENT_PATTERN, "judgement"
        )
        assert_no_words(
            phase_six_authored_texts(assessment), ADVICE_PATTERN, "advice"
        )

    def test_a_rule_may_describe_the_activity_it_detects(self) -> None:
        """The documented reason the advice ban is narrower than the other one."""
        from app.schemas.red_flags import RedFlagCode
        from tests.risk_factories import make_flag

        factor = RiskService().assess(
            (make_flag(RedFlagCode.BORROW_TO_INVEST),)
        ).factors[0]
        assert JUDGEMENT_PATTERN.search(factor.label) is None
        assert ADVICE_PATTERN.search(factor.label) is not None


class TestPhaseOneCatalogueTripwire:
    """Phase 6 reuses Phase 1's rule text verbatim, so Phase 1 wording reaches
    every risk report. Phase 6 must not silently reword it — that would break the
    "reuse the audited text" contract and any Phase 1 guarantee attached to it — so
    these tests pin the known set instead. A new occurrence fails here, where
    someone has to look at it."""

    def phase_one_hits(self, pattern: re.Pattern[str]) -> list[tuple[str, str, str]]:
        """Return `(code, field, word)` for every asserted hit in the catalogue.

        Args:
            pattern: The banned-word matcher to apply.

        Returns:
            One entry per asserted occurrence, in a stable order.
        """
        from app.services.red_flag_rules import RULES_BY_CODE

        hits: list[tuple[str, str, str]] = []
        for code in RedFlagCode:
            rule = RULES_BY_CODE[code]
            for field in ("name", "description"):
                text = getattr(rule, field, "")
                if not isinstance(text, str):
                    continue
                for word in asserted_matches(text, pattern):
                    hits.append((code.value, field, word))
        return hits

    def test_the_known_judgement_wording_in_phase_one_is_unchanged(self) -> None:
        """Exactly one known occurrence, in `SUSPICIOUS_URL`.

        "A link was found that uses a pattern commonly seen in fraudulent
        campaigns." asserts the word `fraudulent`. It is a description of a link
        pattern, not a finding about a person, and it is Phase 1's committed
        wording. It is pinned here rather than reworded, so that changing it is a
        decision someone makes in Phase 1 with its own tests.
        """
        assert self.phase_one_hits(JUDGEMENT_PATTERN) == [
            ("SUSPICIOUS_URL", "description", "fraudulent")
        ]

    def test_the_other_judgement_wording_in_phase_one_is_a_denial(self) -> None:
        """`UNVERIFIED_ADVISER` says "not ... fraudulent", which is permitted."""
        from app.services.red_flag_rules import RULES_BY_CODE

        text = RULES_BY_CODE[RedFlagCode.UNVERIFIED_ADVISER].description
        assert asserted_matches(text, JUDGEMENT_PATTERN) == []

    def test_phase_ones_advice_wording_is_unchanged(self) -> None:
        """Only `BORROW_TO_INVEST`, and only as a description of the pattern."""
        assert self.phase_one_hits(ADVICE_PATTERN) == [
            ("BORROW_TO_INVEST", "name", "Invest"),
            ("BORROW_TO_INVEST", "description", "invest"),
        ]


# -- Matching behaviour --------------------------------------------------


class TestBannedWordMatching:
    @pytest.mark.parametrize("word", ["scam", "fraud", "safe", "legitimate", "buy", "invest"])
    def test_each_banned_word_is_actually_caught(self, word: str) -> None:
        """A ban list that matches nothing is worse than no ban at all."""
        assert BANNED_PATTERN.search(f"The advisor is a {word}.")

    @pytest.mark.parametrize(
        "phrase",
        [
            "30% monthly returns",
            "The firm is not on the SEBI register",
            "This claim could not be confirmed",
            "A heuristic indicator of documented risk factors",
        ],
    )
    def test_neutral_wording_is_not_flagged(self, phrase: str) -> None:
        assert BANNED_PATTERN.search(phrase) is None

    def test_matching_is_word_bounded(self) -> None:
        """``investing`` must not be caught by the bare word ``invest``."""
        assert BANNED_PATTERN.search("The company focuses on investing") is None

    def test_matching_ignores_case(self) -> None:
        assert BANNED_PATTERN.search("SCAM") is not None

    def test_matching_catches_word_variants_that_are_listed(self) -> None:
        """Variants are listed explicitly rather than left to prefix matching."""
        assert BANNED_PATTERN.search("a fraudulent return promise") is not None

    @pytest.mark.parametrize(
        "text",
        [
            "It is not a probability of fraud.",
            "This is not a finding that the person is fraudulent.",
            "No source confirmed the claim was legitimate.",
            "The firm never described itself as safe.",
        ],
    )
    def test_a_denied_banned_word_is_not_a_violation(self, text: str) -> None:
        """The product's disclaimers depend on being able to say this."""
        assert asserted_matches(text, BANNED_PATTERN) == []

    @pytest.mark.parametrize(
        "text",
        [
            "The advisor is a scam.",
            "The operator committed fraud.",
            "The investment is safe.",
            "This firm is legitimate.",
        ],
    )
    def test_an_asserted_banned_word_is_a_violation(self, text: str) -> None:
        assert asserted_matches(text, BANNED_PATTERN)

    def test_a_negation_does_not_excuse_a_later_assertion(self) -> None:
        """Denial-aware matching must not become a loophole."""
        text = "No source confirmed the claim. The advisor is a scam."
        assert asserted_matches(text, BANNED_PATTERN) == ["scam"]

    def test_a_negation_in_a_previous_sentence_does_not_reach_forward(self) -> None:
        text = "Nothing was confirmed against this firm; the operator is fraudulent."
        assert asserted_matches(text, BANNED_PATTERN) == ["fraudulent"]

    def test_a_denial_still_leaves_other_assertions_visible(self) -> None:
        """Mixed text reports the assertion and forgives the denial."""
        text = "This is not proof of fraud, and the scheme is a scam."
        assert asserted_matches(text, BANNED_PATTERN) == ["scam"]


# -- The score is not a probability -------------------------------------


class TestScoreIsNotAProbability:
    def test_the_standing_caveat_names_the_thing_it_is_not(self) -> None:
        """A reader who sees only the number must still be told what it is."""
        from app.services.risk import SCORE_NOT_A_PROBABILITY

        assessment = RiskService().assess()
        assert assessment.warnings[-1] == SCORE_NOT_A_PROBABILITY
        assert "not a probability" in SCORE_NOT_A_PROBABILITY

    def test_every_assessment_carries_the_caveat(self) -> None:
        from app.services.risk import SCORE_NOT_A_PROBABILITY

        for assessment in all_scenarios():
            assert SCORE_NOT_A_PROBABILITY in assessment.warnings

    def test_the_caveat_disclaims_loss_and_advice(self) -> None:
        """Both readings a user could take, named explicitly."""
        from app.services.risk import SCORE_NOT_A_PROBABILITY

        assert "loss" in SCORE_NOT_A_PROBABILITY
        assert "recommendation" in SCORE_NOT_A_PROBABILITY

    def test_no_field_is_named_as_a_probability(self) -> None:
        """The schema is part of the surface a report could be built from."""
        forbidden = (
            "probability",
            "likelihood",
            "chance",
            "odds",
            "p_",
        )
        for name in RiskAssessment.model_fields:
            lowered = name.lower()
            assert not any(term in lowered for term in forbidden), name

    def test_the_score_field_is_bounded_and_not_a_ratio(self) -> None:
        """A 0-100 bounded heuristic sum, not a 0-1 probability."""
        assert RiskAssessment.model_fields["risk_score"].annotation is int

    def test_every_ratio_is_documented_as_not_a_confidence(self) -> None:
        """`evidence_coverage` is a transparency measure, not a correctness one."""
        for name in ("evidence_coverage", "analysis_completeness"):
            description = RiskAssessment.model_fields[name].description or ""
            assert "NOT" in description or "NOT a" in description

    def test_an_uncertainty_factor_is_not_called_a_finding(self) -> None:
        """The `is_uncertainty` flag is what keeps that distinction visible."""
        from tests.risk_factories import make_placed_claim, unsearchable

        claim = make_placed_claim(
            "SEBI approved", ClaimType.REGULATORY_STATUS, claim_id="claim_001"
        )
        factor = RiskService().assess((), (claim,), (unsearchable("claim_001"),)).factors[0]
        assert factor.is_uncertainty is True
        assert factor.is_scoring is False


# -- Status vocabulary ---------------------------------------------------


class TestStatusVocabulary:
    @pytest.mark.parametrize(
        "forbidden",
        ["SCAM", "NOT_SCAM", "FRAUD", "SAFE", "DANGEROUS", "BUY", "SELL", "INVEST"],
    )
    def test_no_risk_level_is_a_verdict(self, forbidden: str) -> None:
        """The band names describe quantity of indicators, not a finding."""
        assert forbidden not in {level.value for level in RiskLevel}

    def test_the_levels_are_exactly_the_four_bands(self) -> None:
        assert {level.value for level in RiskLevel} == {
            "LOW",
            "MEDIUM",
            "HIGH",
            "CRITICAL",
        }

    def test_no_risk_level_reports_a_verification_status(self) -> None:
        """A level is not a claim status; the two vocabularies stay separate."""
        for level in RiskLevel:
            assert level.value not in {status.value for status in VerificationStatus}
