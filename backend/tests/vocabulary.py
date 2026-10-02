"""The judgement-and-advice vocabulary ban, shared by every suite that enforces it.

`tests/test_risk_safety.py` was written in Phase 6 and carried its own copy of this
machinery. Phase 10 needed the same matcher to check the **API and persistence**
layers, and importing it from a module named `test_risk_safety` is not possible — a
test module is not a library, and duplicating it would produce two matchers free to
disagree about what counts as a violation. So the machinery moved here and the
Phase 6 suite now imports it. One matcher, one definition of a violation.

## What is banned

**Verdicts and accusations** — `scam`, `fraud`, `criminal`, and the rest. This tool
reports documented indicators. It does not decide what anyone is.

**Absolution** — `safe`, `legitimate`, `genuine`, `trustworthy`. The symmetric
risk. A system that cannot accuse can still mislead by implying clearance, and "no
red flags found" must never become "this is safe".

**Advice** — `buy`, `sell`, `invest`, `recommend`, `avoid`. The product is not an
adviser. It says what was found and stops.

## Denials are permitted

The product's own wording depends on this. The standing caveat says the score "is
not a probability **of fraud**"; Phase 1's `UNVERIFIED_ADVISER` rule says its
detection is "not a finding that the person is unverified or **fraudulent**". Both
name the banned thing in order to rule it out, and a plain word-boundary matcher
cannot tell a denial from an accusation.

`asserted_matches` therefore treats a match as a denial when a negation cue governs
it — a cue within the preceding window, with no sentence or clause boundary between
the cue and the word. The clause boundary matters: *"This is not proof of fraud, and
the scheme is a scam."* reports the scam, while *"not a finding that the person is
unverified or fraudulent"* forgives the fraudulent.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

__all__ = [
    "ADVICE_PATTERN",
    "BANNED_ADVICE_WORDS",
    "BANNED_JUDGEMENT_WORDS",
    "BANNED_PATTERN",
    "JUDGEMENT_PATTERN",
    "NEGATION_PATTERN",
    "NEGATION_WINDOW",
    "VERDICT_PATTERN",
    "assert_no_words",
    "asserted_matches",
    "phase_six_authored_texts",
]


#: Words that would mean Phase 6 had started judging someone, or implying that
#: someone had been cleared. Matched on whole words, case-insensitively.
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


#: The absolution half of the judgement ban, named separately so
#: :data:`VERDICT_PATTERN` can be built as the judgement words *minus* these.
_ABSOLUTION_WORDS = frozenset(
    {
        "safe",
        "safety",
        "legitimate",
        "legitimacy",
        "genuine",
        "trustworthy",
        "honest",
        "innocent",
    }
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

#: The accusation family alone: the words that name someone a wrongdoer.
#:
#: Split out because the two halves of the judgement ban fail in different places.
#: The accusation half is unbendable and applies to *everything* the product emits,
#: including long-form documentation: a document that calls a pattern "a scam" is
#: making the accusation the product refuses to make.
#:
#: The absolution half — `safe`, `legitimate`, `genuine`, `honest` — is enforced on
#: generated output, where the fields are short machine-authored labels and "safe"
#: would be a real defect. It is **not** enforced on documentation prose, because
#: describing this product's own stance unavoidably uses those words about the tool
#: rather than about the user's subject: "the honest answer is no evidence", "an
#: explainable investor-safety report", "an empty evidence tuple is a legitimate,
#: meaningful result". Distinguishing those from a clearance verdict needs
#: judgement, not a word list, and a word list that tried would have to be an
#: allowlist of approved sentences.
VERDICT_PATTERN = _pattern(
    word for word in BANNED_JUDGEMENT_WORDS if word not in _ABSOLUTION_WORDS
)


#: Cues that turn a following banned word into a denial of it.
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
#: assertion in the next, so a full stop, semicolon or coordinating conjunction all
#: end its reach.
#:
#: A **comma does not**, and that is deliberate. "It is not a probability of fraud,
#: of financial loss, or of the investment failing" is one negation governing a list,
#: and a comma-based boundary would misread the second and third items as fresh
#: assertions. The same holds for "nothing about safety, fraud or the merits of an
#: investment". The conjunction list is what stops this rule from excusing "not
#: proof of fraud, **and** the scheme is a scam".
_CLAUSE_BREAK = re.compile(
    r"[.;:]|"
    r"\b(?:and|but|however|although|though|while|whereas|yet|nonetheless|nevertheless)\b",
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


def phase_six_authored_texts(assessment) -> list[str]:
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
    from app.schemas.risk import RiskFactorOrigin

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
