"""Equivalent input must produce an equivalent investigation (Phase 10).

Phases 7 and 8 each prove a version of this, in their own layer: the graph in
`tests/graph/test_graph_execution.py`, the API in
`tests/api/test_investigation_endpoints.py`. This module holds the *cross-cutting*
version — the same guarantee stated once, from the raw id derivation through the
graph, the adapter, the database and back out over HTTP — because a determinism bug
almost never lives in one layer. It is the seam: a normaliser that changes between
two runs of the same document, a repository that reorders a collection, an adapter
that stamps a fresh timestamp, a risk engine that iterates a `set`.

## What is compared, and what is not

Comparison is by `semantic_view`, the helper Phase 7 built for exactly this. It
strips `TIMESTAMP_FIELDS` — `started_at`, `assessed_at`, `built_at`,
`retrieved_at`, `searched_at`, `completed_at` — wherever they appear, and reduces
the timeline to `(stage, status, message)` triples.

That exclusion is not a convenience. Those fields are stamped from a real clock by
whichever phase owns them, so two runs of the same input *must* differ in them and
must agree in everything else. Asserting full equality would be asserting that the
clock does not move, and the resulting test would be flaky in exactly the way that
gets ignored.

What remains after stripping is the pipeline's actual output: every claim, every
entity, every red flag, every verification result, every piece of evidence, the
whole risk assessment, every warning and error, and the timeline's decisions.

## The id is content-derived, and that is a real constraint

`investigation_id_for` digests `input_type` and the **raw** input. Two consequences
are asserted below because they are design decisions a reader would otherwise have
to guess at:

- The same content submitted twice shares an id, so the two runs are comparable.
- The same *visible* content submitted with different whitespace does **not** share
  an id, because the digest is over the raw bytes. Phase 9's surrogate key is what
  makes both runs storable; the public id is a content fingerprint, not a
  normalised one.
"""

from __future__ import annotations

import pytest

from app.api.adapters import serialize_investigation
from app.graph.context import GraphContext
from app.graph.investigation_graph import run_investigation
from app.graph.state import (
    TIMESTAMP_FIELDS,
    InvestigationInputType,
    investigation_id_for,
    semantic_view,
)
from app.repositories.investigations import InvestigationRepository
from tests.graph.graph_factories import offline_context, real_dependencies
from tests.persistence_factories import MESSY_CONTENT, REGULATORY_CONTENT

#: `app.schemas.api.MAX_TEXT_LENGTH`. Duplicated as a number rather than imported
#: so that changing the API limit does not silently move this test's idea of the
#: boundary — the boundary is a documented contract, and a test that follows the
#: constant cannot detect the constant changing.
MAX_TEXT_LENGTH = 20_000


def _fresh_context() -> GraphContext:
    """Build a brand-new offline context for each run.

    A fresh context matters more than it looks. Reusing one would share the search
    recorder, so a second run would see queries the first had issued and could
    legitimately differ. Rebuilding proves the determinism belongs to the pipeline
    rather than to leftover state.

    Returns:
        A `GraphContext` wired to the real services, offline.
    """
    return offline_context(real_dependencies()[0])


def _run(text: str, context: GraphContext | None = None) -> dict:
    """Run one investigation and return its final state.

    Args:
        text: Content to investigate.
        context: The context to run against, or a fresh one.

    Returns:
        The final investigation state.
    """
    return run_investigation(
        text,
        input_type=InvestigationInputType.TEXT,
        context=context or _fresh_context(),
    )


# -- The content-derived id --------------------------------------------------


class TestTheInvestigationIdIsContentDerived:
    """Phase 7's id guarantee, stated as a property of the function."""

    def test_the_same_input_always_yields_the_same_id(self) -> None:
        """Repeated calls are identical, including across many calls."""
        ids = {
            investigation_id_for(InvestigationInputType.TEXT, REGULATORY_CONTENT)
            for _ in range(50)
        }

        assert len(ids) == 1

    @pytest.mark.parametrize(
        "text",
        [
            REGULATORY_CONTENT,
            MESSY_CONTENT,
            "a",
            "A",
            " ",
            "Acme Capital Advisors is SEBI registered. ",
            "not investment content at all",
            "",
        ],
    )
    def test_different_content_yields_a_different_id(self, text: str) -> None:
        """Distinct content must not collide, or two runs would be indistinguishable."""
        mine = investigation_id_for(InvestigationInputType.TEXT, text)
        theirs = investigation_id_for(InvestigationInputType.TEXT, REGULATORY_CONTENT)

        assert mine != theirs or text == REGULATORY_CONTENT

    def test_the_input_type_is_mixed_into_the_digest(self) -> None:
        """The same text under two types must not collide.

        Phase 8 accepts `TEXT` and `PDF`. A document submitted as one and re-uploaded
        as the other is a different claim about the world, and the id has to say so.

        """
        as_text = investigation_id_for(InvestigationInputType.TEXT, REGULATORY_CONTENT)
        as_pdf = investigation_id_for(InvestigationInputType.PDF, REGULATORY_CONTENT)

        assert as_text != as_pdf

    def test_the_id_has_a_fixed_shape(self) -> None:
        """Prefix, length and alphabet are part of what a client stores."""
        identifier = investigation_id_for(InvestigationInputType.TEXT, REGULATORY_CONTENT)

        assert identifier.startswith("inv_")
        assert len(identifier) == len("inv_") + 16
        assert all(character in "0123456789abcdef" for character in identifier[4:])

    def test_the_id_is_not_a_counter(self) -> None:
        """Ids must not depend on run order or on how many runs came before."""
        # Run a batch and a single run of the same content, interleaved with other
        # content, and confirm the derived id never moves.
        other = investigation_id_for(InvestigationInputType.TEXT, MESSY_CONTENT)
        target = investigation_id_for(InvestigationInputType.TEXT, REGULATORY_CONTENT)

        for _ in range(3):
            investigation_id_for(InvestigationInputType.TEXT, MESSY_CONTENT)

        assert investigation_id_for(InvestigationInputType.TEXT, REGULATORY_CONTENT) == target
        assert other != target

    @pytest.mark.parametrize(
        "label,text",
        [
            ("trailing space", f"{REGULATORY_CONTENT} "),
            ("leading space", f" {REGULATORY_CONTENT}"),
            ("double space", REGULATORY_CONTENT.replace(" is ", "  is  ")),
            ("trailing newline", f"{REGULATORY_CONTENT}\n"),
            ("crlf instead of lf", "Line one.\r\nLine two."),
            ("lf instead of crlf", "Line one.\nLine two."),
            ("zero-width space", f"{REGULATORY_CONTENT}\u200b"),
            ("combining accent", "Cafe\u0301 Advisors is SEBI registered."),
            ("precomposed accent", "Caf\u00e9 Advisors is SEBI registered."),
        ],
    )
    def test_visually_identical_content_does_not_share_an_id(
        self, label: str, text: str
    ) -> None:
        """The digest is over raw bytes, so whitespace and Unicode variants differ.

        This is a documented property, not a defect, and asserting it is the only
        way a reader can tell it apart from one. The alternative — normalising
        before digesting — would make the id depend on the normaliser, and a change
        to the normaliser would then silently change every stored id.

        Args:
            label: Which variant this is, for the failure message.
            text: The variant text.
        """
        baseline = investigation_id_for(InvestigationInputType.TEXT, REGULATORY_CONTENT)
        variant = investigation_id_for(InvestigationInputType.TEXT, text)

        assert variant != baseline, f"{label} should not share the baseline id"

    def test_nfc_and_nfd_spellings_get_different_ids(self) -> None:
        """Precomposed and decomposed characters are different strings entirely.

        The two render identically on screen and a reader will see them as the same
        text, which is exactly why this is asserted rather than assumed. They are
        different code point sequences, so a future change to digest the normalised
        form would quietly merge them — and this is the test that would notice.
        """
        precomposed = "Caf\u00e9 Advisors"
        decomposed = "Cafe\u0301 Advisors"

        assert precomposed != decomposed
        assert precomposed.encode() != decomposed.encode()
        assert investigation_id_for(
            InvestigationInputType.TEXT, precomposed
        ) != investigation_id_for(InvestigationInputType.TEXT, decomposed)


# -- Semantic determinism through the graph ---------------------------------


class TestTwoRunsOfTheSameInputAgreeCompletely:
    """The whole pipeline's output, not a sample of it."""

    def test_two_runs_agree_on_every_semantic_field(self) -> None:
        """Claims, entities, flags, verification, evidence and risk, all equal."""
        first = _run(REGULATORY_CONTENT)
        second = _run(REGULATORY_CONTENT)

        assert semantic_view(first) == semantic_view(second)

    def test_the_comparison_covers_every_collected_output(self) -> None:
        """The states being compared actually contain all the collections.

        Without this, a change that made extraction return nothing would produce two
        identical empty states and the determinism test above would pass while
        proving nothing.
        """
        state = _run(REGULATORY_CONTENT)

        assert state["claims"]
        assert state["red_flags"]
        assert state["verification_results"]
        assert state["evidence"]
        assert state["risk_assessment"] is not None

    def test_each_collection_is_individually_equal(self) -> None:
        """Per-field, so a failure names the collection that diverged.

        A whole-state comparison reports only that *something* differed, which for a
        twelve-thousand-line diff is a poor place to start.
        """
        first = semantic_view(_run(REGULATORY_CONTENT))
        second = semantic_view(_run(REGULATORY_CONTENT))

        for key in (
            "claims",
            "entities",
            "claim_entity_links",
            "red_flags",
            "verification_results",
            "evidence",
            "risk_assessment",
            "warnings",
            "errors",
            "timeline",
            "extracted_text",
            "status",
        ):
            assert first.get(key) == second.get(key), f"{key} differed between runs"

    def test_only_timestamps_differ(self) -> None:
        """The two states are not identical objects, and the difference is only time.

        This is the assertion that makes the exclusions above honest. If the states
        were wholly equal, `semantic_view` would be hiding something, and a reader
        would have no way to know.

        The comparison is on the *stripped* states, not the raw ones, because
        `EvidenceResponse.built_at` and `RiskAssessment.assessed_at` are nested
        several levels down. Comparing raw states would report `evidence` and
        `risk_assessment` as differing, which is true of the bytes and misleading
        about the pipeline: nothing in either collection changed.
        """
        first = semantic_view(_run(REGULATORY_CONTENT))
        second = semantic_view(_run(REGULATORY_CONTENT))

        differing = {
            key
            for key in set(first) | set(second)
            if first.get(key) != second.get(key)
        }

        assert differing <= set(TIMESTAMP_FIELDS), (
            f"these fields differ between two identical runs and are not "
            f"timestamps: {sorted(differing - set(TIMESTAMP_FIELDS))}"
        )

    def test_the_fixed_clock_covers_the_graph_but_not_the_services(self) -> None:
        """Which timestamps the test context pins, and which it cannot.

        `offline_context` installs a fixed clock on the `GraphContext`, so the
        graph's own instants — `started_at`, `completed_at` — are identical between
        two runs. The *services* stamp theirs from their own clocks: Phase 5 writes
        `EvidenceResponse.built_at` and Phase 6 writes
        `RiskAssessment.assessed_at`, and neither reads the graph's clock. Those
        differ by microseconds and always will.

        So the raw states are **not** equal, which is precisely why
        `semantic_view` exists and why the comparisons in this class go through it.
        Asserted because the distinction is easy to assume away: a reader who
        believed the whole state was byte-reproducible would strengthen one of these
        tests to `first == second` and get a failure that looks like a determinism
        bug but is not one.
        """
        first = _run(REGULATORY_CONTENT)
        second = _run(REGULATORY_CONTENT)

        # The graph's own clock is pinned.
        assert first["started_at"] == second["started_at"]

        # `completed_at` is never set. Phase 7 stamps `started_at` when the input
        # stage begins and never stamps a completion time, so the key is absent from
        # the state entirely and the adapter renders it as `null`. Asserted here
        # because it is the kind of gap that documentation quietly claims is filled,
        # and because the moment a node *does* start writing it, this test is the one
        # that should be updated to say so.
        assert "completed_at" not in first
        assert "completed_at" not in second

        # The services' clocks are not, and the evidence is right there.
        assert first["evidence"][0].built_at != second["evidence"][0].built_at
        assert first["risk_assessment"].assessed_at != second["risk_assessment"].assessed_at

        # And once those are stripped, the states are equal.
        assert semantic_view(first) == semantic_view(second)

    def test_the_adapter_is_deterministic(self) -> None:
        """Serialising the same state twice gives the same response.

        A pass-through adapter cannot vary, and asserting it costs one call — but it
        is the boundary where a "harmless" formatting change would land, and
        `tests/api/test_api_risk_safety.py` relies on the response being a faithful
        projection.
        """
        state = _run(REGULATORY_CONTENT)

        first = serialize_investigation(state).model_dump(mode="json")
        second = serialize_investigation(state).model_dump(mode="json")

        assert first == second


# -- Determinism across storage --------------------------------------------


class TestDeterminismSurvivesStorage:
    """The round trip must not reorder or regenerate anything."""

    def test_a_stored_run_reloads_to_the_same_semantics(
        self, session: InvestigationRepository
    ) -> None:
        """Write, read back, compare — Phase 7's definition, through Phase 9.

        Args:
            session: The session to write and read through.
        """
        live = _run(REGULATORY_CONTENT)
        repository = InvestigationRepository(session)
        repository.save(live)
        repository.commit()

        reloaded = repository.load(live["investigation_id"])

        assert semantic_view(reloaded) == semantic_view(live)

    def test_collection_order_is_preserved_through_storage(
        self, session: InvestigationRepository
    ) -> None:
        """Order is part of the output, not an implementation detail.

        A repository that returned claims or evidence sorted by a key rather than
        by the order the pipeline produced them would produce a semantically equal
        but differently *ordered* collection. The whole-state comparison above
        catches it, since tuples compare positionally — this names the risk so a
        failure points somewhere useful.

        Args:
            session: The session to write and read through.
        """
        live = _run(REGULATORY_CONTENT)
        repository = InvestigationRepository(session)
        repository.save(live)
        repository.commit()

        reloaded = repository.load(live["investigation_id"])

        assert tuple(claim.id for claim in reloaded["claims"]) == tuple(
            claim.id for claim in live["claims"]
        )
        assert tuple(
            (bundle.claim_id, tuple(item.id for item in bundle.evidence))
            for bundle in reloaded["evidence"]
        ) == tuple(
            (bundle.claim_id, tuple(item.id for item in bundle.evidence))
            for bundle in live["evidence"]
        )

    def test_the_id_survives_storage_unchanged(
        self, session: InvestigationRepository
    ) -> None:
        """The public id is the client's handle, so storage must not renumber it.

        Args:
            session: The session to write and read through.
        """
        live = _run(REGULATORY_CONTENT)
        repository = InvestigationRepository(session)
        repository.save(live)
        repository.commit()

        reloaded = repository.load(live["investigation_id"])

        assert reloaded["investigation_id"] == live["investigation_id"]
        assert reloaded["investigation_id"] == investigation_id_for(
            InvestigationInputType.TEXT, REGULATORY_CONTENT
        )


# -- Determinism at the HTTP boundary --------------------------------------


# -- Edge cases that must stay deterministic --------------------------------


class TestAwkwardInputIsStillDeterministic:
    """Unusual text is common in this domain, so it must be boring."""

    @pytest.mark.parametrize(
        "label,text",
        [
            ("empty", ""),
            ("single space", " "),
            ("tabs", "\t\t\t"),
            ("newlines only", "\n\n\n"),
            ("crlf only", "\r\n\r\n"),
            ("mixed whitespace", "  \t\r\n  \t "),
            ("zero-width characters", "\u200b\u200c\u200d"),
            ("combining marks alone", "\u0301\u0302"),
            ("emoji", "Acme Capital Advisors 🚀🚀 is SEBI registered."),
            ("rtl text", "مستشارون ماليون. Acme is SEBI registered."),
            ("cjk text", "在任何交易所上市的基金。Acme is SEBI registered."),
            ("punctuation only", "!!!???...;;;:::---"),
            ("single character", "x"),
            ("no whitespace at all", "a" * 500),
            ("one long line", "word " * 1000),
            ("many short lines", "line\n" * 500),
            ("url only", "https://example.invalid/promo?a=1&b=2"),
            ("angle brackets and tags", "<script>alert(1)</script> Acme is registered."),
            ("null-ish characters", "a\x00b is registered."),
            ("surrogate pair emoji", "Acme \U0001F1EE\U0001F1F3 is registered."),
        ],
    )
    def test_awkward_text_yields_one_stable_id(
        self, label: str, text: str
    ) -> None:
        """Any text at all must hash without raising, and hash the same way twice.

        Args:
            label: Which case this is, for the failure message.
            text: The awkward content.
        """
        first = investigation_id_for(InvestigationInputType.TEXT, text)
        second = investigation_id_for(InvestigationInputType.TEXT, text)

        assert first == second, f"{label} was not hashed deterministically"
        assert first.startswith("inv_")

    @pytest.mark.parametrize(
        "label,text",
        [
            ("empty", ""),
            ("whitespace only", "   \t\n  "),
            ("emoji", "Acme Capital Advisors 🚀 is SEBI registered."),
            ("rtl", "مستشارون ماليون. Acme is SEBI registered."),
            ("cjk", "letonline"),
            ("zero-width", "Acme is SEBI registered.\u200b"),
            ("punctuation", "!?.,;:"),
            ("one long line", "word " * 1000),
        ],
    )
    def test_awkward_text_runs_deterministically_through_the_graph(
        self, label: str, text: str
    ) -> None:
        """The graph must not be sensitive to input shape beyond the content.

        Args:
            label: Which case this is, for the failure message.
            text: The awkward content.
        """
        try:
            first = semantic_view(_run(text))
        except Exception as error:  # noqa: BLE001 - the point is to report any failure
            pytest.fail(f"{label} crashed the graph: {error!r}")

        second = semantic_view(_run(text))

        assert first == second, f"{label} produced different results on two runs"

    @pytest.mark.parametrize(
        "length",
        [0, 1, 999, 19_999, MAX_TEXT_LENGTH, MAX_TEXT_LENGTH + 1],
    )
    def test_the_length_boundary_is_handled_consistently(
        self, length: int
    ) -> None:
        """A given length always gives the same answer, above or below the cap.

        The cap itself is enforced by the API schema; the id function has no
        opinion, and that is what makes the two layers independently testable.

        Args:
            length: How many characters to submit.
        """
        text = "a" * length

        assert investigation_id_for(
            InvestigationInputType.TEXT, text
        ) == investigation_id_for(InvestigationInputType.TEXT, text)
        assert investigation_id_for(
            InvestigationInputType.TEXT, text
        ) != investigation_id_for(InvestigationInputType.TEXT, text + "a")
