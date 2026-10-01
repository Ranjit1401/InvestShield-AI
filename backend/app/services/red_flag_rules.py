"""Centralised red-flag rule catalogue (Phase 1).

All red-flag knowledge lives in this module: patterns, thresholds, severities,
weights and plain-language reasons. Nothing else in the codebase should contain
an investment-scam regex.

Design rules enforced here:

* **Deterministic** — no randomness, no LLM, no network. The same input always
  produces the same findings.
* **Explainable** — every finding carries a human-readable ``rule_reason``.
  Pattern strings never reach the report.
* **False-positive aware** — negation handling, sentence-level exclusion cues,
  and context requirements (a bare "Telegram" or a bare "return" is not a red
  flag).
* **Configurable** — weights and thresholds come from
  :class:`~app.core.config.Settings`; patterns are data, not literals spread
  across code.

Severity is declared per rule and is intentionally *not* environment-tunable:
severity expresses the intrinsic seriousness of the indicator type, while
weights express how much it contributes to the risk score.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.schemas.red_flags import RedFlagCode, Severity

# ---------------------------------------------------------------------------
# Shared text helpers
# ---------------------------------------------------------------------------

#: Boundaries of a "sentence" for evidence/exclusion purposes. Single line
#: breaks are treated as soft (marketing posts are line-broken), while blank
#: lines and sentence terminators separate one thought from the next.
#:
#: Public because Phase 2's claim extractor segments text with the identical
#: definition; keeping one definition prevents the two stages from disagreeing
#: about where a sentence ends.
SENTENCE_BOUNDARY = re.compile(r"[.!?।]|\.\.\.|\n\s*\n")

#: Backwards-compatible private alias.
_SENTENCE_SPLIT = SENTENCE_BOUNDARY

#: Cue words that turn a phrase into a disclaimer rather than a promise.
#: Only checked in a short window immediately before a match.
_NEGATION_CUES = re.compile(
    # NOTE: the `no` alternative excludes "no loss", which is itself claim
    # language ("no loss guaranteed"), not a disclaimer.
    r"\b(?:not|no(?!\s+loss)|never|cannot|can't|won't|wouldn't|shouldn't|doesn't|does|do|did|"
    r"isn't|aren't|wasn't|weren't|without|neither|nor|avoid|avoiding|"
    r"past performance|history|neither\s+nor)\b"
    r"|नहीं|नही|नहि",
    re.IGNORECASE,
)

#: How far back to look for a negation cue. Kept short so that an unrelated
#: "no" earlier in the same message cannot silence a genuine claim.
NEGATION_WINDOW = 40


def sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Return the sentence containing ``text[start:end]``.

    Args:
        text: The full input string.
        start: Match start offset.
        end: Match end offset.

    Returns:
        ``(sentence_start, sentence_end)`` offsets.
    """
    prefix = text[:start]
    cuts = list(_SENTENCE_SPLIT.finditer(prefix))
    sentence_start = cuts[-1].end() if cuts else 0
    tail = _SENTENCE_SPLIT.search(text, end)
    sentence_end = tail.start() if tail else len(text)
    return sentence_start, sentence_end


def is_negated(text: str, start: int, end: int | None = None, window: int = NEGATION_WINDOW) -> bool:
    """Return True when a negation cue surrounds a match.

    Used to distinguish a claim from a disclaimer. Cues are looked for both in a
    short window *before* the match ("not guaranteed returns") and *inside* it
    ("returns cannot be guaranteed"), because disclaimers use both orders.

    Args:
        text: The full input string.
        start: Match start offset.
        end: Match end offset. Defaults to ``start`` (before-window only).
        window: Number of characters to inspect before the match.

    Returns:
        Whether the match appears to be negated.
    """
    stop = start if end is None else max(end, start)
    prefix = text[max(0, start - window) : stop]
    return bool(_NEGATION_CUES.search(prefix))


def sentence_contains(text: str, start: int, end: int, cue: re.Pattern[str]) -> bool:
    """Return True when ``cue`` appears inside the match's sentence.

    Args:
        text: The full input string.
        start: Match start offset.
        end: Match end offset.
        cue: Compiled pattern to look for.

    Returns:
        Whether the cue occurs in the surrounding sentence.
    """
    sentence_start, sentence_end = sentence_bounds(text, start, end)
    return bool(cue.search(text, sentence_start, sentence_end))


def parse_number(raw: str) -> float:
    """Parse a numeric token that may contain Indian or Western separators.

    Args:
        raw: For example ``"35"``, ``"1,00,000"`` or ``"12.5"``.

    Returns:
        The parsed float, or ``0.0`` when the token is not numeric.
    """
    cleaned = raw.replace(",", "").replace("_", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# Detector protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawMatch:
    """A candidate detection before rule metadata is attached."""

    start: int
    end: int
    reason: str


class Detector(Protocol):
    """Finds candidate matches for one aspect of a rule."""

    reason: str
    priority: int

    def find(self, text: str, settings: object) -> list[RawMatch]:
        """Return every candidate match in ``text``."""


def _compile(pattern: str) -> re.Pattern[str]:
    """Compile a case-insensitive, unicode-aware pattern.

    Args:
        pattern: Regular expression source.

    Returns:
        A compiled pattern with ``re.IGNORECASE | re.UNICODE``.
    """
    return re.compile(pattern, re.IGNORECASE | re.UNICODE)


@dataclass(frozen=True)
class RegexDetector:
    """Literal-pattern detector with optional negation and exclusion guards.

    Attributes:
        pattern: Compiled case-insensitive pattern.
        reason: Plain-language explanation used when this detector fires.
        priority: Lower wins when choosing the primary evidence span.
        respect_negation: Drop matches preceded by a negation cue.
        exclusions: If any of these matches the match's sentence, drop it.
    """

    pattern: re.Pattern[str]
    reason: str
    priority: int = 10
    respect_negation: bool = True
    exclusions: tuple[re.Pattern[str], ...] = ()

    @classmethod
    def create(
        cls,
        pattern: str,
        reason: str,
        priority: int = 10,
        respect_negation: bool = True,
        exclusions: tuple[str, ...] = (),
    ) -> RegexDetector:
        """Build a detector from pattern source strings.

        Args:
            pattern: Regular expression source.
            reason: Plain-language explanation.
            priority: Lower wins for the primary evidence span.
            respect_negation: Whether negation cues suppress the match.
            exclusions: Patterns whose presence in the sentence cancels it.

        Returns:
            A configured :class:`RegexDetector`.
        """
        return cls(
            pattern=_compile(pattern),
            reason=reason,
            priority=priority,
            respect_negation=respect_negation,
            exclusions=tuple(_compile(item) for item in exclusions),
        )

    def find(self, text: str, settings: object) -> list[RawMatch]:
        """Return every match surviving the negation and exclusion guards."""
        results: list[RawMatch] = []
        for match in self.pattern.finditer(text):
            if self.respect_negation and is_negated(text, match.start(), match.end()):
                continue
            if any(sentence_contains(text, match.start(), match.end(), item) for item in self.exclusions):
                continue
            results.append(RawMatch(start=match.start(), end=match.end(), reason=self.reason))
        return results


@dataclass(frozen=True)
class PercentThresholdDetector:
    """Fires when a promised percentage return exceeds a configurable bound.

    Every pattern must expose exactly one capturing group holding the
    percentage. Historical/reporting language inside the sentence suppresses the
    match, because the rule targets *promissory* claims rather than past results.

    Attributes:
        reason: Plain-language explanation.
        patterns: Compiled patterns, each with one capturing group.
        threshold_attr: Name of the :class:`Settings` attribute holding the bound.
        priority: Lower wins for the primary evidence span.
        historical_cues: Patterns indicating a past/reported figure.
    """

    reason: str
    patterns: tuple[re.Pattern[str], ...]
    threshold_attr: str
    priority: int = 5
    historical_cues: tuple[re.Pattern[str], ...] = ()

    @classmethod
    def create(
        cls,
        patterns: tuple[str, ...],
        reason: str,
        threshold_attr: str,
        priority: int = 5,
    ) -> PercentThresholdDetector:
        """Build a threshold detector from pattern source strings.

        Args:
            patterns: Pattern sources, each capturing the percentage in group 1.
            reason: Plain-language explanation.
            threshold_attr: Settings attribute name holding the threshold.
            priority: Lower wins for the primary evidence span.

        Returns:
            A configured :class:`PercentThresholdDetector`.
        """
        return cls(
            reason=reason,
            patterns=tuple(_compile(item) for item in patterns),
            threshold_attr=threshold_attr,
            priority=priority,
            historical_cues=(
                _compile(
                    r"\b(?:past|previously|historically|historical|last\s+(?:year|month|quarter)|"
                    r"reported|recorded|realised|realized|achieved|since\s+\d{4}|"
                    r"year\s+ended|during\s+\d{4}|fiscal\s+\d{4})\b|\b(?:19|20)\d{2}\b"
                ),
            ),
        )

    def threshold(self, settings: object) -> float:
        """Return the configured percentage bound."""
        return float(getattr(settings, self.threshold_attr))

    def find(self, text: str, settings: object) -> list[RawMatch]:
        """Return matches whose captured percentage exceeds the threshold."""
        limit = self.threshold(settings)
        results: list[RawMatch] = []
        for pattern in self.patterns:
            for match in pattern.finditer(text):
                raw = match.group(1)
                if raw is None:
                    continue
                if parse_number(raw) <= limit:
                    continue
                if any(sentence_contains(text, match.start(), match.end(), cue) for cue in self.historical_cues):
                    continue
                results.append(RawMatch(start=match.start(), end=match.end(), reason=self.reason))
        return results


@dataclass(frozen=True)
class MultiplierDetector:
    """Fires on "N x in a period" growth claims above a configurable multiple."""

    reason: str
    patterns: tuple[re.Pattern[str], ...]
    threshold_attr: str
    priority: int = 6
    plain_word_multiples: tuple[str, ...] = ()
    word_multiples: dict[str, float] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        patterns: tuple[str, ...],
        reason: str,
        threshold_attr: str,
        plain_word_multiples: tuple[str, ...] = (),
        word_multiples: dict[str, float] | None = None,
        priority: int = 6,
    ) -> MultiplierDetector:
        """Build a multiplier detector.

        Args:
            patterns: Pattern sources capturing the multiplier in group 1.
            reason: Plain-language explanation.
            threshold_attr: Settings attribute holding the bound.
            plain_word_multiples: Word patterns that always fire (e.g. "double your money").
            word_multiples: Mapping of word to its numeric multiple.
            priority: Lower wins for the primary evidence span.

        Returns:
            A configured :class:`MultiplierDetector`.
        """
        return cls(
            reason=reason,
            patterns=tuple(_compile(item) for item in patterns),
            threshold_attr=threshold_attr,
            priority=priority,
            plain_word_multiples=tuple(_compile(item) for item in plain_word_multiples),
            word_multiples=word_multiples or {},
        )

    def threshold(self, settings: object) -> float:
        """Return the configured multiple bound."""
        return float(getattr(settings, self.threshold_attr))

    def find(self, text: str, settings: object) -> list[RawMatch]:
        """Return matches whose implied growth exceeds the threshold."""
        limit = self.threshold(settings)
        results: list[RawMatch] = []

        for pattern in self.patterns:
            for match in pattern.finditer(text):
                raw = match.group(1)
                if raw is not None and parse_number(raw) >= limit:
                    results.append(RawMatch(match.start(), match.end(), self.reason))

        for pattern in self.plain_word_multiples:
            for match in pattern.finditer(text):
                results.append(RawMatch(match.start(), match.end(), self.reason))

        for word, multiple in self.word_multiples.items():
            if multiple < limit:
                continue
            for match in _compile(rf"\b{word}\b").finditer(text):
                results.append(RawMatch(match.start(), match.end(), self.reason))

        return results


@dataclass(frozen=True)
class CurrencyGrowthDetector:
    """Fires on ``₹10,000 becomes ₹1,00,000`` style multiplication claims."""

    reason: str
    pattern: re.Pattern[str]
    threshold_attr: str
    priority: int = 4

    @classmethod
    def create(cls, reason: str, threshold_attr: str, priority: int = 4) -> CurrencyGrowthDetector:
        """Build the currency-growth detector.

        Args:
            reason: Plain-language explanation.
            threshold_attr: Settings attribute holding the growth multiple bound.
            priority: Lower wins for the primary evidence span.

        Returns:
            A configured :class:`CurrencyGrowthDetector`.
        """
        return cls(
            reason=reason,
            pattern=_compile(
                r"(?:₹|rs\.?|inr)\s?([\d,]+(?:\.\d+)?)"
                r"[^.\n]{0,40}?(?:becomes?|turns\s+into|grows\s+to|goes\s+to|"
                r"in\s+just|in\s+only|to)\s*"
                r"(?:₹|rs\.?|inr)\s?([\d,]+(?:\.\d+)?)"
            ),
            threshold_attr=threshold_attr,
            priority=priority,
        )

    def find(self, text: str, settings: object) -> list[RawMatch]:
        """Return matches where the claimed growth exceeds the bound."""
        limit = float(getattr(settings, self.threshold_attr))
        results: list[RawMatch] = []
        for match in self.pattern.finditer(text):
            before = parse_number(match.group(1))
            after = parse_number(match.group(2))
            if before <= 0:
                continue
            if (after / before) >= limit:
                results.append(RawMatch(match.start(), match.end(), self.reason))
        return results


@dataclass(frozen=True)
class ShortPeriodProfitDetector:
    """Fires on very large profits promised over a very short period."""

    reason: str
    pattern: re.Pattern[str]
    pct_attr: str
    days_attr: str
    priority: int = 4

    @classmethod
    def create(
        cls,
        pattern: str,
        reason: str,
        pct_attr: str,
        days_attr: str,
        priority: int = 4,
    ) -> ShortPeriodProfitDetector:
        """Build the short-period profit detector.

        Args:
            pattern: Source capturing ``(percentage, day-count)``.
            reason: Plain-language explanation.
            pct_attr: Settings attribute for the minimum percentage.
            days_attr: Settings attribute for the maximum day count.
            priority: Lower wins for the primary evidence span.

        Returns:
            A configured :class:`ShortPeriodProfitDetector`.
        """
        return cls(
            reason=reason,
            pattern=_compile(pattern),
            pct_attr=pct_attr,
            days_attr=days_attr,
            priority=priority,
        )

    def find(self, text: str, settings: object) -> list[RawMatch]:
        """Return matches satisfying both the percentage and duration bounds."""
        min_pct = float(getattr(settings, self.pct_attr))
        max_days = int(getattr(settings, self.days_attr))
        results: list[RawMatch] = []
        for match in self.pattern.finditer(text):
            pct = parse_number(match.group(1))
            days = parse_number(match.group(2))
            if pct >= min_pct and 0 < days <= max_days:
                results.append(RawMatch(match.start(), match.end(), self.reason))
        return results


@dataclass(frozen=True)
class ContextKeywordDetector:
    """Fires when an anchor is followed by a context keyword in a nearby window.

    Used for rules that must not fire on a bare mention — for example Telegram
    is only an indicator when the surrounding text is about investing.

    Attributes:
        anchor: Compiled pattern for the primary entity (e.g. ``telegram``).
        keywords: Compiled patterns that supply the investment context.
        reason: Plain-language explanation.
        priority: Lower wins for the primary evidence span.
        window_attr: Settings attribute for the context window size.
    """

    anchor: re.Pattern[str]
    keywords: tuple[re.Pattern[str], ...]
    reason: str
    priority: int = 7
    window_attr: str = "red_flag_context_window"

    @classmethod
    def create(
        cls,
        anchor: str,
        keywords: tuple[str, ...],
        reason: str,
        priority: int = 7,
        window_attr: str = "red_flag_context_window",
    ) -> ContextKeywordDetector:
        """Build a context-keyword detector.

        Args:
            anchor: Source pattern for the anchor entity.
            keywords: Source patterns supplying context.
            reason: Plain-language explanation.
            priority: Lower wins for the primary evidence span.
            window_attr: Settings attribute for the window size.

        Returns:
            A configured :class:`ContextKeywordDetector`.
        """
        return cls(
            anchor=_compile(anchor),
            keywords=tuple(_compile(item) for item in keywords),
            reason=reason,
            priority=priority,
            window_attr=window_attr,
        )

    def find(self, text: str, settings: object) -> list[RawMatch]:
        """Return spans running from the anchor through the context keyword.

        The search is confined to the anchor's sentence whenever that sentence
        contains a keyword, so evidence never bleeds across an unrelated
        sentence. The nearest keyword by absolute distance is used.
        """
        window = int(getattr(settings, self.window_attr))
        results: list[RawMatch] = []
        for anchor_match in self.anchor.finditer(text):
            sentence_start, sentence_end = sentence_bounds(text, anchor_match.start(), anchor_match.end())
            left = max(0, anchor_match.start() - window)
            right = min(len(text), anchor_match.end() + window)
            in_sentence = self._nearest(text, anchor_match, sentence_start, sentence_end)
            best = in_sentence or self._nearest(text, anchor_match, left, right)
            if best is None:
                continue
            start = min(anchor_match.start(), best.start())
            end = max(anchor_match.end(), best.end())
            results.append(RawMatch(start=start, end=end, reason=self.reason))
        return results

    def _nearest(
        self,
        text: str,
        anchor_match: re.Match[str],
        left: int,
        right: int,
    ) -> re.Match[str] | None:
        """Return the context keyword closest to the anchor within a range."""
        best: re.Match[str] | None = None
        best_distance = -1
        for keyword in self.keywords:
            found = keyword.search(text, left, right)
            while found is not None:
                distance = min(abs(found.start() - anchor_match.end()), abs(anchor_match.start() - found.end()))
                if best_distance < 0 or distance < best_distance:
                    best = found
                    best_distance = distance
                found = keyword.search(text, found.end(), right)
        return best


# ---------------------------------------------------------------------------
# Shared vocabulary
# ---------------------------------------------------------------------------

_RETURN_WORD = r"(?:returns?|profits?|income|gains?|yield|earnings?|payouts?)"

_REGULATORS = r"(?:sebi|rbi|irdai|nse|bse|mca|nbafc|pfrda|sfio)"

# NOTE: both vocabularies below MUST be pre-grouped. Interpolating a bare
# alternation into a larger pattern would silently split that pattern at the
# top level and make it match far more than intended.
_CREDENTIAL_STATUS = (
    r"(?:approved|registered|authori[sz]ed|certified|verified|regulated|licensed|"
    r"guaranteed|sanctioned|recognised|recognized)"
)

#: Investment/trading context cues. Used to gate rules that must not fire on a
#: bare mention (messaging groups, unsecured links).
_INVESTMENT_CONTEXT = (
    r"\b(?:invest|investing|investment|investments|trading|trade|trades|trader|traders|"
    r"signal|signals|profit|profits|return|returns|portfolio|stock|stocks|share|shares|"
    r"forex|crypto|bitcoin|coin|coins|equity|commodity|commodities|demat|broker|brokers|"
    r"option|options|prediction|mt4|mt5|nifty|sensex)\b",
    # Group/community framing is investment context in itself, and platform
    # names may sit between the qualifier and the noun ("private Telegram group").
    r"\b(?:private|vip|signal|trading|investment|premium|paid|inner[\s\-]?circle)\b"
    r"[^.\n]{0,25}?\b(?:group|channel|community|family|room)\b",
)

_PERSONAL_ACCOUNT = (
    r"personal|my\s+own|our\s+own|my|our|his|her|their|another|a\s+different|"
    r"third[\s\-]party|employee|friend|agent'?s?|manager'?s?|"
    r"company'?s\s+personal|personal\s+bank|savings\s+account"
)

_REQUIREMENT_EXCLUSIONS = (
    # "...must be registered", "...should be approved", "...is required to be licensed"
    r"\b(?:must|has\s+to|have\s+to|should|shall|need\s+to|needs\s+to|required\s+to|"
    r"requir(?:e|es|ed|ing)|meant\s+to|obliged\s+to|is\s+required|are\s+required|"
    r"is\s+mandatory|only\s+if\s+you)\b[^.\n]{0,45}?\b(?:be\s+)?"
    r"(?:registered|approved|licensed|certified|authori[sz]ed|authorized)\b",
    r"\b(?:how\s+to|where\s+to|steps?\s+to)\s+(?:check|verify|register|find|search)\b",
    r"\b(?:law|regulation|rule|rules|guideline|guidelines|requirement|requirements|"
    r"policy|policies|act|statute|section)\b",
)

_FUNDS_WORD = r"profits?|money|funds?|balance|amount|earnings|winnings|investments?"


# ---------------------------------------------------------------------------
# Rule catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RedFlagRule:
    """A single, independently testable red-flag rule.

    Attributes:
        code: Stable identifier persisted and returned by the API.
        name: Human-readable indicator name.
        description: What the indicator means, in plain language.
        severity: Intrinsic seriousness of the indicator type.
        default_weight: Fallback weight when settings do not override it.
        weight_attr: :class:`Settings` attribute holding the configured weight.
        detectors: Ordered detectors; the first match set defines the evidence.
        verification_note: Safety note explaining that detection is not a
            finding of truth.
    """

    code: RedFlagCode
    name: str
    description: str
    severity: Severity
    default_weight: int
    weight_attr: str
    detectors: tuple[Detector, ...]
    verification_note: str = ""


# --- 1. GUARANTEED_RETURN ---------------------------------------------------

_GUARANTEED_RETURN = RedFlagRule(
    code=RedFlagCode.GUARANTEED_RETURN,
    name="Guaranteed Return",
    description="The content promises or guarantees an investment return.",
    severity=Severity.HIGH,
    default_weight=20,
    weight_attr="risk_weight_guaranteed_return",
    detectors=(
        RegexDetector.create(
            r"\bguarantee[sd]?\b[^.\n]{0,40}?\b" + _RETURN_WORD + r"\b",
            "The content uses guaranteed-return language.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b" + _RETURN_WORD + r"\b[^.\n]{0,25}?\bguarantee[sd]?\b",
            "The content attaches a guarantee to an investment outcome.",
            priority=2,
        ),
        RegexDetector.create(
            r"\brisk[\s\-]?free\b[^.\n]{0,30}?\b" + _RETURN_WORD + r"\b",
            "The content describes investment returns as risk-free.",
            priority=3,
        ),
        RegexDetector.create(
            r"\bguarantee[sd]?\s+(?:a\s+|the\s+)?\d+(?:\.\d+)?\s*%",
            "A specific guaranteed percentage return is offered.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b100\s*%\s*(?:guarantee[sd]?|risk[\s\-]?free|assured)\b[^.\n]{0,25}?",
            "A 100% guaranteed or entirely risk-free outcome is claimed.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:assure[sd]?|promise[sd]?)\b[^.\n]{0,25}?\b" + _RETURN_WORD + r"\b",
            "The content uses assured-income or promised-return language.",
            priority=2,
        ),
        RegexDetector.create(
            r"\bfixed\s+(?:and\s+)?guarantee[sd]?\b[^.\n]{0,25}?",
            "A fixed, guaranteed return is offered.",
            priority=2,
        ),
        RegexDetector.create(
            r"\bcapital\s+(?:is\s+|are\s+)?(?:fully\s+)?guarantee[sd]?\b[^.\n]{0,25}?",
            "The capital itself is described as guaranteed.",
            priority=1,
        ),
        RegexDetector.create(
            r"\bno[\s\-]?loss\b[^.\n]{0,30}?\b(?:guarantee[sd]?|assure[sd]?|risk[\s\-]?free)\b",
            "The content claims a no-loss outcome.",
            priority=1,
        ),
        RegexDetector.create(
            r"गारंटी[^।\n]{0,30}(?:रिटर्न|लाभ|आय|मुनाफा|returns?|profits?|income)"
            r"|(?:रिटर्न|लाभ|आय|मुनाफा|returns?|profits?|income)[^।\n]{0,30}गारंटी",
            "The content uses guaranteed-return language.",
            priority=2,
        ),
    ),
)


# --- 2. UNREALISTIC_RETURN --------------------------------------------------

_UNREALISTIC_RETURN = RedFlagRule(
    code=RedFlagCode.UNREALISTIC_RETURN,
    name="Unrealistic Return",
    description="A return is promised that is far beyond plausible market performance.",
    severity=Severity.HIGH,
    default_weight=20,
    weight_attr="risk_weight_unrealistic_return",
    detectors=(
        PercentThresholdDetector.create(
            patterns=(
                r"(\d+(?:\.\d+)?)\s*%\s*(?:per|a|each|every)\s*month(?:ly|s)?\b",
                r"\bmonthly\s+(?:" + _RETURN_WORD + r"|roi)\s*(?:of|is|are|was|were|at|:|=)?\s*(\d+(?:\.\d+)?)\s*%",
                r"(\d+(?:\.\d+)?)\s*%\s*monthly\b(?:\s*" + _RETURN_WORD + r")?",
                r"(\d+(?:\.\d+)?)\s*%\s*(?:monthly)\s*(?:" + _RETURN_WORD + r"|roi|profit|return)?\s*(?:each|per)?\s*month",
            ),
            reason="A monthly return is promised that is far above plausible levels.",
            threshold_attr="unrealistic_monthly_return_threshold",
            priority=1,
        ),
        PercentThresholdDetector.create(
            patterns=(
                r"(\d+(?:\.\d+)?)\s*%\s*(?:per|a|each|every)\s*year(?:ly)?\b",
                r"(\d+(?:\.\d+)?)\s*%\s*annually\b",
                r"(\d+(?:\.\d+)?)\s*%\s*annual\b",
                r"\bannual(?:is(?:ed|zed))?\s+(?:" + _RETURN_WORD + r"|roi)\s*(?:of|is|are|was|were|at|:|=)?\s*(\d+(?:\.\d+)?)\s*%",
                r"\b(?:returns?|profits?|roi|gains?)\s*(?:of\s*)?(\d+(?:\.\d+)?)\s*%\s*(?:p\.?a\.?|per\s+annum|annually|per\s+year)\b",
            ),
            reason="An annual return is promised that is far above plausible levels.",
            threshold_attr="unrealistic_annual_return_threshold",
            priority=2,
        ),
        MultiplierDetector.create(
            patterns=(
                r"\b(\d+(?:\.\d+)?)\s*x\s*(?:in|within|every|each|per)\s*(?:a|one|each)?\s*"
                r"(?:single\s+|just\s+|mere\s+)?(?:day|week|month|fortnight)\b",
            ),
            reason="A multiple-times growth claim is made over a very short period.",
            threshold_attr="unrealistic_multiplier_threshold",
            priority=1,
        ),
        MultiplierDetector.create(
            patterns=(),
            reason="The content promises to multiply the investor's money very quickly.",
            threshold_attr="unrealistic_multiplier_threshold",
            plain_word_multiples=(
                r"\b(?:double|triple|quadruple|ten\s*fold|10\s*x)\b[^.\n]{0,25}?"
                r"\b(?:money|investment|capital|funds?|wealth|income|returns?)\b",
            ),
            priority=3,
        ),
        CurrencyGrowthDetector.create(
            reason="A very large short-term growth in the invested amount is promised.",
            threshold_attr="unrealistic_currency_growth_multiple",
            priority=1,
        ),
        ShortPeriodProfitDetector.create(
            pattern=r"(\d+(?:\.\d+)?)\s*%\s*(?:profit|profits|return|returns|gain|gains|roi)"
            r"[^.\n]{0,30}?\bin\s+(\d+)\s*(?:day|days|week|weeks)\b",
            reason="A very large profit is promised within a very short number of days.",
            pct_attr="unrealistic_short_period_pct_threshold",
            days_attr="unrealistic_short_period_days_threshold",
            priority=1,
        ),
    ),
)


# --- 3. URGENCY_PRESSURE ----------------------------------------------------

_URGENCY_PRESSURE = RedFlagRule(
    code=RedFlagCode.URGENCY_PRESSURE,
    name="Urgency and Pressure",
    description="The content pressures the reader to act immediately.",
    severity=Severity.MEDIUM,
    default_weight=15,
    weight_attr="risk_weight_urgency_pressure",
    detectors=(
        RegexDetector.create(
            r"\b(?:act|apply|register|sign\s*up|signup|start|begin|invest|book|join|"
            r"subscribe|enrol|enroll|deposit|transfer|buy|send|pay|grab)\b"
            r"[^.\n]{0,40}?\btoday\b",
            "The content asks the reader to act today.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:today\s+only|tonight\s+only|only\s+today|this\s+is\s+your\s+last\s+chance|"
            r"last\s+chance|don'?t\s+miss\s+(?:out|this)|do\s+not\s+miss|miss\s+(?:out|this)\b|"
            r"before\s+it'?s\s+too\s+late|hurry|quickly|right\s+now)\b",
            "The content creates pressure to act before an opportunity is lost.",
            priority=2,
        ),
        RegexDetector.create(
            r"\b(?:limited\s+(?:time|slots?|seats?|spots?|period)|"
            r"only\s+\d+\s+(?:slots?|seats?|spots?|places?|left|remaining)|"
            r"seats?\s+(?:are\s+)?filling|slots?\s+(?:are\s+)?filling|"
            r"(?:offer|window|slots?|seats?|spots?)\s+(?:is\s+|are\s+)?(?:closing|closed|full)|"
            r"closing\s+(?:fast|soon|tonight|today))\b",
            "Scarcity or a closing window is used to prompt immediate action.",
            priority=2,
        ),
        RegexDetector.create(
            r"\b(?:closes?|closing|ends?|ending|expir\w*|lapses?|last\s+day)\b"
            r"[^.\n]{0,20}?\b(?:tonight|today|soon|midnight|now|hours?)\b",
            "A deadline is described as closing imminently.",
            priority=2,
        ),
        RegexDetector.create(
            r"\b(?:registration|enrolment|enrollment|admission|subscription|offer|scheme|"
            r"plan|batch|course)\b[^.\n]{0,25}?\b(?:closes?|closing|ends?|ending|expir\w*)\b"
            r"[^.\n]{0,25}?\b(?:today|tonight|soon|now|midnight|last\s+chance|hurry|limited|filling)\b",
            "An offer or registration window is described as closing imminently.",
            priority=2,
        ),
        RegexDetector.create(
            r"\bexpir\w+\b[^.\n]{0,25}?\b(?:today|tonight|soon|midnight|hours?)\b|"
            r"\b(?:offer|deal|scheme)\b[^.\n]{0,25}?\bexpires?\b",
            "The offer is described as expiring imminently.",
            priority=2,
        ),
        RegexDetector.create(
            r"\b(?:invest|send|pay|deposit|transfer|join|act)\b[^.\n]{0,20}?"
            r"\b(?:now|immediately|right\s+away|instantly)\b",
            "The reader is told to act immediately.",
            priority=1,
        ),
        RegexDetector.create(
            r"तुरंत|आज\s*ही|भाग\s*जाए|सीटें\s*सीमित|अभी\s*जुड़",
            "The content pressures the reader in Hindi to act immediately.",
            priority=3,
        ),
    ),
)


# --- 4. FAKE_REGULATORY_CLAIM ----------------------------------------------

_FAKE_REGULATORY_CLAIM = RedFlagRule(
    code=RedFlagCode.FAKE_REGULATORY_CLAIM,
    name="Regulatory Claim Requiring Verification",
    description=(
        "The content claims regulatory approval, registration or authorisation. "
        "This indicates a claim that must be independently verified — it is not "
        "a finding that the claim is false."
    ),
    severity=Severity.HIGH,
    default_weight=25,
    weight_attr="risk_weight_fake_regulatory_claim",
    verification_note="Whether the registration actually exists is decided by verification in Phase 4, not here.",
    detectors=(
        RegexDetector.create(
            r"\b(?:" + _REGULATORS + r")\b[\s\-_]*" + _CREDENTIAL_STATUS + r"\b",
            "A regulator's name is directly attached to an approval or registration claim.",
            priority=1,
            exclusions=_REQUIREMENT_EXCLUSIONS,
        ),
        RegexDetector.create(
            r"\b(?:" + _CREDENTIAL_STATUS + r")\s+(?:by|with|from|under)\s+(?:the\s+)?"
            r"(?:" + _REGULATORS + r")\b",
            "Approval or registration is attributed to a regulator.",
            priority=2,
            exclusions=_REQUIREMENT_EXCLUSIONS,
        ),
        RegexDetector.create(
            r"\b(?:government|govt\.?|central\s+government|state\s+government)\b"
            r"[\s\-_]*" + _CREDENTIAL_STATUS + r"\b",
            "Government approval is claimed.",
            priority=1,
            exclusions=_REQUIREMENT_EXCLUSIONS,
        ),
        RegexDetector.create(
            r"\b(?:" + _REGULATORS + r")\b\s+(?:" + _CREDENTIAL_STATUS + r")\s+"
            r"(?:investment\s+|financial\s+)?(?:profits?|returns?|income|adviser|advisor|"
            r"consultant|broker|fund|platform|company|services?)\b",
            "A regulator's authority is claimed over profits, returns or the entity itself.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:fully|100%|completely)\s+" + _CREDENTIAL_STATUS + r"\b"
            r"[^.\n]{0,25}?\b(?:" + _REGULATORS + r")\b",
            "A regulator's name is used to imply a blanket approval.",
            priority=2,
        ),
    ),
)


# --- 5. UNVERIFIED_ADVISER --------------------------------------------------

_UNVERIFIED_ADVISER = RedFlagRule(
    code=RedFlagCode.UNVERIFIED_ADVISER,
    name="Adviser Credential Claim Requiring Verification",
    description=(
        "An adviser, broker or expert credential is claimed. The claim requires "
        "independent verification — this is not a finding that the person is "
        "unverified or fraudulent."
    ),
    severity=Severity.MEDIUM,
    default_weight=20,
    weight_attr="risk_weight_unverified_adviser",
    verification_note="Registration is looked up during verification in Phase 4.",
    detectors=(
        RegexDetector.create(
            r"\b(?:our|our\s+in[\s\-]?house|house|the)\s+(?:" + _REGULATORS + r")\b[\s\-_]*"
            r"(?:expert|experts|adviser|advisor|consultant|team|desk)\b",
            "A regulator's name is used to present the promoter's own expert team.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:" + _REGULATORS + r")\b[\s\-_]*(?:approved|registered|certified|qualified|"
            r"verified|authori[sz]ed)\s+(?:investment\s+|financial\s+|trading\s+)?"
            r"(?:adviser|advisor|consultant|expert|experts|advisory|research\s+analyst)s?\b",
            "A regulatory or professional credential is claimed for an adviser or expert.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:certified|registered|licensed|professional|qualified|verified|expert|senior)\s+"
            r"(?:investment\s+|financial\s+|trading\s+|wealth\s+|portfolio\s+|research\s+)?"
            r"(?:adviser|advisor|consultant|planner|expert|manager|analyst)s?\b",
            "A professional or registered credential is claimed for an adviser or expert.",
            priority=2,
        ),
        RegexDetector.create(
            r"\b(?:adviser|advisor|consultant|planner|manager)\b[^.\n]{0,20}?"
            r"\b(?:certified|registered|licensed|qualified|professional)\b",
            "A credential is claimed for an adviser or manager.",
            priority=3,
        ),
        RegexDetector.create(
            r"\b(?:our|our\s+in[\s\-]?house|house)\s+(?:expert|experts|adviser|advisor|"
            r"consultant|research\s+team)s?\b[^.\n]{0,30}?"
            r"\b(?:" + _REGULATORS + r"|certified|registered|licensed|qualified|professional)\b",
            "The promoter's own team is presented as certified or registered.",
            priority=2,
        ),
    ),
)


# --- 6. SUSPICIOUS_URL ------------------------------------------------------

_SUSPICIOUS_URL = RedFlagRule(
    code=RedFlagCode.SUSPICIOUS_URL,
    name="Suspicious Link",
    description="A link was found that uses a pattern commonly seen in fraudulent campaigns.",
    severity=Severity.MEDIUM,
    default_weight=10,
    weight_attr="risk_weight_suspicious_url",
    verification_note="A pattern match is an indicator only; a domain is never labelled malicious on pattern alone.",
    detectors=(
        RegexDetector.create(
            r"\bhttps?://(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?(?:/[^\s]*)?",
            "A link points to a raw IP address instead of a named domain.",
            priority=1,
            respect_negation=False,
        ),
        RegexDetector.create(
            r"\bhttps?://(?:www\.)?(?:bit\.ly|tinyurl\.com|t\.co|goo\.gl|is\.gd|cutt\.ly|"
            r"rb\.gy|rebrand\.ly|ow\.ly|buff\.ly|t\.ly|shorturl\.at|tiny\.cc|adf\.ly|"
            r"lnkd\.in)\b[^\s]*",
            "A URL shortener hides the real destination of the link.",
            priority=1,
            respect_negation=False,
        ),
        RegexDetector.create(
            r"\bhttps?://[^\s/]*\.(?:xyz|top|tk|ml|ga|cf|gq|buzz|click|loan|zip|cam|rest|"
            r"quest|monster|icu|cfd|science|party|win|racing|stream|download|review|trade|"
            r"invest|finance)\b[^\s]*",
            "A link uses a domain pattern commonly abused for investment fraud.",
            priority=2,
            respect_negation=False,
        ),
        RegexDetector.create(
            r"\bhttp://[^\s]*(?:login|signin|sign[\s\-]?in|account|verify|kyc|payment|"
            r"pay|wallet|bank|deposit|withdraw)[^\s]*",
            "An insecure (non-HTTPS) link relates to login or payment activity.",
            priority=1,
            respect_negation=False,
        ),
        RegexDetector.create(
            r"\bhttps?://(?:xn--[^\s/]+)",
            "A link uses a punycode domain, which can imitate a familiar name.",
            priority=1,
            respect_negation=False,
        ),
        ContextKeywordDetector.create(
            anchor=r"\bhttps?://[^\s]+",
            keywords=_INVESTMENT_CONTEXT,
            reason="An unsecured (non-HTTPS) link is used in an investment context.",
            priority=3,
        ),
    ),
)


# --- 7. THIRD_PARTY_PAYMENT ------------------------------------------------

_THIRD_PARTY_PAYMENT = RedFlagRule(
    code=RedFlagCode.THIRD_PARTY_PAYMENT,
    name="Third-Party or Personal Account Payment",
    description="Payment is requested to a personal or third-party account instead of an official account.",
    severity=Severity.HIGH,
    default_weight=15,
    weight_attr="risk_weight_third_party_payment",
    verification_note="This detects the payment instruction only; it makes no finding about the payee.",
    detectors=(
        RegexDetector.create(
            r"\bpay\b[^.\n]{0,60}?\b(?:" + _PERSONAL_ACCOUNT + r")\b[^.\n]{0,30}?\b"
            r"(?:account|upi|wallet|bank|a/c)\b",
            "Payment is requested to a personal or third-party account.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:deposit|transfer|send)\b[^.\n]{0,40}?\b(?:" + _PERSONAL_ACCOUNT + r")\b"
            r"[^.\n]{0,30}?\b(?:account|upi|wallet|bank|a/c)\b",
            "Funds are requested to be sent to a personal or third-party account.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:the\s+)?(?:money|funds|cash|payment|amount|rupees|rs\.?)\b[^.\n]{0,40}?"
            r"\bto\b[^.\n]{0,20}?\b(?:" + _PERSONAL_ACCOUNT + r")\b",
            "Money is directed to a personal or third-party destination.",
            priority=2,
        ),
        RegexDetector.create(
            r"\b(?:" + _PERSONAL_ACCOUNT + r")\b[^.\n]{0,25}?\b(?:bank\s+|savings\s+)?"
            r"(?:account|a/c|upi)\b[^.\n]{0,40}?\b(?:pay|payment|deposit|transfer|send)\b",
            "A personal or third-party account is presented for payment.",
            priority=2,
        ),
        RegexDetector.create(
            r"\bupi\s*(?:id|number)?\b\s*[:\-]?\s*[A-Za-z0-9._\-]{2,}@[A-Za-z0-9._\-]{2,}",
            "A UPI payment identifier is supplied directly in the content.",
            priority=1,
            respect_negation=False,
        ),
        RegexDetector.create(
            r"\b(?:ifsc|neft|imps|rtgs)\b\s*[:\-]?\s*[A-Za-z]{4}0[A-Za-z0-9]{6}\b",
            "Bank transfer details are supplied directly in the content.",
            priority=1,
            respect_negation=False,
        ),
        RegexDetector.create(
            r"\b(?:transfer|send|pay|deposit|credit)\b[^.\n]{0,30}?\bto\s+"
            r"(?:this|our|my|your|the\s+following|below\s+mentioned|ad[\s\-]?hoc)\s+"
            r"(?:upi|a/c|account|qr\s*code|wallet|bank\s+account|number)\b",
            "Funds are directed to an ad-hoc payment destination given in the content.",
            priority=1,
        ),
        RegexDetector.create(
            r"\bpay\s+(?:directly\s+)?to\s+(?:the\s+)?"
            r"(?:our|my|his|her|their|another|a\s+different)\b[^.\n]{0,60}?"
            r"\b(?:account|a/c|upi|wallet|bank)\b",
            "A direct payment to a personal or organisational account is requested.",
            priority=1,
        ),
    ),
)


# --- 8. APK_DOWNLOAD --------------------------------------------------------

_APK_DOWNLOAD = RedFlagRule(
    code=RedFlagCode.APK_DOWNLOAD,
    name="APK / Sideloaded App Download",
    description="The content asks the reader to install an app from outside an official app store.",
    severity=Severity.HIGH,
    default_weight=15,
    weight_attr="risk_weight_apk_download",
    verification_note="InvestShield only reads the text; it never downloads or executes any file.",
    detectors=(
        RegexDetector.create(
            r"\.apk\b",
            "A link or reference to an Android application package file is present.",
            priority=1,
            respect_negation=False,
        ),
        RegexDetector.create(
            r"\b(?:download|install|installing|downloads)\b[^.\n]{0,30}?\bapk\b",
            "The reader is asked to download or install an APK.",
            priority=1,
        ),
        RegexDetector.create(
            r"\binstall\s+(?:this|the|our|our\s+new)\s+[^.\n]{0,30}?\bapp\b"
            r"[^.\n]{0,40}?\b(?:from\s+(?:this|the|our)\s+link|link|below|site|website|server)\b",
            "The reader is asked to install an app from a link rather than a store.",
            priority=1,
        ),
        RegexDetector.create(
            r"\bdownload\b[^.\n]{0,30}?\b(?:trading|investment|forex|signal|profit)\s+app\b",
            "An unofficial trading or investment app download is offered.",
            priority=2,
        ),
        RegexDetector.create(
            r"\b(?:enable|allow|turn\s+on)\s+(?:unknown\s+sources|install\s+unknown\s+apps|"
            r"app\s+installs\s+from\s+unknown\s+sources)\b",
            "The reader is asked to weaken device security settings.",
            priority=1,
        ),
        RegexDetector.create(
            r"\bsideload\w*\b|\bandroid\s+(?:application\s+package|package\s+file)\b",
            "Sideloading or a direct Android package is referenced.",
            priority=2,
            respect_negation=False,
        ),
    ),
)


# --- 9/10. Messaging investment groups --------------------------------------

_TELEGRAM_GROUP = RedFlagRule(
    code=RedFlagCode.TELEGRAM_INVESTMENT_GROUP,
    name="Telegram Investment Group",
    description="Investment or trading activity is being organised through a Telegram group or channel.",
    severity=Severity.MEDIUM,
    default_weight=10,
    weight_attr="risk_weight_telegram_investment_group",
    detectors=(
        ContextKeywordDetector.create(
            anchor=r"\b(?:telegram|t\.me|telegram\.me|telegram\s+group)\b",
            keywords=_INVESTMENT_CONTEXT,
            reason="Telegram is used in an investment or trading context.",
            priority=1,
        ),
    ),
)

_WHATSAPP_GROUP = RedFlagRule(
    code=RedFlagCode.WHATSAPP_INVESTMENT_GROUP,
    name="WhatsApp Investment Group",
    description="Investment or trading activity is being organised through a WhatsApp group or chat.",
    severity=Severity.MEDIUM,
    default_weight=10,
    weight_attr="risk_weight_whatsapp_investment_group",
    detectors=(
        ContextKeywordDetector.create(
            anchor=r"\b(?:whatsapp|wa\.me|whatsapp\.com|whats\s*app)\b",
            keywords=_INVESTMENT_CONTEXT,
            reason="WhatsApp is used in an investment or trading context.",
            priority=1,
        ),
    ),
)


# --- 11. BORROW_TO_INVEST ---------------------------------------------------

_BORROW_TO_INVEST = RedFlagRule(
    code=RedFlagCode.BORROW_TO_INVEST,
    name="Borrowing to Invest",
    description="The reader is encouraged to borrow money or use credit in order to invest.",
    severity=Severity.HIGH,
    default_weight=15,
    weight_attr="risk_weight_borrow_to_invest",
    detectors=(
        RegexDetector.create(
            r"\b(?:borrow|borrowing|take|taking|use|using|apply\s+for|avail)\b[^.\n]{0,40}?"
            r"\b(?:a\s+|an\s+)?(?:loan|loans|money|funds|credit\s+money|credit\s+card|"
            r"personal\s+loan|home\s+loan|advance|emi)\b[^.\n]{0,40}?"
            r"\b(?:invest|investing|investment|trading|trade)\b",
            "The reader is encouraged to borrow or use credit in order to invest.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:invest|investing|investment|trading|trade)\b[^.\n]{0,40}?"
            r"\b(?:borrowed|loan|loans|loan\s+money|credit\s+card|credit\s+money|emi|"
            r"personal\s+loan|debt)\b",
            "Investing with borrowed money or on credit is suggested.",
            priority=2,
        ),
        RegexDetector.create(
            r"\buse\s+(?:your\s+|a\s+)?credit\s+card\b[^.\n]{0,30}?\b(?:invest|trading|trade|pay)\b",
            "Credit card usage is suggested for investing.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:take|get|apply\s+for)\s+(?:a\s+|an\s+)?(?:personal\s+|home\s+)?loan\b"
            r"[^.\n]{0,35}?\b(?:invest|investing|investment|trading|trade|this\s+opportunity|scheme)\b",
            "Taking a loan for this investment is suggested.",
            priority=1,
        ),
    ),
)


# --- 12. WITHDRAWAL_FEE -----------------------------------------------------

_WITHDRAWAL_FEE = RedFlagRule(
    code=RedFlagCode.WITHDRAWAL_FEE,
    name="Fee Charged Before Withdrawal",
    description="A fee or tax is required before existing profits or funds can be accessed.",
    severity=Severity.HIGH,
    default_weight=20,
    weight_attr="risk_weight_withdrawal_fee",
    verification_note="This is a well-known withdrawal-fee pattern; the actual conduct of the entity is verified in Phase 4.",
    detectors=(
        RegexDetector.create(
            r"\b(?:pay|paying|payment\s+of|charge|charges|deduct|deducted)\b[^.\n]{0,40}?"
            r"\b(?:withdrawal|release|unlock|exit|closure|transfer|settlement)\s*"
            r"(?:fee|fees|charge|charges|tax|amount|payment|sum)\b",
            "A fee or tax must be paid before money can be released or withdrawn.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:withdrawal|release|unlock|exit|closure)\s*(?:fee|fees|charge|charges|tax)\b"
            r"[^.\n]{0,40}?\b(?:" + _FUNDS_WORD + r"|account)\b",
            "A withdrawal or release fee is described alongside funds or an account.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:fee|fees|charge|charges|tax)\b[^.\n]{0,30}?\b(?:to|for|before)\b"
            r"[^.\n]{0,30}?\b(?:withdraw\w*|unlock\w*|release)\b",
            "A fee is required in order to withdraw or release funds.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:unlock|unblock|release|free\s+up|release\s+of|access)\b[^.\n]{0,20}?"
            r"\b(?:your|the|my)\b[^.\n]{0,20}?\b(?:" + _FUNDS_WORD + r"|demo\s+account)\b",
            "Access to funds is presented as something that must be unlocked or released.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:tax|taxes|charges?|deduction)\b[^.\n]{0,25}?\bbefore\s+"
            r"(?:release|withdrawal|unlock\w*|transfer)\b",
            "A deduction is required before funds are released.",
            priority=1,
        ),
        RegexDetector.create(
            r"\bwithdraw\w*[^.\n]{0,35}?\b(?:fee|charge|tax)\b|\b(?:fee|charge|tax)\b"
            r"[^.\n]{0,35}?\bwithdraw\w*\b",
            "A fee is associated with withdrawing funds.",
            priority=2,
        ),
        RegexDetector.create(
            r"\bdemo\s+account\b[^.\n]{0,35}?\b(?:fee|fees|charge|charges|tax)\b",
            "A fee is described for a demo or blocked account.",
            priority=2,
        ),
    ),
)


# --- 13. ACCOUNT_ACTIVATION_FEE --------------------------------------------

_ACCOUNT_ACTIVATION_FEE = RedFlagRule(
    code=RedFlagCode.ACCOUNT_ACTIVATION_FEE,
    name="Account Activation or Registration Fee",
    description="A payment is required to activate, register or unlock an investment account.",
    severity=Severity.MEDIUM,
    default_weight=15,
    weight_attr="risk_weight_account_activation_fee",
    detectors=(
        RegexDetector.create(
            r"\b(?:pay|paying|payment|deposit|send|transfer)\b[^.\n]{0,60}?\bactivat\w+\b"
            r"[^.\n]{0,20}?\b(?:trading\s+account|account|trading\s+platform|platform)\b",
            "Payment is required in order to activate an account.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:activation|registration|account\s+opening|account\s+unlock\w*|joining|"
            r"membership|onboarding)\s*(?:fee|fees|charge|charges|payment|amount|deposit)\b",
            "An activation or registration fee is described.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:fee|fees|charge|charges|payment|deposit|amount)\b[^.\n]{0,30}?"
            r"\bactivat\w+\b[^.\n]{0,30}?\b(?:account|trading|platform)\b",
            "A fee or payment is tied to activating an account.",
            priority=1,
        ),
        RegexDetector.create(
            r"\bpay\s+(?:a\s+|the\s+|an\s+)?(?:account\s+)?activation\s+"
            r"(?:fee|charge|amount|payment)\b|\bpay\s+to\s+activat\w+\b",
            "Payment to activate an account is requested.",
            priority=1,
        ),
        RegexDetector.create(
            r"\bdeposit\b[^.\n]{0,35}?\bactivat\w+\b[^.\n]{0,35}?\b(?:account|trading)\b",
            "A deposit is required to activate an account.",
            priority=1,
        ),
        RegexDetector.create(
            r"\bregistration\s+fee\b[^.\n]{0,35}?\b(?:trading|trade|start|before|invest)\b",
            "A registration fee is required before trading or investing.",
            priority=2,
        ),
    ),
)


# --- 14. FAKE_PROFIT_SCREENSHOT --------------------------------------------

_FAKE_PROFIT_SCREENSHOT = RedFlagRule(
    code=RedFlagCode.FAKE_PROFIT_SCREENSHOT,
    name="Purported Profit / Payment Proof",
    description="The content refers to supposed proof of profits, payments or withdrawals.",
    severity=Severity.MEDIUM,
    default_weight=10,
    weight_attr="risk_weight_fake_profit_screenshot",
    verification_note="Phase 1 reads text only; no image or screenshot authenticity analysis is performed.",
    detectors=(
        RegexDetector.create(
            r"\b(?:see|look\s+at|check|view|refer|have\s+a\s+look)\b[^.\n]{0,45}?"
            r"\b(?:profits?|payments?|withdrawal|withdrawals?|trading|account|balance|"
            r"winnings|earnings|gains)\b[^.\n]{0,30}?"
            r"\b(?:screenshots?|screen\s?shots?|proof|image|picture|photo|video|clip|statement)\b",
            "The content directs the reader to purported proof of profits or payments.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:profits?|payments?|withdrawal|withdrawals?|trade|trading|winnings|earnings)"
            r"\s*(?:proof|screenshots?|screen\s?shots?|image|photo|pics?)\b",
            "Purported proof of profits or payments is referenced.",
            priority=1,
        ),
        RegexDetector.create(
            r"\bproof\s+of\s+(?:our|my|the|actual|real|genuine|verified)?\s*"
            r"(?:profits?|earnings|winnings|money|returns?|payouts?|income)\b",
            "The content asserts proof of profits.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:real|actual|genuine|verified|100%\s*real)\s+"
            r"(?:money|profit|profits|earnings|withdrawal|returns?)\s+proof\b",
            "The content claims its profit proof is genuine.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:attached|below|pictured|here)\b[^.\n]{0,30}?"
            r"\b(?:screenshots?|image|photo|video|picture)s?\b[^.\n]{0,45}?"
            r"\b(?:profits?|payments?|withdrawal|balance|winnings|earnings)\b",
            "An attached image is presented as proof of profits or withdrawals.",
            priority=1,
        ),
        RegexDetector.create(
            r"[₹$]?\s*[\d,.]+\s*(?:lakh|lakhs|crore|crores|k)?\s*"
            r"(?:a\s+|per\s+)?(?:month)?\s*(?:profit\s+)?"
            r"(?:screenshots?|proof|statement)\b",
            "A specific profit amount is paired with supposed proof.",
            priority=2,
        ),
    ),
)


# --- 15. IMPERSONATION ------------------------------------------------------

_IMPERSONATION = RedFlagRule(
    code=RedFlagCode.IMPERSONATION,
    name="Impersonation of an Institution",
    description="The content claims to represent a regulator, exchange, bank or government body.",
    severity=Severity.HIGH,
    default_weight=25,
    weight_attr="risk_weight_impersonation",
    verification_note="Detection of the claim only; authenticity is resolved in Phase 4.",
    detectors=(
        RegexDetector.create(
            r"\b(?:i\s*am|i'?m|we\s*are|this\s*is|speaking|my\s+name\s+is|"
            r"i\s+work\s+(?:as|with|for|at)|i\s+(?:work|serve|represent))\b[^.\n]{0,25}?"
            r"\b(?:an?\s+|the\s+)?(?:official\s+)?(?:" + _REGULATORS + r"|regulator|"
            r"government|stock\s+exchange|exchange)\b",
            "The content claims to be a regulator, exchange or government body.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:i\s*am|i'?m|we\s*are|this\s*is)\s+(?:an?\s+)?(?:" + _REGULATORS + r")\s+"
            r"(?:officer|inspector|advisor|adviser|representative|employee|official)s?\b",
            "The content claims to be an official of a named institution.",
            priority=1,
        ),
        RegexDetector.create(
            r"\bofficial\s+(?:" + _REGULATORS + r"|regulator|government|stock\s+exchange|bank)\b"
            r"[^.\n]{0,25}?\b(?:representative|officer|official|advisor|adviser|department|"
            r"team|desk|office|personnel|helpdesk|support)\b",
            "An official role at a regulator, bank or government body is claimed.",
            priority=1,
        ),
        RegexDetector.create(
            r"\b(?:representative|officer|official|employee|desk)\s+of\s+(?:the\s+)?"
            r"(?:" + _REGULATORS + r"|regulator|government|stock\s+exchange|bank)\b",
            "The content claims to represent a regulator, exchange or bank.",
            priority=2,
        ),
        RegexDetector.create(
            r"\b(?:call|calling|called|message|messaging|writing|contacting|reaching)\s+"
            r"(?:you\s+)?(?:from|on\s+behalf\s+of)\s+(?:the\s+)?"
            r"(?:" + _REGULATORS + r"|regulator|government|stock\s+exchange|bank)\b",
            "The content claims to be contacting the reader on behalf of an institution.",
            priority=1,
        ),
        RegexDetector.create(
            r"\bon\s+behalf\s+of\s+(?:the\s+)?(?:" + _REGULATORS + r"|regulator|government|"
            r"stock\s+exchange|central\s+government)\b",
            "The content claims to act on behalf of an institution.",
            priority=1,
        ),
        RegexDetector.create(
            r"\bofficial\s+bank\b[^.\n]{0,25}?\b(?:investment|loan|wealth|deposit|advisory)\s+"
            r"(?:department|desk|team|division|office)\b",
            "An official bank department is impersonated.",
            priority=1,
        ),
    ),
)


#: Every rule, in catalogue order.
ALL_RULES: tuple[RedFlagRule, ...] = (
    _GUARANTEED_RETURN,
    _UNREALISTIC_RETURN,
    _URGENCY_PRESSURE,
    _FAKE_REGULATORY_CLAIM,
    _UNVERIFIED_ADVISER,
    _SUSPICIOUS_URL,
    _THIRD_PARTY_PAYMENT,
    _APK_DOWNLOAD,
    _TELEGRAM_GROUP,
    _WHATSAPP_GROUP,
    _BORROW_TO_INVEST,
    _WITHDRAWAL_FEE,
    _ACCOUNT_ACTIVATION_FEE,
    _FAKE_PROFIT_SCREENSHOT,
    _IMPERSONATION,
)

#: Rule lookup by code.
RULES_BY_CODE: dict[RedFlagCode, RedFlagRule] = {rule.code: rule for rule in ALL_RULES}


def get_rule(code: RedFlagCode) -> RedFlagRule:
    """Return the rule definition for a code.

    Args:
        code: The red-flag code to look up.

    Returns:
        The matching :class:`RedFlagRule`.

    Raises:
        KeyError: If the code is not part of the catalogue.
    """
    return RULES_BY_CODE[code]


__all__ = [
    "ALL_RULES",
    "NEGATION_WINDOW",
    "RULES_BY_CODE",
    "SENTENCE_BOUNDARY",
    "ContextKeywordDetector",
    "CurrencyGrowthDetector",
    "Detector",
    "MultiplierDetector",
    "PercentThresholdDetector",
    "RawMatch",
    "RedFlagRule",
    "RegexDetector",
    "ShortPeriodProfitDetector",
    "get_rule",
    "is_negated",
    "parse_number",
    "sentence_bounds",
    "sentence_contains",
]
