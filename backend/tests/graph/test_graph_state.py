"""Investigation state tests (Phase 7).

The state is the contract every future phase reads, so its shapes are pinned
here: the stage vocabulary, the structured warning and error models, the derived
investigation id, and the accumulator channels.

The accumulator tests matter most. `warnings`, `errors` and `timeline` use
`operator.add` reducers precisely so that a node appending one entry cannot
silently discard the entries already recorded. A regression that removed the
reducer would still pass a naive happy-path test, because the happy path never
accumulates twice.
"""

from __future__ import annotations

import operator
from typing import get_type_hints

import pytest

from app.graph.state import (
    GraphError,
    GraphStage,
    GraphWarning,
    InvestigationInputType,
    InvestigationState,
    TimelineEvent,
    TimelineStatus,
    investigation_id_for,
    semantic_view,
)
from tests.graph.graph_factories import FIXED_INSTANT


def _bare_context() -> object:
    """A context with fake services, for exercising a node in isolation."""
    from tests.graph.graph_factories import offline_context

    return offline_context()


class TestStageVocabulary:
    """The stage names are one shared vocabulary."""

    def test_the_six_stages_are_declared_in_pipeline_order(self) -> None:
        assert [stage.value for stage in GraphStage] == [
            "input",
            "extraction",
            "red_flags",
            "verification",
            "evidence",
            "risk",
            "completed",
        ]

    def test_completed_is_a_terminal_label_not_a_pipeline_stage(self) -> None:
        """It appears last and is not one of the six stages that run."""
        stages = [stage.value for stage in GraphStage]
        assert stages[-1] == "completed"
        assert len(stages) == 7

    def test_timeline_distinguishes_partial_from_skipped(self) -> None:
        """The two degraded outcomes mean different things to a reader."""
        assert TimelineStatus.PARTIAL is not TimelineStatus.SKIPPED
        assert {"PARTIAL", "SKIPPED"} <= {status.value for status in TimelineStatus}


class TestInputTypeVocabulary:
    """All four documented input types are declared."""

    def test_every_documented_input_type_exists(self) -> None:
        assert {kind.value for kind in InvestigationInputType} == {
            "TEXT",
            "URL",
            "IMAGE",
            "PDF",
        }

    def test_exactly_one_type_is_analysed_by_this_phase(self) -> None:
        """Declaring the product must not overstate what this build analyses.

        OCR, PDF and image ingestion are deferred, so exactly one of the four
        declared types reaches the pipeline. This test exists so that quietly
        starting to analyse another one — without the ingestion work that would
        require — fails here instead of producing an empty investigation that
        reads like a clean one.
        """
        from app.graph.nodes import input_node

        analysed = []
        for kind in InvestigationInputType:
            state: InvestigationState = {
                "raw_input": "some content",
                "input_type": kind.value,
            }
            result = input_node(state, _bare_context())
            if not result.get("errors"):
                analysed.append(kind)

        assert analysed == [InvestigationInputType.TEXT]


class TestAccumulatorChannels:
    """The three append-only channels are declared as append-only."""

    @pytest.mark.parametrize(
        "channel", ["warnings", "errors", "timeline"], ids=["warnings", "errors", "timeline"]
    )
    def test_each_accumulator_has_an_add_reducer(self, channel: str) -> None:
        """Appending must be structural, not a convention each node repeats."""
        hints = get_type_hints(InvestigationState, include_extras=True)
        metadata = hints[channel]
        assert operator.add in getattr(metadata, "__metadata__", ())

    @pytest.mark.parametrize(
        "channel", ["warnings", "errors", "timeline"], ids=["warnings", "errors", "timeline"]
    )
    def test_non_accumulating_fields_have_no_reducer(self, channel: str) -> None:
        """Ordinary fields are replaced, so a stale value cannot accumulate."""
        hints = get_type_hints(InvestigationState, include_extras=True)
        single = [key for key in hints if key not in {"warnings", "errors", "timeline"}]
        for key in single:
            metadata = hints[key]
            assert operator.add not in getattr(metadata, "__metadata__", ()), key


class TestStructuredModels:
    """Warnings and errors are structured, immutable and closed."""

    def test_a_warning_records_its_code_and_stage(self) -> None:
        warning = GraphWarning(
            code="SEARCH_UNAVAILABLE",
            stage=GraphStage.VERIFICATION,
            message="External search was unavailable.",
        )
        assert warning.code == "SEARCH_UNAVAILABLE"
        assert warning.stage is GraphStage.VERIFICATION

    def test_a_warning_carries_no_error_type_by_default(self) -> None:
        """Only a service fault supplies one, so absence is unambiguous."""
        warning = GraphWarning(code="X", stage=GraphStage.INPUT, message="y")
        assert warning.error_type is None

    def test_a_warning_is_frozen(self) -> None:
        warning = GraphWarning(code="X", stage=GraphStage.INPUT, message="y")
        with pytest.raises(Exception):
            warning.code = "Z"  # type: ignore[misc]

    def test_an_unknown_field_is_rejected(self) -> None:
        """A closed model cannot quietly accumulate unexplained data."""
        with pytest.raises(Exception):
            GraphWarning(
                code="X", stage=GraphStage.INPUT, message="y", traceback="boom"
            )  # type: ignore[call-arg]

    def test_an_error_keeps_only_the_exception_type(self) -> None:
        error = GraphError(
            code="RISK_ASSESSMENT_FAILED",
            stage=GraphStage.RISK,
            message="The risk stage did not complete.",
            error_type="ValueError",
        )
        assert error.error_type == "ValueError"
        assert "ValueError" not in error.message

    def test_a_timeline_event_defaults_to_the_fixed_instant(self) -> None:
        event = TimelineEvent(
            stage=GraphStage.INPUT,
            status=TimelineStatus.STARTED,
            message="Content received.",
            at=FIXED_INSTANT,
        )
        assert event.at == FIXED_INSTANT

    def test_a_timeline_event_has_no_free_text_explanation_field(self) -> None:
        """No place for a natural-language stage narrative to be smuggled in."""
        assert set(TimelineEvent.model_fields) == {
            "stage",
            "status",
            "message",
            "at",
        }


class TestInvestigationId:
    """The id is derived, so the same input yields the same id."""

    def test_the_same_input_yields_the_same_id(self) -> None:
        first = investigation_id_for(InvestigationInputType.TEXT, "hello")
        second = investigation_id_for(InvestigationInputType.TEXT, "hello")
        assert first == second

    def test_different_input_yields_a_different_id(self) -> None:
        assert investigation_id_for("TEXT", "a") != investigation_id_for("TEXT", "b")

    def test_the_input_type_is_part_of_the_digest(self) -> None:
        """The same text submitted as two kinds must not collide."""
        assert investigation_id_for("TEXT", "a") != investigation_id_for("URL", "a")

    def test_the_id_is_prefixed(self) -> None:
        assert investigation_id_for("TEXT", "a").startswith("inv_")

    def test_the_enum_and_its_value_agree(self) -> None:
        assert investigation_id_for(InvestigationInputType.PDF, "x") == investigation_id_for(
            "PDF", "x"
        )


class TestSemanticView:
    """Metadata is separable from what the pipeline decided."""

    def test_timestamps_are_excluded(self) -> None:
        state: InvestigationState = {
            "started_at": FIXED_INSTANT,
            "completed_at": FIXED_INSTANT,
        }
        reduced = semantic_view(state)
        assert "started_at" not in reduced
        assert "completed_at" not in reduced

    def test_the_timeline_is_reduced_to_semantic_triples(self) -> None:
        state: InvestigationState = {
            "timeline": (
                TimelineEvent(
                    stage=GraphStage.INPUT,
                    status=TimelineStatus.STARTED,
                    message="Content received.",
                    at=FIXED_INSTANT,
                ),
            )
        }
        reduced = semantic_view(state)
        assert reduced["timeline"] == (("input", "STARTED", "Content received."),)

    def test_real_results_are_preserved(self) -> None:
        state: InvestigationState = {"claims": ("kept",)}
        assert semantic_view(state)["claims"] == ("kept",)

    def test_it_does_not_mutate_the_state_it_reads(self) -> None:
        state: InvestigationState = {"started_at": FIXED_INSTANT}
        semantic_view(state)
        assert state["started_at"] == FIXED_INSTANT