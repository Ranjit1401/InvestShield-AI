"""The full HTTP input contract of the investigation API (Phase 10).

`tests/api/test_investigation_endpoints.py` already covers the headline refusals
this project cares most about. What it does not cover is **breadth**: the content
kinds a real user of an Indian investment-safety product actually submits, the
malformed requests a client library produces when it is misconfigured, and the
exact status code for each.

Two properties are asserted throughout.

**Every failure is the documented envelope.** `{"error": {"code", "message"}}` on
every non-2xx, whatever produced it — a pydantic validation refusal, a rejected
submission, an unsupported input kind, or an internal fault. A client that handles
one error shape must not meet a second.

**No refusal ever leaks internal detail.** The `detail` of a validation error is
deliberately verbose — FastAPI's own `exc.errors()`, naming the offending field —
so these tests pin down what that verbosity is allowed to contain: field paths,
types and codes. Never a stack trace, a SQL statement, a DSN, or a provider's
error text.

Nothing here fakes the pipeline. Every "valid" case runs the **real** graph
against offline services, so a content kind that the pipeline cannot actually
handle would fail here rather than being hidden behind a stub.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.contract_helpers import semantic_response
from tests.persistence_factories import MESSY_CONTENT, REGULATORY_CONTENT

#: Prefixes the API serves. Built rather than repeated, so a prefix change is a
#: one-line edit here instead of a silent `404` in every assertion.
TEXT_URL = "/api/investigations/text"
URL_URL = "/api/investigations/url"
TYPED_URL = "/api/investigations"
LIMITS_URL = "/api/investigations/limits"
HEALTH_URL = "/api/health"


def _error_body(response) -> dict[str, object]:
    """Return the documented error envelope from a failed response.

    Args:
        response: A `TestClient` response with a non-2xx status.

    Returns:
        The decoded `error` object.

    Raises:
        AssertionError: If the body is not the documented envelope.
    """
    body = response.json()
    assert set(body) == {"error"}, (
        f"the error body carried {sorted(body)} rather than exactly 'error'"
    )
    error = body["error"]
    assert isinstance(error, dict)
    assert "code" in error and "message" in error
    assert isinstance(error["code"], str) and error["code"]
    assert isinstance(error["message"], str) and error["message"]
    return error


class TestValidSubmissions:
    """Content a real client submits must be accepted and investigated."""

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("Acme Capital Advisors is SEBI registered.", id="plain_english"),
            pytest.param(MESSY_CONTENT, id="synthetic_scam_content"),
            pytest.param(
                "Line one is harmless.\nLine two promises 40% returns.\nLine three.",
                id="multiline",
            ),
            pytest.param(
                "First line.\r\nSecond line about guaranteed returns.", id="crlf_multiline"
            ),
            pytest.param(
                "🚨 Exclusive AI Trading Opportunity 🚨 35% returns — guaranteed! ₹50,000",
                id="unicode_symbols",
            ),
            pytest.param(
                "अक्मे कैपिटल एडवाइज़र्स द्वारा 30% मासिक रिटर्न की गारंटी दी जाती है।",
                id="devanagari_hindi",
            ),
            pytest.param("७५% परतावा, निश्चित नुस्सान नाही, आजच संधीकरा.", id="devanagari_marathi"),
            pytest.param(
                "Bhai guaranteed 40% returns, no risk, invest abhi.", id="hinglish_romanised"
            ),
            pytest.param("   Acme Capital Advisors is SEBI registered.   ", id="whitespace_padded"),
            pytest.param("x", id="single_character"),
            pytest.param("!!!???---*** 30% returns!!! ??? --- ***", id="punctuation_heavy"),
            pytest.param("guaranteed returns " * 20, id="repeated_token"),
            pytest.param("99999999999999999999999999", id="digits_only"),
            pytest.param(
                "Consider your horizon and risk tolerance. " * 200, id="benign_long_advice"
            ),
        ],
    )
    def test_a_content_kind_is_investigated_not_refused(self, api_client: TestClient, text: str) -> None:
        """Every accepted content kind produces a complete, contract-shaped result.

        Args:
            api_client: A client running the real graph, offline.
            text: The submitted content.
        """
        response = api_client.post(TEXT_URL, json={"text": text})

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == _DOCUMENTED_RESPONSE_KEYS
        assert body["status"] in {"COMPLETED", "PARTIAL"}
        assert body["investigation_id"].startswith("inv_")
        assert body["input_type"] == "TEXT"
        # Every collection is present even when empty, so a client can read
        # `claims.length` without first testing whether the field exists.
        for key in (
            "claims",
            "entities",
            "red_flags",
            "verification_results",
            "evidence",
            "timeline",
            "limitations",
            "warnings",
            "errors",
        ):
            assert isinstance(body[key], list), f"{key} was not a list"

    def test_a_long_but_permitted_submission_is_accepted(self, api_client: TestClient) -> None:
        """The maximum permitted length is a real limit, not an off-by-one refusal.

        Args:
            api_client: A client running the real graph, offline.
        """
        text = "Acme Capital Advisors guarantees returns. " * 450
        assert 1 < len(text) <= 20_000

        response = api_client.post(TEXT_URL, json={"text": text})

        assert response.status_code == 200, response.text
        assert response.json()["status"] in {"COMPLETED", "PARTIAL"}

    def test_text_at_the_documented_maximum_is_accepted(self, api_client: TestClient) -> None:
        """Exactly `MAX_TEXT_LENGTH` characters is inside the limit.

        The boundary is asserted at the HTTP layer as well as in the schema test,
        because a validator that is correct in isolation and wrong in a route
        (an off-by-one, or a length applied to the normalised rather than the raw
        string) is exactly what a contract test exists to catch.

        Args:
            api_client: A client running the real graph, offline.
        """
        text = "a" * 20_000

        response = api_client.post(TEXT_URL, json={"text": text})

        assert response.status_code == 200, response.text

    def test_one_character_over_the_maximum_is_refused(self, api_client: TestClient) -> None:
        """One character past the limit is refused, not truncated.

        Args:
            api_client: A client running the real graph, offline.
        """
        response = api_client.post(TEXT_URL, json={"text": "a" * 20_001})

        assert response.status_code == 422
        _error_body(response)

    @pytest.mark.parametrize("language", ["en", "hi", "mr"])
    def test_every_declared_language_is_accepted_and_echoed(
        self, api_client: TestClient, language: str
    ) -> None:
        """Each declared language is recorded and echoed, and none is translated.

        Recording rather than honouring is the documented Phase 8 behaviour; the
        risk is a later change silently *claiming* to translate, so the English
        wording is asserted here too.

        Args:
            api_client: A client running the real graph, offline.
            language: The declared language code.
        """
        response = api_client.post(
            TEXT_URL, json={"text": REGULATORY_CONTENT, "language": language}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["language"] == language
        # No translation happens, so at least one message is still English.
        english = [w for w in body["warnings"] if re_isascii(w["message"])]
        assert body["warnings"] == [] or english, (
            "warnings were expected to remain in English; the language field is "
            "recorded, not honoured"
        )

    def test_an_undeclared_language_is_refused(self, api_client: TestClient) -> None:
        """An invented language code is a typed refusal, not a silent default.

        Args:
            api_client: A client running the real graph, offline.
        """
        response = api_client.post(
            TEXT_URL, json={"text": REGULATORY_CONTENT, "language": "fr"}
        )

        assert response.status_code == 422
        _error_body(response)


class TestInvalidSubmissions:
    """A malformed request must be refused with the documented envelope."""

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param({}, id="missing_text"),
            pytest.param({"text": None}, id="text_null"),
            pytest.param({"text": 12345}, id="text_integer"),
            pytest.param({"text": True}, id="text_boolean"),
            pytest.param({"text": ["Acme", "Capital"]}, id="text_list"),
            pytest.param({"text": {"value": "Acme"}}, id="text_object"),
            pytest.param({"text": ""}, id="text_empty"),
            pytest.param({"text": "   "}, id="text_whitespace_only"),
            pytest.param({"text": "\n\n\n"}, id="text_newline_only"),
            pytest.param({"text": "\t\t"}, id="text_tab_only"),
            pytest.param({"text": "a" * 20_001}, id="text_oversized"),
            pytest.param({"text": "Acme", "language": None}, id="language_null"),
            pytest.param({"text": "Acme", "language": 7}, id="language_wrong_type"),
            pytest.param({"text": "Acme", "depth": 3}, id="unknown_field"),
            pytest.param(
                {"text": "Acme", "is_scam": True}, id="unknown_field_with_valid_text"
            ),
            pytest.param(["Acme Capital Advisors"], id="body_is_a_list"),
            pytest.param("Acme Capital Advisors", id="body_is_a_string"),
            pytest.param(42, id="body_is_a_number"),
        ],
    )
    def test_a_malformed_body_is_refused_with_the_standard_envelope(
        self, api_client: TestClient, body: object
    ) -> None:
        """Every malformed body is a `422` carrying the documented envelope.

        Args:
            api_client: A client running the real graph, offline.
            body: The request body to send.
        """
        response = api_client.post(TEXT_URL, json=body)

        assert response.status_code == 422, response.text
        _error_body(response)

    def test_a_missing_body_is_refused(self, api_client: TestClient) -> None:
        """No body at all is a `422`, not a `500`.

        Args:
            api_client: A client running the real graph, offline.
        """
        response = api_client.post(TEXT_URL)

        assert response.status_code == 422
        _error_body(response)

    def test_malformed_json_is_refused_with_the_standard_envelope(
        self, api_client: TestClient
    ) -> None:
        """Unparseable JSON is a `422`, and the parser's own text is not echoed.

        A client that sends a truncated body should learn the request was
        unreadable. It should not learn anything about our parser or our stack.

        Args:
            api_client: A client running the real graph, offline.
        """
        response = api_client.post(
            TEXT_URL,
            content=b'{"text": "Acme", "language": }',
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 422
        _error_body(response)
        assert "Traceback" not in response.text
        assert "json.decoder" not in response.text

    def test_a_json_body_sent_as_form_data_is_refused(self, api_client: TestClient) -> None:
        """A form-encoded body is refused rather than silently coerced.

        The single most common client-side mistake: the body is built as form data
        and posted to a JSON endpoint. Coercing it would accept a request the
        client did not intend to send in this shape.

        Args:
            api_client: A client running the real graph, offline.
        """
        response = api_client.post(TEXT_URL, data={"text": "Acme Capital Advisors"})

        assert response.status_code == 422
        _error_body(response)

    def test_an_unsupported_input_type_is_refused_by_the_typed_endpoint(
        self, api_client: TestClient
    ) -> None:
        """A kind the vocabulary does not carry is refused by the schema.

        `URL` left this set in Phase 12, `IMAGE` in Phase 13, and
        `PDF` in Phase 14, which began analysing them. Every kind
        the vocabulary carries is analysed now, so a kind outside
        the vocabulary is refused by the request schema before the
        graph runs, and the accepted kinds are named in the detail.

        Args:
            api_client: A client running the real graph, offline.
        """
        for kind in ("PODCAST",):
            response = api_client.post(
                TYPED_URL, json={"input_type": kind, "text": "some content"}
            )

            assert response.status_code == 422, f"{kind}: {response.text}"
            error = _error_body(response)
            assert error["code"] == "VALIDATION_ERROR"
            # The validation detail names every accepted kind, so
            # the refusal tells the caller what to send instead.
            detail = " ".join(
                str(item.get("msg", "")) for item in error["detail"]
            )
            for accepted in ("TEXT", "URL", "IMAGE", "PDF"):
                assert accepted in detail

    def test_an_invented_input_type_is_refused(self, api_client: TestClient) -> None:
        """A kind that was never declared is refused by the schema, not guessed at.

        Args:
            api_client: A client running the real graph, offline.
        """
        response = api_client.post(
            TYPED_URL, json={"input_type": "SPREADSHEET", "text": "Acme"}
        )

        assert response.status_code == 422
        _error_body(response)

    def test_the_shorthand_endpoint_cannot_be_redirected_to_another_input_type(
        self, api_client: TestClient
    ) -> None:
        """`/text` fixes the input type, so declaring another is refused.

        Args:
            api_client: A client running the real graph, offline.
        """
        response = api_client.post(
            TEXT_URL, json={"input_type": "URL", "text": "https://example.invalid"}
        )

        assert response.status_code == 422
        _error_body(response)

    def test_the_typed_endpoint_defaults_to_text(self, api_client: TestClient) -> None:
        """Omitting `input_type` on the typed endpoint means `TEXT`.

        Args:
            api_client: A client running the real graph, offline.
        """
        response = api_client.post(TYPED_URL, json={"text": REGULATORY_CONTENT})

        assert response.status_code == 200, response.text
        assert response.json()["input_type"] == "TEXT"


class TestErrorEnvelopeDiscipline:
    """One failure shape, whatever produced the failure."""

    @pytest.mark.parametrize(
        "response_factory",
        [
            pytest.param(lambda c: c.post(TEXT_URL, json={}), id="validation"),
            pytest.param(
                lambda c: c.post(TYPED_URL, json={"input_type": "PDF", "text": "x"}),
                id="unsupported_input_type",
            ),
            pytest.param(
                lambda c: c.get("/api/investigations/inv_does_not_exist_at_all"),
                id="missing_investigation",
            ),
            pytest.param(
                lambda c: c.get("/api/investigations?limit=0"), id="bad_paging"
            ),
            pytest.param(lambda c: c.get("/api/nonexistent-endpoint"), id="unknown_route"),
        ],
    )
    def test_every_failure_returns_the_documented_envelope(
        self, api_client: TestClient, response_factory
    ) -> None:
        """A client that handles one envelope handles all of them.

        Args:
            api_client: A client running the real graph, offline.
            response_factory: Callable producing the request to make.
        """
        response = response_factory(api_client)

        assert response.status_code >= 400
        _error_body(response)

    def test_a_refusal_never_exposes_internal_detail(self, api_client: TestClient) -> None:
        """A validation `detail` may name fields, never internals or submitted values.

        Pydantic's `errors()` is deliberately verbose and reaches the client, which
        makes it the most likely place for an internal detail to escape. Only
        `type`, `loc` and `msg` are copied, and the allowed set is asserted exactly
        so a future edit that widens it fails rather than passes unnoticed.

        `input` is excluded on purpose: it holds the value the client sent, so
        echoing it reflects the whole request body — and hands back a credential in
        full to anyone who mistakenly puts one in a field this API forbids.

        Args:
            api_client: A client running the real graph, offline.
        """
        response = api_client.post(TEXT_URL, json={"text": "", "surprise": 1})

        assert response.status_code == 422
        error = _error_body(response)
        detail = error["detail"]
        assert isinstance(detail, list) and detail

        allowed_keys = {"type", "loc", "msg"}
        allowed_types = {
            "missing", "string_too_short", "string_too_long", "extra_forbidden",
            "bool_parsing", "int_parsing", "enum", "string_type", "list_type",
            "dict_type", "int_type", "value_error",
        }
        for entry in detail:
            assert set(entry) <= allowed_keys, f"unexpected detail keys: {sorted(entry)}"
            assert entry["type"] in allowed_types, f"unexpected error type: {entry['type']}"

        assert "Traceback" not in response.text
        assert "app." not in response.text
        assert "sqlalchemy" not in response.text.lower()

    def test_a_validation_detail_never_reflects_the_submitted_body(
        self, api_client: TestClient
    ) -> None:
        """A value the client sent is not echoed back in the refusal.

        The realistic case is a client that puts a credential in a field this API
        forbids. With `extra="forbid"`, the `extra_forbidden` error's `input` is
        the whole submitted object — so a naive detail would hand the sender their
        own secret, in the response, on the error path.

        Args:
            api_client: A client running the real graph, offline.
        """
        secret = "hunter2-SUPER-SECRET"

        response = api_client.post(
            TEXT_URL, json={"text": "Acme", "api_key": secret}
        )

        assert response.status_code == 422
        assert secret not in response.text
        assert "hunter2" not in response.text

    def test_the_shared_envelope_is_used_for_method_not_allowed(
        self, api_client: TestClient
    ) -> None:
        """A wrong method is a `405` in the same envelope as everything else.

        Args:
            api_client: A client running the real graph, offline.
        """
        response = api_client.delete(LIMITS_URL)

        assert response.status_code == 405
        _error_body(response)


class TestDiscoveryEndpoints:
    """The two endpoints that need no investigation."""

    def test_limits_describes_what_this_version_accepts(
        self, api_client: TestClient
    ) -> None:
        """`/limits` advertises the real constraint, not a stale one.

        Args:
            api_client: A client running the real graph, offline.
        """
        response = api_client.get(LIMITS_URL)

        assert response.status_code == 200
        body = response.json()
        assert body["supported_input_types"] == ["TEXT", "URL", "IMAGE", "PDF"]
        assert body["max_text_length"] == 20_000
        assert body["max_url_length"] == 2048
        # Phase 13: a screenshot is bounded in bytes, the image formats it
        # may be are named, and the OCR language is discoverable too.
        assert body["max_upload_bytes"] == 10_485_760
        assert set(body["allowed_image_types"]) == {
            "image/png",
            "image/jpeg",
            "image/webp",
        }
        assert isinstance(body["ocr_languages"], str)
        assert set(body["languages"]) == {"en", "hi", "mr"}
        assert body["translation_enabled"] is False

        # The advertised maximum is the maximum the API actually enforces, and the
        # advertised kinds are exactly the ones a submission can be accepted as.
        assert api_client.post(TEXT_URL, json={"text": "a" * body["max_text_length"]}).status_code == 200
        assert api_client.post(TEXT_URL, json={"text": "a" * (body["max_text_length"] + 1)}).status_code == 422

        # A URL is bounded as a URL, not as prose, and the advertised figure is
        # the one the URL endpoint enforces. This app's graph is built without
        # URL services, so a URL the schema accepts is refused further in with a
        # `503` — which is what separates "too long" (422, the caller's) from
        # "this deployment cannot do that" (503, not the caller's).
        prefix = "https://example.com/"
        within = prefix + "a" * (body["max_url_length"] - len(prefix))
        assert len(within) == body["max_url_length"]
        assert api_client.post(URL_URL, json={"url": within}).status_code == 503
        assert api_client.post(URL_URL, json={"url": within + "a"}).status_code == 422

    def test_health_is_reachable_without_an_investigation(
        self, api_client: TestClient
    ) -> None:
        """`/health` answers, and reports no configured provider in the test app.

        Args:
            api_client: A client running the real graph, offline.
        """
        response = api_client.get(HEALTH_URL)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] in {"ok", "degraded"}
        assert "database" in body

    def test_limits_is_not_shadowed_by_the_parameterised_route(
        self, api_client: TestClient
    ) -> None:
        """`/limits` still answers after the id route was added.

        FastAPI matches in declaration order, so this is a regression guard on the
        route ordering rather than on the endpoint.

        Args:
            api_client: A client running the real graph, offline.
        """
        assert api_client.get(LIMITS_URL).status_code == 200
        assert api_client.get(LIMITS_URL).json()["max_text_length"] == 20_000


class TestContractStability:
    """The documented shape, asserted field by field."""

    @pytest.mark.parametrize("url", [TEXT_URL, TYPED_URL])
    def test_both_post_endpoints_return_the_same_documented_fields(
        self, api_client: TestClient, url: str
    ) -> None:
        """Neither write endpoint grows or drops a field relative to the contract.

        Args:
            api_client: A client running the real graph, offline.
            url: The endpoint to exercise.
        """
        payload: dict[str, object] = {"text": REGULATORY_CONTENT}
        if url == TYPED_URL:
            payload["input_type"] = "TEXT"

        body = api_client.post(url, json=payload).json()

        assert set(body) == _DOCUMENTED_RESPONSE_KEYS

    def test_retrieval_returns_the_same_documented_fields(
        self, api_client: TestClient
    ) -> None:
        """A retrieved investigation has exactly the write response's fields.

        A `GET` that added or dropped a field would break a client that treats the
        two as the same contract — which is the whole premise of D-041.

        Args:
            api_client: A client running the real graph, offline.
        """
        posted = api_client.post(TEXT_URL, json={"text": REGULATORY_CONTENT}).json()

        retrieved = api_client.get(
            f"/api/investigations/{posted['investigation_id']}"
        ).json()

        assert set(retrieved) == _DOCUMENTED_RESPONSE_KEYS
        assert retrieved == posted

    def test_a_retrieved_investigation_equals_the_live_one_apart_from_clocks(
        self, api_client: TestClient
    ) -> None:
        """Every non-timestamp field survives the round trip through the database.

        Args:
            api_client: A client running the real graph, offline.
        """
        posted = api_client.post(TEXT_URL, json={"text": MESSY_CONTENT}).json()

        retrieved = api_client.get(
            f"/api/investigations/{posted['investigation_id']}"
        ).json()

        assert semantic_response(retrieved) == semantic_response(posted)

    def test_the_response_is_json_serialisable_without_a_custom_encoder(
        self, api_client: TestClient
    ) -> None:
        """The body round-trips through `json.dumps` unchanged.

        A body that only serialises with a configured encoder would break the
        error path in `app.main`, which builds `JSONResponse` content by hand.

        Args:
            api_client: A client running the real graph, offline.
        """
        body = api_client.post(TEXT_URL, json={"text": REGULATORY_CONTENT}).json()

        assert json.loads(json.dumps(body)) == body


#: The response fields Phase 8 documented. Asserted as a set so that a field added
#: in Phase 9 or 10 without a contract decision fails here.
_DOCUMENTED_RESPONSE_KEYS = frozenset(
    {
        "investigation_id",
        "status",
        "input_type",
        "language",
        "current_stage",
        "claims",
        "entities",
        "red_flags",
        "verification_results",
        "evidence",
        "risk_assessment",
        "timeline",
        "limitations",
        "warnings",
        "errors",
        "started_at",
        "completed_at",
        # Phase 12: null for a `TEXT` submission, populated for a `URL` one.
        # Present in both so a client can read one key regardless of kind.
        "url_source",
        # Phase 13: null unless the input was a screenshot, whose decoded
        # shape and recovered text are reported so the analysis is checkable.
        "image_source",
        # Phase 14: null unless the input was a PDF, whose parsed
        # shape, page count and recovered text are reported so the
        # analysis is checkable.
        "pdf_source",
        # Phase 15: the localized presentation layer. Populated in
        # every language (English by default); the canonical fields
        # above it are identical in every language.
        "report",
    }
)


def re_isascii(text: str) -> bool:
    """Whether `text` is pure ASCII.

    Args:
        text: A message from the response.

    Returns:
        `True` when every character is ASCII.
    """
    return all(ord(character) < 128 for character in text)
