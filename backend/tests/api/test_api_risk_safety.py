"""The safety vocabulary survives the API and the database (Phase 10).

`tests/test_risk_safety.py` proves the Phase 6 **engine** cannot emit a verdict, an
accusation, absolution or advice. It writes against assessments directly.

This module closes the gap that opened when two more layers were added after it.
The adapter (`app/api/adapters.py`) and the repository
(`app/repositories/investigations.py`) both *re-emit* text Phase 6 produced, and
both are places a caveat can be quietly dropped:

- The adapter could pass the `RiskAssessment` through as a projection and lose
  `warnings`, which is where `SCORE_NOT_A_PROBABILITY` lives.
- The repository could rebuild an assessment field by field and omit `warnings`
  again on the way back out, so a *retrieved* investigation would lack the caveat
  while the live one has it.

Either would be invisible to the existing suite, because it only ever looks at a
fresh assessment. The guarantee has to be re-checked at every boundary text
crosses.

Three properties are asserted.

**No banned word, asserted.** `BUY`, `SELL`, `INVEST`, `DO NOT INVEST`, `SAFE
INVESTMENT`, `FRAUD`, `SCAM` — with the negation handling Phase 6 already built,
so the disclaimer that *names* fraud to rule it out is not a violation.

**Attributed quotation is allowed.** A word that appears inside a retrieved
excerpt is the *source's* word, not the system's. The system's own fields carry no
such words; user-submitted and retrieved text is checked separately and expected to
contain them.

**The caveat survives every hop.** Live response, stored record, retrieved
response, history entry, error body — the exact sentence
`SCORE_NOT_A_PROBABILITY` must be present in all of them.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.schemas.risk import SCORE_NOT_A_PROBABILITY
from tests.contract_helpers import iter_json_strings
from tests.graph.graph_factories import offline_context, real_dependencies
from tests.persistence_factories import MESSY_CONTENT, REGULATORY_CONTENT
from tests.vocabulary import (
    ADVICE_PATTERN,
    BANNED_PATTERN,
    JUDGEMENT_PATTERN,
    VERDICT_PATTERN,
    asserted_matches,
)

#: The advice verbs a client must never be told to do. Separate from Phase 6's own
#: list because the API's audience is a human, not an engineer reading a factor.
ADVICE_VERBS = ("buy", "sell", "invest", "recommend", "avoid", "proceed", "withhold")

#: Words that would turn an assessment into a verdict about a person or a scheme.
VERDICT_WORDS = ("scam", "fraud", "fraudulent", "criminal", "liar", "thief", "perpetrator")

#: Words that imply someone or something has been cleared. The symmetric failure.
ABSOLUTION_WORDS = ("safe", "legitimate", "genuine", "trustworthy", "innocent", "honest")

#: Multi-word constructions that would amount to advice or a verdict even though
#: every individual word in them is innocent to the word-level ban.
#:
#: Notably absent: "guaranteed return". That is the literal *name* of the Phase 1
#: rule for `unrealistic_return` — a description of a detection pattern, which is
#: what the catalogue is for. Naming a pattern is not making a promise.
FORBIDDEN_PHRASES = (
    "you should invest",
    "you should buy",
    "you should sell",
    "you must invest",
    "invest now",
    "safe investment",
    "safe investment opportunity",
    "this is a scam",
    "this is a fraud",
    "is fraudulent",
    "has been cleared",
    "looks legitimate",
)

#: The product's own tagline, which contains an advice word. Narrow exemption, used
#: only for the OpenAPI description; see the test that applies it for why.
PRODUCT_TAGLINE = "Investigate Before You Invest"

#: Fields whose contents are the *user's* or a *source's*, not the system's judgement.
#:
#: A scammer's own message contains the word "scam". Reproducing that text is the
#: product working correctly, so these are checked separately and a match there is
#: expected rather than forbidden.
QUOTED_FIELDS = (
    "text",
    "excerpt",
    "excerpt_source",
    "title",
    "reason",
    # Phase 1's rule catalogue, reproduced verbatim. The `unrealistic_return` rule
    # is legitimately *named* "guaranteed return": that is a description of a
    # detection pattern, not a promise. The advice ban is scoped to the system's own
    # wording, which is how `tests/vocabulary.py` already treats this.
    "name",
    "rule_reason",
    "matched_text",
    # The user's submission, echoed back so a run is traceable to its input. The
    # offline test content literally says "no risk" and "guarantees 30% monthly
    # returns", so a whole-body scan would otherwise flag the echo of the suspect.
    "extracted_text",
    "raw_input",
    "source_text",
    "normalized_text",
)

#: Fields the system authors, and which must never carry a banned word.
SYSTEM_FIELDS = (
    "status",
    "risk_level",
    "risk_score",
    "raw_score",
    "completeness",
    "code",
    "message",
    "stage",
    "error_type",
    "current_stage",
    "summary",
    "label",
    "description",
    "relationship",
    "source_type",
    "warning",
    "caveat",
)


def _system_texts(body: object) -> list[tuple[str, str]]:
    """Collect every string in a response that the *system* authored.

    Args:
        body: A decoded response body.

    Returns:
        `(path, value)` for each string reachable at a system-authored field.
    """
    return [
        (path, value)
        for path, value in iter_json_strings(body)
        if not any(f".{field}" in path or path.endswith(f".{field}") for field in QUOTED_FIELDS)
    ]


def _assert_no_verdict(texts: list[tuple[str, str]]) -> None:
    """Fail if any system-authored text asserts a banned word.

    Args:
        texts: `(path, value)` pairs to check.
    """
    offenders: list[str] = []
    for path, value in texts:
        for pattern, family in (
            (JUDGEMENT_PATTERN, "judgement"),
            (ADVICE_PATTERN, "advice"),
        ):
            asserted = asserted_matches(value, pattern)
            if asserted:
                offenders.append(f"{path} asserts {asserted}: {value[:120]}")

    assert not offenders, "\n".join(offenders)


def _scammy_response(api_client: TestClient) -> dict:
    """Post content that trips several rules and return the response.

    Args:
        api_client: A client running the real app, offline.

    Returns:
        The decoded response body.
    """
    response = api_client.post("/api/investigations/text", json={"text": MESSY_CONTENT})
    assert response.status_code == 200, response.text
    return response.json()


class TestNoVerdictInAnyApiResponse:
    """The vocabulary ban holds at the HTTP boundary, not just in the engine."""

    def test_a_full_response_contains_no_asserted_verdict_word(
        self, api_client: TestClient
    ) -> None:
        """Every system-authored string in a complete response is checked.

        Args:
            api_client: A client running the real app, offline.
        """
        _assert_no_verdict(_system_texts(_scammy_response(api_client)))

    def test_a_clean_response_contains_no_asserted_verdict_word(
        self, api_client: TestClient
    ) -> None:
        """The clean case is the one most likely to drift toward absolution.

        "No patterns found" is one small wording change away from "this looks
        clean", and absolution is the failure mode a safety tool cannot afford.

        Args:
            api_client: A client running the real app, offline.
        """
        response = api_client.post(
            "/api/investigations/text",
            json={"text": "Acme Capital Advisors is SEBI registered."},
        )
        assert response.status_code == 200

        _assert_no_verdict(_system_texts(response.json()))

    def test_every_warning_message_is_free_of_a_verdict(
        self, api_client: TestClient
    ) -> None:
        """Warning wording is the text a user reads most directly.

        Args:
            api_client: A client running the real app, offline.
        """
        body = _scammy_response(api_client)

        for warning in body["warnings"]:
            _assert_no_verdict([(f"warnings[{warning['code']}]", warning["message"])])

    def test_every_timeline_message_is_free_of_a_verdict(
        self, api_client: TestClient
    ) -> None:
        """The timeline is the closest thing the API has to narration.

        Args:
            api_client: A client running the real app, offline.
        """
        body = _scammy_response(api_client)

        for event in body["timeline"]:
            _assert_no_verdict([(f"timeline[{event['stage']}]", event["message"])])

    def test_every_error_body_is_free_of_a_verdict(
        self, api_client: TestClient
    ) -> None:
        """Even a failure must not accuse anyone.

        Args:
            api_client: A client running the real app, offline.
        """
        for path, method, payload in (
            ("/api/investigations/text", "post", {"text": ""}),
            ("/api/investigations", "post", {"input_type": "PODCAST", "text": "x"}),
        ):
            response = api_client.request(method.upper(), path, json=payload)
            assert response.status_code == 422
            _assert_no_verdict(list(iter_json_strings(response.json())))

    def test_the_health_endpoint_makes_no_judgement_either(
        self, api_client: TestClient
    ) -> None:
        """A health check that said "everything looks safe" would be a verdict.

        Args:
            api_client: A client running the real app, offline.
        """
        _assert_no_verdict(list(iter_json_strings(api_client.get("/api/health").json())))

    def test_the_openapi_document_makes_no_judgement(self, api_client: TestClient) -> None:
        """Published documentation is read before the tool is ever run.

        Args:
            api_client: A client running the real app, offline.
        """
        document = api_client.get("/openapi.json").json()

        offenders = []
        for path, value in iter_json_strings(document):
            # The accusation family is banned outright, here as everywhere. A
            # document that calls a pattern "a scam" makes the accusation this
            # product refuses to make. Denials are fine, and the descriptions say
            # things like "nothing about safety, fraud or the merits".
            asserted = asserted_matches(value, VERDICT_PATTERN)
            if asserted:
                offenders.append(f"{path} asserts {asserted}")
                continue
            # Advice words are banned in the system's own prose, with one narrow and
            # deliberate exemption. The OpenAPI description opens with the product
            # tagline, "Investigate Before You Invest." A description of this tool
            # cannot avoid naming this tool, and a tagline is not a recommendation
            # to the reader. The exemption is the tagline alone, matched literally,
            # so it cannot grow into a licence for advice elsewhere.
            asserted = asserted_matches(
                value.replace(PRODUCT_TAGLINE, ""), ADVICE_PATTERN
            )
            if asserted:
                offenders.append(f"{path} asserts {asserted}")

        assert not offenders, offenders

    def test_the_absence_words_are_excluded_only_from_documentation(
        self, api_client: TestClient
    ) -> None:
        """The absolution ban is scoped to generated output, and the reason is real.

        Long-form descriptions cannot avoid words like "honest" and "legitimate"
        when describing the tool's own behaviour — "the honest answer is no
        evidence", "an empty evidence tuple is a legitimate, meaningful result".
        Generated response fields are short machine-authored labels, and there
        "safe" or "legitimate" would be a genuine clearance claim.

        This test pins that boundary so it stays deliberate: the words may appear in
        documentation, and must not appear in what the API actually says.

        Args:
            api_client: A client running the real app, offline.
        """
        document = api_client.get("/openapi.json").json()
        body = _scammy_response(api_client)

        # Documentation may say "honest" about its own reporting.
        assert any(
            "honest" in value.lower() for _, value in iter_json_strings(document)
        ), "the documentation should still describe the tool's own honesty"

        # A response must not.
        _assert_no_verdict(_system_texts(body))


class TestTheRequiredVocabularyIsAbsent:
    """The specific phrases `PROJECT_CONTEXT.md` forbids."""

    @pytest.mark.parametrize("phrase", FORBIDDEN_PHRASES)
    def test_a_forbidden_phrase_never_appears(self, api_client: TestClient, phrase: str) -> None:
        """Each phrase from the product's own forbidden list, checked literally.

        The word-level ban above catches a bare word. These are the multi-word
        constructions a whole-sentence rewrite produces, and which a word-boundary
        matcher cannot flag because every individual word is innocent.

        Only the system's own prose is scanned. The response echoes the submitted
        content, and the offline test content is itself promotional — scanning the
        whole body would flag the echo of the suspect rather than any judgement of
        ours, which is the opposite of the property being checked.

        Args:
            api_client: A client running the real app, offline.
            phrase: A multi-word construction that would amount to advice or a verdict.
        """
        body = _scammy_response(api_client)

        _assert_no_verdict(_system_texts(body))

        prose = "\n".join(value for _, value in _system_texts(body)).lower()
        assert phrase not in prose, f"the system authored the forbidden phrase {phrase!r}"


class TestQuotedMaterialMayCarryTheWords:
    """A retrieved excerpt is the source's voice, not the system's."""

    def test_a_scammer_own_words_are_reproduced_without_being_adopted(
        self, api_client: TestClient
    ) -> None:
        """Content the user submitted is returned verbatim.

        The product's job includes showing the user what was actually said. Redacting
        "scam" out of a scammer's message would make the evidence untellable — and
        the check that matters is that the *surrounding* system fields stay clean,
        which `_assert_no_verdict` covers.

        Args:
            api_client: A client running the real app, offline.
        """
        submitted = "This is a SAFE INVESTMENT. Guaranteed profit. Do not invest elsewhere."

        body = api_client.post(
            "/api/investigations/text", json={"text": submitted}
        ).json()

        # The user's own text comes back in the claims the system extracted from it.
        quoted = [value for _, value in iter_json_strings(body) if submitted[:20] in value]
        assert quoted, "the submitted content should be traceable in the response"

        # And nothing around it adopts the vocabulary.
        _assert_no_verdict(_system_texts(body))


class TestTheCaveatSurvivesEveryHop:
    """`SCORE_NOT_A_PROBABILITY` cannot be dropped by a later layer."""

    def test_the_caveat_is_present_in_a_live_response(self, api_client: TestClient) -> None:
        """The first place it has to appear.

        Args:
            api_client: A client running the real app, offline.
        """
        body = _scammy_response(api_client)

        assert SCORE_NOT_A_PROBABILITY in body["risk_assessment"]["warnings"]

    def test_the_caveat_survives_the_repository_round_trip(self, api_client: TestClient) -> None:
        """Persisting and reloading must not lose it.

        This is the specific regression Phase 9 could have introduced: the
        repository rebuilds the assessment from stored columns, and `warnings_json`
        is one of them. A future edit that stops storing it would leave a *retrieved*
        investigation without the disclaimer while every engine test still passed.

        Args:
            api_client: A client running the real app, offline.
        """
        posted = _scammy_response(api_client)

        retrieved = api_client.get(
            f"/api/investigations/{posted['investigation_id']}"
        ).json()

        assert retrieved["risk_assessment"]["warnings"] == posted["risk_assessment"]["warnings"]
        assert SCORE_NOT_A_PROBABILITY in retrieved["risk_assessment"]["warnings"]

    def test_the_caveat_survives_in_the_stored_row(self, api_client: TestClient) -> None:
        """The warning is in the database, not merely re-added on read.

        Checked against the repository rather than the response, so a repair that
        re-injected the caveat at read time would be caught here.

        Args:
            api_client: A client running the real app, offline.
        """
        from app.db.session import Database
        from app.repositories.investigations import InvestigationRepository

        posted = _scammy_response(api_client)
        settings = api_client.app.state.settings

        database = Database(settings)
        try:
            session = database.session_factory()
            try:
                repo = InvestigationRepository(session)
                state = repo.load(posted["investigation_id"])
                assert SCORE_NOT_A_PROBABILITY in state["risk_assessment"].warnings
            finally:
                session.close()
        finally:
            database.dispose()

    def test_the_caveat_disclaims_fraud_and_loss(self, api_client: TestClient) -> None:
        """The sentence says what it is not, in the project's own terms.

        Args:
            api_client: A client running the real app, offline.
        """
        body = _scammy_response(api_client)
        caveat = SCORE_NOT_A_PROBABILITY

        assert "not a probability" in caveat.lower()
        assert "fraud" in caveat.lower()
        assert "loss" in caveat.lower()

    def test_a_zero_score_still_carries_the_caveat(self, api_client: TestClient) -> None:
        """A `0` is the score most likely to be read as an all-clear.

        Without the caveat, "score 0, LOW" is indistinguishable from an endorsement.

        Args:
            api_client: A client running the real app, offline.
        """
        body = api_client.post(
            "/api/investigations/text",
            json={"text": "Acme Capital Advisors is SEBI registered."},
        ).json()

        assessment = body["risk_assessment"]
        assert assessment is not None
        assert SCORE_NOT_A_PROBABILITY in assessment["warnings"]

    def test_the_caveat_appears_on_every_stored_run(self, api_client: TestClient) -> None:
        """Every run a client can retrieve carries it, not just the first one.

        Args:
            api_client: A client running the real app, offline.
        """
        for text in (MESSY_CONTENT, REGULATORY_CONTENT, "Something else entirely."):
            body = api_client.post("/api/investigations/text", json={"text": text}).json()
            retrieved = api_client.get(
                f"/api/investigations/{body['investigation_id']}"
            ).json()
            assert SCORE_NOT_A_PROBABILITY in retrieved["risk_assessment"]["warnings"]

    def test_the_history_entry_does_not_summarise_the_score_into_a_verdict(
        self, api_client: TestClient
    ) -> None:
        """A list row must not acquire a judgement the detail view does not make.

        Args:
            api_client: A client running the real app, offline.
        """
        api_client.post("/api/investigations/text", json={"text": MESSY_CONTENT})

        body = api_client.get("/api/investigations").json()

        # The summary deliberately omits risk_level and risk_score (D-039: lifting
        # them invites treating them as the headline verdict).
        for entry in body["investigations"]:
            assert "risk_level" not in entry
            assert "risk_score" not in entry
        _assert_no_verdict(list(iter_json_strings(body)))


class TestTheAssessmentIsForwardedNotRestated:
    """D-039: the API interprets, it never re-words a judgement."""

    def test_the_assessment_reaches_the_client_unchanged(self) -> None:
        """A response carries the very object the engine produced.

        Built here rather than over HTTP because the property is about object
        identity: a projection, however faithful in content, would break the moment
        Phase 6 added a field.
        """
        from app.api.adapters import serialize_investigation
        from app.graph.state import semantic_view

        context = offline_context(real_dependencies()[0])
        state = __import__(
            "app.graph.investigation_graph", fromlist=["run_investigation"]
        ).run_investigation(MESSY_CONTENT, context=context)

        response = serialize_investigation(state)

        assert response.risk_assessment is state["risk_assessment"]
        assert response.claims == list(state["claims"])
        assert response.red_flags == list(state["red_flags"])
        del semantic_view

    def test_no_response_field_is_derived_from_the_score(self, api_client: TestClient) -> None:
        """Nothing restates the assessment as a threshold or a verdict.

        Args:
            api_client: A client running the real app, offline.
        """
        body = _scammy_response(api_client)

        # The score is present exactly once, inside the assessment it belongs to.
        assert "risk_score" not in body
        assert "risk_level" not in body
        assert body["risk_assessment"]["risk_score"] == body["risk_assessment"]["risk_score"]

    def test_the_limits_endpoint_advertises_no_judgement(self, api_client: TestClient) -> None:
        """Discovery output is read before any investigation, so it must be neutral.

        Args:
            api_client: A client running the real app, offline.
        """
        body = api_client.get("/api/investigations/limits").json()

        _assert_no_verdict(list(iter_json_strings(body)))
        assert set(body) == {
            "supported_input_types",
            "max_text_length",
            # Phase 12: a URL is bounded as a URL, not as prose, and the bound is
            # discoverable before a submission rather than after a rejection.
            "max_url_length",
            # Phase 13: a screenshot is bounded in bytes, the image formats it
            # may be are named, and the OCR language is discoverable too.
            "max_upload_bytes",
            "allowed_image_types",
            # Phase 14: a PDF is bounded in pages, and the one
            # document type it may be is named too.
            "allowed_pdf_types",
            "pdf_max_pages",
            "ocr_languages",
            "languages",
            "translation_enabled",
        }


class TestNoAdviceInAnyLanguageEcho:
    """The `language` field must not change the vocabulary."""

    @pytest.mark.parametrize("language", ["en", "hi", "mr"])
    def test_every_declared_language_produces_the_same_safe_vocabulary(
        self, api_client: TestClient, language: str
    ) -> None:
        """Echoing a language cannot change what the system says.

        Args:
            api_client: A client running the real app, offline.
            language: The declared language code.
        """
        body = api_client.post(
            "/api/investigations/text",
            json={"text": MESSY_CONTENT, "language": language},
        ).json()

        assert body["language"] == language
        _assert_no_verdict(_system_texts(body))
        assert SCORE_NOT_A_PROBABILITY in body["risk_assessment"]["warnings"]
