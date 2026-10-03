"""The multilingual report contract over HTTP (Phase 15).

The investigation engine is language-independent; these tests
are what prove the API keeps that promise while adding a
localized presentation layer. Four properties are pinned:

**Every response carries a report.** The `report` field is
populated in the requested language — English when the caller
said nothing — on both the write path and retrieval.

**Only the presentation differs.** A report retrieved in
Hindi describes exactly what the English one described: the
canonical fields are compared value-for-value against the
English retrieval, because a localized report that changed a
claim, a verdict or a risk score would be a different
investigation, not a different rendering.

**Refusals are typed.** A language this version does not
render is a `422` carrying the rendered languages, so a
client can correct itself instead of guessing.

**The choice is discoverable.** `/limits` publishes the
languages the API renders.

Every case runs the real graph against offline services.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.persistence_factories import REGULATORY_CONTENT

TEXT_URL = "/api/investigations/text"
LIMITS_URL = "/api/investigations/limits"

#: The presentation-layer keys that are allowed to differ by language.
LOCALIZED_KEYS = {"language", "report"}


def _post_text(api_client: TestClient, language: str | None = None) -> dict:
    """Investigate the fixture content, optionally in a language."""
    payload: dict[str, object] = {"text": REGULATORY_CONTENT}
    if language is not None:
        payload["language"] = language
    response = api_client.post(TEXT_URL, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _canonical(body: dict) -> dict:
    """The language-neutral part of a response body."""
    return {key: value for key, value in body.items() if key not in LOCALIZED_KEYS}


# -- the report is populated in the requested language --------------


def test_a_text_investigation_carries_an_english_report_by_default(
    api_client: TestClient,
) -> None:
    """Omitting the language renders English, on the write path too."""
    body = _post_text(api_client)

    assert body["language"] == "en"
    report = body["report"]
    assert report["language"] == "en"
    assert report["sections"]["risk_assessment"] == "Risk Assessment"
    assert report["labels"]["risk_level"]["HIGH"] == "High"


@pytest.mark.parametrize(
    ("language", "section_key", "expected"),
    [
        ("hi", "claims", "दावे"),
        ("mr", "claims", "दावे"),
        ("hi", "red_flags", "लाल झंडे"),
        ("mr", "red_flags", "लाल धजे"),
    ],
)
def test_the_report_is_rendered_in_the_requested_language(
    api_client: TestClient,
    language: str,
    section_key: str,
    expected: str,
) -> None:
    """Each language renders its own section titles."""
    body = _post_text(api_client, language=language)

    assert body["language"] == language
    assert body["report"]["language"] == language
    assert body["report"]["sections"][section_key] == expected


def test_the_fixed_prose_is_localized(api_client: TestClient) -> None:
    """The disclaimer, guidance and caveat travel in the report."""
    hindi = _post_text(api_client, language="hi")
    marathi = _post_text(api_client, language="mr")

    assert hindi["report"]["disclaimer"] != marathi["report"]["disclaimer"]
    assert hindi["report"]["safety_guidance"]
    assert hindi["report"]["risk_caveat"]
    assert "निवेश सलाह नहीं है" in hindi["report"]["safety_guidance"]


def test_the_summary_carries_the_canonical_numbers(
    api_client: TestClient,
) -> None:
    """The localized summary interpolates the run's own counts."""
    body = _post_text(api_client, language="hi")
    report = body["report"]

    red_flags = len(body["red_flags"])
    claims = len(body["claims"])
    verified = sum(
        1
        for result in body["verification_results"]
        if result["status"] == "VERIFIED"
    )
    summary = report["summary"]

    assert str(red_flags) in summary
    assert str(claims) in summary
    assert str(verified) in summary


# -- only the presentation differs ----------------------------------


@pytest.mark.parametrize("language", ["hi", "mr"])
def test_retrieval_in_a_language_keeps_every_canonical_field(
    api_client: TestClient,
    language: str,
) -> None:
    """A localized retrieval is the same investigation, differently worded."""
    posted = _post_text(api_client)
    investigation_id = posted["investigation_id"]

    english = api_client.get(f"/api/investigations/{investigation_id}").json()
    localized = api_client.get(
        f"/api/investigations/{investigation_id}?language={language}"
    ).json()

    assert localized["language"] == language
    assert localized["report"]["language"] == language
    assert localized["report"]["sections"] != english["report"]["sections"]
    assert _canonical(localized) == _canonical(english)


def test_retrieval_defaults_to_english_when_the_parameter_is_absent(
    api_client: TestClient,
) -> None:
    """No `language` parameter, or an empty one, means English."""
    posted = _post_text(api_client)
    investigation_id = posted["investigation_id"]

    absent = api_client.get(f"/api/investigations/{investigation_id}").json()
    empty = api_client.get(
        f"/api/investigations/{investigation_id}?language="
    ).json()

    assert absent["report"]["language"] == "en"
    assert empty["report"]["language"] == "en"


def test_the_language_parameter_is_case_insensitive(
    api_client: TestClient,
) -> None:
    """`HI` names the same language as `hi`."""
    posted = _post_text(api_client)
    investigation_id = posted["investigation_id"]

    body = api_client.get(
        f"/api/investigations/{investigation_id}?language=HI"
    ).json()

    assert body["report"]["language"] == "hi"


def test_a_posted_language_is_echoed_in_the_retrieved_report(
    api_client: TestClient,
) -> None:
    """The report a `POST` returns survives storage unchanged."""
    posted = _post_text(api_client, language="mr")
    investigation_id = posted["investigation_id"]

    retrieved = api_client.get(
        f"/api/investigations/{investigation_id}?language=mr"
    ).json()

    assert retrieved["report"] == posted["report"]


# -- refusals ---------------------------------------------------------


@pytest.mark.parametrize("value", ["fr", "english", "EN-US"])
def test_an_unrendered_language_is_refused_with_a_typed_422(
    api_client: TestClient,
    value: str,
) -> None:
    """A language this version does not render is a caller error."""
    posted = _post_text(api_client)
    investigation_id = posted["investigation_id"]

    response = api_client.get(
        f"/api/investigations/{investigation_id}?language={value}"
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "LANGUAGE_NOT_SUPPORTED"
    assert error["detail"]["supported_languages"] == ["en", "hi", "mr"]


def test_surrounding_whitespace_is_trimmed_before_resolving(
    api_client: TestClient,
) -> None:
    """A padded value names the language it pads."""
    posted = _post_text(api_client)
    investigation_id = posted["investigation_id"]

    body = api_client.get(
        f"/api/investigations/{investigation_id}?language=%20hi%20"
    ).json()

    assert body["report"]["language"] == "hi"


def test_an_unrendered_language_is_refused_on_the_write_path(
    api_client: TestClient,
) -> None:
    """The write path enforces the same language set, through
    request validation: the `language` field is a closed enum,
    so an unknown value never reaches the graph.
    """
    response = api_client.post(TEXT_URL, json={"text": "Hello.", "language": "fr"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"


# -- discoverability ----------------------------------------------------


def test_the_limits_endpoint_publishes_the_rendered_languages(
    api_client: TestClient,
) -> None:
    """A client can discover the language set before submitting."""
    limits = api_client.get(LIMITS_URL).json()

    assert limits["languages"] == ["en", "hi", "mr"]
    assert limits["translation_enabled"] is False
