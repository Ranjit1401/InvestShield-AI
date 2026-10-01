"""Tests for the deterministic RedFlagEngine (Phase 1).

Covers, for every rule: positive detection, at least one legitimate
non-triggering example, and the engine-level contracts (exact evidence spans,
deduplication, deterministic sorting, configurable weights and thresholds).
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.schemas.red_flags import RedFlagCode, Severity, severity_rank
from app.services.red_flag_engine import RedFlagEngine

# The synthetic demo input from the product brief. Synthetic test content only.
DEMO_INPUT = (
    "\U0001F6A8 Exclusive AI Trading Opportunity \U0001F6A8\n\n"
    "Our SEBI-approved expert team guarantees 35% monthly returns.\n\n"
    "Join our private Telegram group today.\n\n"
    "Minimum investment \u20b925,000.\n\n"
    "Pay directly to our account to activate your trading account."
)

DEMO_EXPECTED_CODES = {
    RedFlagCode.GUARANTEED_RETURN,
    RedFlagCode.UNREALISTIC_RETURN,
    RedFlagCode.URGENCY_PRESSURE,
    RedFlagCode.FAKE_REGULATORY_CLAIM,
    RedFlagCode.UNVERIFIED_ADVISER,
    RedFlagCode.THIRD_PARTY_PAYMENT,
    RedFlagCode.ACCOUNT_ACTIVATION_FEE,
    RedFlagCode.TELEGRAM_INVESTMENT_GROUP,
}


# ---------------------------------------------------------------------------
# Per-rule positives
# ---------------------------------------------------------------------------

POSITIVES: dict[RedFlagCode, tuple[str, ...]] = {
    RedFlagCode.GUARANTEED_RETURN: (
        "We offer guaranteed returns of 12% a year.",
        "Invest with us for risk-free returns.",
        "100% guaranteed profit for every investor.",
        "Assured monthly income of 50,000.",
        "A fixed guaranteed return is offered on this plan.",
        "Your capital is fully guaranteed.",
        "No loss guaranteed on every trade.",
        "Our returns are guaranteed and your profit is protected.",
        "We guarantee monthly income of 1,00,000.",
        "हम 35% monthly returns की गारंटी देते हैं।",
    ),
    RedFlagCode.UNREALISTIC_RETURN: (
        "We promise 35% monthly returns.",
        "Get 50% monthly profit guaranteed.",
        "Double your money every month with us.",
        "100% profit in 7 days guaranteed.",
        "Rs.10,000 becomes Rs.1,00,000 in one month.",
        "Earn 8x in a single month.",
        "Our monthly returns are 40%.",
    ),
    RedFlagCode.URGENCY_PRESSURE: (
        "Act now, slots are limited.",
        "Today is the last chance to invest.",
        "Join our Telegram trading group today.",
        "Only 5 spots left in the scheme.",
        "Deposit now before the window closes.",
        "Registration closes tonight.",
        "Send the money now or miss out.",
        "Hurry, the offer expires today.",
        "Limited seats left, join now.",
        "आज ही पैसे भेजें, तुरंत करें।",
    ),
    RedFlagCode.FAKE_REGULATORY_CLAIM: (
        "Our experts are SEBI approved.",
        "RBI approved investment scheme for all.",
        "Government approved trading platform.",
        "Officially approved by SEBI.",
        "SEBI certified profits on every trade.",
        "This trading platform is registered with SEBI.",
        "We are 100% SEBI approved.",
        "SEBI authorized guaranteed returns.",
    ),
    RedFlagCode.UNVERIFIED_ADVISER: (
        "Our SEBI expert will guide your trades.",
        "Talk to our certified trading expert.",
        "Our expert adviser manages the portfolio.",
        "A registered advisor is assigned to you.",
        "SEBI-approved investment adviser available.",
        "A professional investment adviser will call you.",
        "Our in-house certified analysts research each stock.",
    ),
    RedFlagCode.SUSPICIOUS_URL: (
        "Visit http://192.168.1.9:8080/login now.",
        "Details at https://bit.ly/3xTrading",
        "Apply here http://secure-invest.xyz/login",
        "Track it at https://xn--invst-6wa.com/portal",
        "Check http://trade-now.top/dashboard",
    ),
    RedFlagCode.THIRD_PARTY_PAYMENT: (
        "Pay to my personal account for activation.",
        "Send money to another person's account today.",
        "Transfer to this UPI id for deposit.",
        "Deposit into personal bank account to start.",
        "Pay directly to our account now.",
        "Send funds to a different account.",
        "UPI id: quickpay@okaxis for the deposit.",
    ),
    RedFlagCode.APK_DOWNLOAD: (
        "Download the APK from the link below.",
        "Install this APK to trade.",
        "Enable unknown sources and install our app.",
        "Download our trading app APK now.",
        "Our app is available as trading.apk.",
    ),
    RedFlagCode.TELEGRAM_INVESTMENT_GROUP: (
        "Join our Telegram trading group.",
        "Our t.me channel posts trading signals daily.",
        "Join the private Telegram group for trading.",
    ),
    RedFlagCode.WHATSAPP_INVESTMENT_GROUP: (
        "Join our WhatsApp investment group.",
        "Add us on WhatsApp for trading signals.",
        "Join our private WhatsApp group for investment.",
    ),
    RedFlagCode.BORROW_TO_INVEST: (
        "Take a loan and invest in this scheme.",
        "Use your credit card to invest in forex.",
        "Borrow money to invest with us.",
        "Take a personal loan for this opportunity.",
        "Apply for a loan to invest in the fund.",
    ),
    RedFlagCode.WITHDRAWAL_FEE: (
        "Pay a withdrawal fee of 10% before release.",
        "A processing fee before withdrawal applies.",
        "Unlock your profits after payment.",
        "Pay withdrawal tax before release.",
        "A release fee is needed before you withdraw.",
    ),
    RedFlagCode.ACCOUNT_ACTIVATION_FEE: (
        "Pay an activation fee to open the account.",
        "Account activation payment required.",
        "Deposit activation amount to begin trading.",
        "Pay to activate your trading account.",
        "An account unlocking charge of 5,000 applies.",
    ),
    RedFlagCode.FAKE_PROFIT_SCREENSHOT: (
        "Look at my profits in this screenshot.",
        "See these profit screenshots attached.",
        "Proof of our profits below.",
        "Here is our withdrawal proof.",
        "Rs 1 lakh profit screenshot attached.",
        "Attached image shows payment proof.",
    ),
    RedFlagCode.IMPERSONATION: (
        "I am from SEBI, this is official.",
        "We are calling from the regulator regarding your account.",
        "I am an NSE officer speaking with you.",
        "Official SEBI representative here.",
        "I work as a government investment adviser.",
        "This is the official bank investment department.",
        "Speaking on behalf of SEBI regarding your refund.",
    ),
}


# ---------------------------------------------------------------------------
# Per-rule negatives: legitimate content that must NOT trip that rule.
# ---------------------------------------------------------------------------

NEGATIVES: dict[RedFlagCode, tuple[str, ...]] = {
    RedFlagCode.GUARANTEED_RETURN: (
        "High returns are possible.",
        "Investment returns vary depending on market conditions.",
        "Past performance does not guarantee future results.",
        "Past performance does not guarantee future returns.",
        "Investors should evaluate risks before investing.",
        "The company reported a 30% annual return last year.",
        "Some investments may generate higher returns but also carry higher risk.",
        "Returns cannot be guaranteed in any market.",
        "We do not guarantee any specific return.",
        "The objective is capital preservation, not guaranteed returns.",
    ),
    RedFlagCode.UNREALISTIC_RETURN: (
        "Investment returns vary depending on market conditions.",
        "The company reported a 30% annual return last year.",
        "We offer a 12% annualised return on this bond.",
        "Our fund delivered 8% monthly average since inception.",
        "The scheme delivered 15% per annum in 2023.",
        "The company reported a 90% annual return in the year ended 2024.",
        "Expected returns of 14% a year are typical for this category.",
    ),
    RedFlagCode.URGENCY_PRESSURE: (
        "The offer closes on 15 March 2027.",
        "Our workshop is today at 5pm.",
        "The quarterly report is published each month.",
        "Please read the risk disclosure before investing.",
        "The meeting will commence at 10:00 AM on Monday.",
        "The offer is open until further notice.",
        "Returns are credited to your account on the first working day of the month.",
    ),
    RedFlagCode.FAKE_REGULATORY_CLAIM: (
        "SEBI requires investment advisers to be registered with the regulator.",
        "How to verify whether an adviser is registered on the SEBI website.",
        "The regulation mandates that funds disclose their holdings quarterly.",
        "SEBI's investor education portal explains mutual fund basics.",
        "Only registered investment advisers may give investment advice.",
        "Our company is not SEBI registered.",
    ),
    RedFlagCode.UNVERIFIED_ADVISER: (
        "Our customer support team is available 24/7.",
        "The scheme document lists all fees and charges.",
        "The fund is managed by an internal research team.",
        "Investment advisory fees are disclosed in the factsheet.",
        "Please consult the prospectus before investing.",
    ),
    RedFlagCode.SUSPICIOUS_URL: (
        "Visit https://www.sebi.gov.in for the official list.",
        "Our website is https://www.nseindia.com/market-data.",
        "Read more at https://www.rbi.org.in/notifications.",
        "Contact us at https://example.com/contact-us.",
        "Call us on +91 98765 43210 for support.",
    ),
    RedFlagCode.THIRD_PARTY_PAYMENT: (
        "Transfer the amount to the client clearing account of the exchange.",
        "Payments are made to the registered demat account.",
        "The fee is deducted from your mutual fund folio.",
        "Withdrawals are credited to your registered bank account.",
        "Use the payment gateway on the official website.",
    ),
    RedFlagCode.APK_DOWNLOAD: (
        "Download the app from the official Google Play Store.",
        "Install the latest version of your banking app.",
        "You can enable two-factor authentication in settings.",
        "Our mobile application is available on the App Store.",
        "The software is available for Windows and macOS.",
    ),
    RedFlagCode.TELEGRAM_INVESTMENT_GROUP: (
        "Contact support on WhatsApp.",
        "Our customer care is available on Telegram.",
        "Download the latest update on Telegram.",
        "Our website is www.example.com.",
    ),
    RedFlagCode.WHATSAPP_INVESTMENT_GROUP: (
        "Contact support on WhatsApp.",
        "Message us on WhatsApp for your order status.",
        "Call our helpline for account queries.",
    ),
    RedFlagCode.BORROW_TO_INVEST: (
        "Borrowers must repay the principal and interest.",
        "The home loan EMI has increased this quarter.",
        "Investors should not borrow to invest.",
        "Credit card interest rates are high this year.",
        "Investors should evaluate risks before investing.",
    ),
    RedFlagCode.WITHDRAWAL_FEE: (
        "Withdrawal requests are processed within three working days.",
        "You can withdraw your balance at any time.",
        "There is no charge for redemption of direct plan units.",
        "Exit loads are disclosed in the scheme document.",
        "The nominee can withdraw after the death of the investor.",
    ),
    RedFlagCode.ACCOUNT_ACTIVATION_FEE: (
        "Your account is activated automatically after verification.",
        "Activation is free for all new investors.",
        "The app activates your trading session automatically.",
        "Account opening is subject to KYC verification.",
        "There is no activation charge for existing clients.",
    ),
    RedFlagCode.FAKE_PROFIT_SCREENSHOT: (
        "Please review the fund factsheet for historical performance.",
        "Audited financial statements are published quarterly.",
        "Returns are credited to your bank account.",
        "Our annual report contains audited profit figures.",
        "The prospectus explains the risk and return of the scheme.",
    ),
    RedFlagCode.IMPERSONATION: (
        "SEBI's investor education portal explains mutual fund basics.",
        "If you are unsure, contact the regulator directly.",
        "The regulator publishes monthly enforcement news.",
        "This is a general disclaimer about investment risk.",
        "The government has published new rules for digital lending.",
    ),
}


@pytest.fixture(scope="module")
def engine() -> RedFlagEngine:
    """Engine built on isolated settings (no API keys, default thresholds)."""
    return RedFlagEngine(Settings(_env_file=None, groq_api_key="", serpapi_key=""))


@pytest.fixture(scope="module")
def demo_flags(engine: RedFlagEngine):
    """Red flags detected in the synthetic demo input."""
    return engine.detect(DEMO_INPUT)


# ---------------------------------------------------------------------------
# Catalogue completeness
# ---------------------------------------------------------------------------


def test_every_rule_has_at_least_one_positive_sample() -> None:
    assert set(POSITIVES) == set(RedFlagCode)
    assert all(samples for samples in POSITIVES.values())


def test_every_rule_has_at_least_one_negative_sample() -> None:
    assert set(NEGATIVES) == set(RedFlagCode)
    assert all(samples for samples in NEGATIVES.values())


# ---------------------------------------------------------------------------
# Positive detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", sorted(RedFlagCode, key=lambda item: item.value))
def test_rule_detects_its_positive_samples(engine: RedFlagEngine, code: RedFlagCode) -> None:
    for text in POSITIVES[code]:
        assert code in engine.detect_codes(text), f"{code.value} missed: {text!r}"


def test_demo_input_triggers_expected_indicators(engine: RedFlagEngine, demo_flags) -> None:
    detected = {flag.code for flag in demo_flags}

    assert DEMO_EXPECTED_CODES <= detected


def test_demo_input_produces_many_independent_indicators(engine: RedFlagEngine, demo_flags) -> None:
    """Multiple independent indicators, not a single trigger (product §43)."""
    assert len(demo_flags) >= 7


def test_demo_input_flags_are_high_or_medium_only(engine: RedFlagEngine, demo_flags) -> None:
    for flag in demo_flags:
        assert flag.severity in {Severity.HIGH, Severity.MEDIUM, Severity.CRITICAL}


# ---------------------------------------------------------------------------
# False-positive control
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", sorted(RedFlagCode, key=lambda item: item.value))
def test_rule_does_not_fire_on_legitimate_text(engine: RedFlagEngine, code: RedFlagCode) -> None:
    for text in NEGATIVES[code]:
        assert code not in engine.detect_codes(text), f"{code.value} false positive: {text!r}"


@pytest.mark.parametrize(
    "text",
    [
        "High returns are possible.",
        "Investment returns vary depending on market conditions.",
        "Past performance does not guarantee future results.",
        "Investors should evaluate risks before investing.",
        "The company reported a 30% annual return last year.",
        "Some investments may generate higher returns but also carry higher risk.",
        "Returns cannot be guaranteed in any market.",
    ],
)
def test_critical_regression_high_returns_is_possible(engine: RedFlagEngine, text: str) -> None:
    """Regression guard for the product's explicit false-positive example."""
    assert RedFlagCode.GUARANTEED_RETURN not in engine.detect_codes(text)


@pytest.mark.parametrize(
    "text",
    [
        "SEBI's investor education portal explains mutual fund basics.",
        "Please review the fund factsheet for historical performance.",
        "Visit https://www.sebi.gov.in for the official list.",
        "Contact support on WhatsApp.",
        "The quarterly report is published each month.",
    ],
)
def test_benign_financial_information_produces_no_indicators(
    engine: RedFlagEngine, text: str
) -> None:
    """Legitimate financial information must not be flagged (test scenario 2)."""
    assert engine.detect(text) == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "WE GUARANTEE 35% MONTHLY RETURNS.",
        "we guarantee 35% monthly returns.",
        "We GuaRantee 35% MoNthLy Returns.",
        "We   guarantee   35%   monthly   returns.",
        "Guaranteed!! Returns... 100% assured!!!",
        "\U0001F680 Guaranteed 40% monthly returns! Join now \U0001F680",
        "Join our private\nTelegram trading group\nfor daily signals.",
        "Act\tnow\n\nJoin us.",
    ],
)
def test_detection_is_case_punctuation_and_whitespace_insensitive(
    engine: RedFlagEngine, text: str
) -> None:
    assert engine.detect(text), f"expected at least one indicator for {text!r}"


def test_mixed_hindi_english_text_is_handled(engine: RedFlagEngine) -> None:
    text = "हम 35% monthly returns की गारंटी देते हैं।"

    assert engine.detect_codes(text) >= {
        RedFlagCode.GUARANTEED_RETURN,
        RedFlagCode.UNREALISTIC_RETURN,
    }


def test_currency_symbols_and_indian_numbering(engine: RedFlagEngine) -> None:
    assert RedFlagCode.UNREALISTIC_RETURN in engine.detect_codes(
        "₹10,000 becomes ₹1,00,000 in one month."
    )


def test_phone_numbers_are_not_treated_as_urls(engine: RedFlagEngine) -> None:
    assert RedFlagCode.SUSPICIOUS_URL not in engine.detect_codes(
        "For support call +91 98765 43210 between 10am and 6pm."
    )


def test_repeated_phrases_produce_one_finding_with_multiple_spans(engine: RedFlagEngine) -> None:
    text = "Guaranteed returns. Guaranteed returns. Guaranteed returns."
    flags = [flag for flag in engine.detect(text) if flag.code is RedFlagCode.GUARANTEED_RETURN]

    assert len(flags) == 1
    assert flags[0].occurrence_count == 3


# ---------------------------------------------------------------------------
# Evidence spans
# ---------------------------------------------------------------------------


def test_evidence_spans_slice_exactly_from_the_original_input(
    engine: RedFlagEngine, demo_flags
) -> None:
    for flag in demo_flags:
        start, end = flag.evidence_span.start, flag.evidence_span.end
        assert DEMO_INPUT[start:end] == flag.matched_text
        assert 0 <= start < end <= len(DEMO_INPUT)


def test_additional_spans_also_slice_exactly(engine: RedFlagEngine) -> None:
    text = "Guaranteed returns here and guaranteed returns there."
    flag = next(
        flag for flag in engine.detect(text) if flag.code is RedFlagCode.GUARANTEED_RETURN
    )

    for span in flag.additional_spans:
        assert text[span.start : span.end] == span.text


def test_matched_text_always_occurs_verbatim_in_the_input(engine: RedFlagEngine) -> None:
    for text in POSITIVES[RedFlagCode.GUARANTEED_RETURN] + POSITIVES[RedFlagCode.WITHDRAWAL_FEE]:
        for flag in engine.detect(text):
            assert flag.matched_text in text


def test_evidence_span_offsets_are_never_reconstructed(engine: RedFlagEngine) -> None:
    """The span must come from the original string, not a normalised copy."""
    text = "We   GUARANTEE   35% monthly returns."

    flag = next(f for f in engine.detect(text) if f.code is RedFlagCode.GUARANTEED_RETURN)

    assert DEMO_INPUT == DEMO_INPUT  # sanity
    assert text[flag.evidence_span.start : flag.evidence_span.end] == flag.matched_text


# ---------------------------------------------------------------------------
# Deduplication and sorting
# ---------------------------------------------------------------------------


def test_one_finding_per_code_despite_multiple_matches(engine: RedFlagEngine) -> None:
    text = (
        "guaranteed returns, guaranteed profit, 100% guaranteed, "
        "risk-free returns, assured income, no loss guaranteed"
    )
    codes = [flag.code for flag in engine.detect(text)]

    assert len(codes) == len(set(codes))


def test_results_are_sorted_by_severity_then_weight_then_code(engine: RedFlagEngine) -> None:
    flags = engine.detect(DEMO_INPUT)
    keys = [flag.sort_key for flag in flags]

    assert keys == sorted(keys)


def test_sorting_prefers_higher_severity_first(engine: RedFlagEngine) -> None:
    flags = engine.detect(DEMO_INPUT)
    ranks = [severity_rank(flag.severity) for flag in flags]

    assert ranks == sorted(ranks)


def test_detection_is_deterministic(engine: RedFlagEngine) -> None:
    first = engine.detect(DEMO_INPUT)
    second = engine.detect(DEMO_INPUT)

    assert [flag.model_dump() for flag in first] == [flag.model_dump() for flag in second]


def test_detection_is_stable_across_engine_instances() -> None:
    settings = Settings(_env_file=None)
    one = RedFlagEngine(settings).detect(DEMO_INPUT)
    two = RedFlagEngine(settings).detect(DEMO_INPUT)

    assert [flag.code for flag in one] == [flag.code for flag in two]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_weights_are_configurable() -> None:
    engine = RedFlagEngine(
        Settings(_env_file=None, risk_weight_guaranteed_return=77, risk_weight_impersonation=5)
    )

    guaranteed = engine.weight_for(RedFlagCode.GUARANTEED_RETURN)
    impersonation = engine.weight_for(RedFlagCode.IMPERSONATION)

    assert (guaranteed, impersonation) == (77, 5)


def test_configured_weight_is_used_in_findings() -> None:
    engine = RedFlagEngine(Settings(_env_file=None, risk_weight_guaranteed_return=33))

    flag = next(f for f in engine.detect(DEMO_INPUT) if f.code is RedFlagCode.GUARANTEED_RETURN)

    assert flag.weight == 33


def test_default_weights_match_the_documented_heuristics() -> None:
    expected = {
        RedFlagCode.GUARANTEED_RETURN: 20,
        RedFlagCode.UNREALISTIC_RETURN: 20,
        RedFlagCode.URGENCY_PRESSURE: 15,
        RedFlagCode.FAKE_REGULATORY_CLAIM: 25,
        RedFlagCode.UNVERIFIED_ADVISER: 20,
        RedFlagCode.SUSPICIOUS_URL: 10,
        RedFlagCode.THIRD_PARTY_PAYMENT: 15,
        RedFlagCode.APK_DOWNLOAD: 15,
        RedFlagCode.TELEGRAM_INVESTMENT_GROUP: 10,
        RedFlagCode.WHATSAPP_INVESTMENT_GROUP: 10,
        RedFlagCode.BORROW_TO_INVEST: 15,
        RedFlagCode.WITHDRAWAL_FEE: 20,
        RedFlagCode.ACCOUNT_ACTIVATION_FEE: 15,
        RedFlagCode.FAKE_PROFIT_SCREENSHOT: 10,
        RedFlagCode.IMPERSONATION: 25,
    }
    engine = RedFlagEngine(Settings(_env_file=None))

    assert {code: engine.weight_for(code) for code in RedFlagCode} == expected


def test_monthly_return_threshold_is_configurable() -> None:
    lenient = RedFlagEngine(Settings(_env_file=None, unrealistic_monthly_return_threshold=50))
    text = "We promise 35% monthly returns."

    assert RedFlagCode.UNREALISTIC_RETURN not in lenient.detect_codes(text)


def test_annual_return_threshold_is_configurable() -> None:
    strict = RedFlagEngine(Settings(_env_file=None, unrealistic_annual_return_threshold=30))
    text = "We deliver 45% annual returns."

    assert RedFlagCode.UNREALISTIC_RETURN in strict.detect_codes(text)


def test_currency_growth_threshold_is_configurable() -> None:
    lenient = RedFlagEngine(Settings(_env_file=None, unrealistic_currency_growth_multiple=50))

    assert RedFlagCode.UNREALISTIC_RETURN not in lenient.detect_codes(
        "₹10,000 becomes ₹1,00,000 in one month."
    )


def test_negative_weight_falls_back_safely() -> None:
    engine = RedFlagEngine(Settings(_env_file=None, risk_weight_guaranteed_return=-5))

    assert engine.weight_for(RedFlagCode.GUARANTEED_RETURN) == 0


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [None, "", "   ", "\n\n"])
def test_empty_input_yields_no_findings(engine: RedFlagEngine, text: str | None) -> None:
    assert engine.detect(text) == []


def test_engine_uses_supplied_settings() -> None:
    settings = Settings(_env_file=None)

    assert RedFlagEngine(settings).settings is settings


def test_engine_works_without_explicit_settings() -> None:
    """No settings passed must not raise; the cached settings are used."""
    assert RedFlagEngine().detect(DEMO_INPUT)


def test_detect_codes_matches_detect(engine: RedFlagEngine) -> None:
    assert engine.detect_codes(DEMO_INPUT) == {flag.code for flag in engine.detect(DEMO_INPUT)}
