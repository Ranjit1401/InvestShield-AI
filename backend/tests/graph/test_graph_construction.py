"""Graph construction and wiring tests (Phase 7).

Structure is asserted before behaviour, because a graph that is wired wrongly
can still produce a plausible-looking state: a stage silently dropped, an edge
pointing the wrong way, or two stages running in an order that happens to work on
the fixture at hand. Pinning the shape means a later behavioural test failure is
about logic rather than plumbing.
"""

from __future__ import annotations

from langgraph.graph import END, START

from app.graph import (
    ABORT,
    CONTINUE,
    NODE_NAMES,
    build_investigation_graph,
    route_on_recorded_error,
)
from app.graph.state import GraphStage, InvestigationState

EXPECTED_EDGES: frozenset[tuple[str, str]] = frozenset(
    {
        (START, "input"),
        ("input", "extraction"),
        ("extraction", "red_flags"),
        ("red_flags", "verification"),
        ("verification", "evidence"),
        ("evidence", "risk"),
        ("risk", END),
    }
)


class TestGraphCompiles:
    """The factory produces a usable compiled graph."""

    def test_the_graph_compiles(self) -> None:
        """A compiled graph is the precondition for every other test here."""
        assert build_investigation_graph() is not None

    def test_every_stage_is_registered(self) -> None:
        """All six stages exist as nodes."""
        nodes = set(build_investigation_graph().get_graph().nodes)
        assert set(NODE_NAMES) <= nodes

    def test_the_stage_list_matches_the_stage_enum(self) -> None:
        """`NODE_NAMES` cannot drift from the vocabulary the state publishes."""
        assert set(NODE_NAMES) == {stage.value for stage in GraphStage} - {
            GraphStage.COMPLETED.value
        }

    def test_each_call_returns_a_separate_graph(self) -> None:
        """No shared mutable instance for one test's wiring to leak through."""
        first = build_investigation_graph()
        second = build_investigation_graph()
        assert first is not second

    def test_no_unexpected_nodes_are_present(self) -> None:
        """Only the six stages plus LangGraph's own bookends."""
        nodes = set(build_investigation_graph().get_graph().nodes)
        assert nodes == set(NODE_NAMES) | {START, END}


class TestGraphEdges:
    """The pipeline order is the documented one."""

    def test_the_pipeline_is_sequential(self) -> None:
        """Every consecutive stage is joined by an edge."""
        edges = {
            (edge.source, edge.target)
            for edge in build_investigation_graph().get_graph().edges
        }
        for source, target in EXPECTED_EDGES:
            assert (source, target) in edges

    def test_the_graph_never_skips_a_stage(self) -> None:
        """The only extra edges are the abort paths each stage can take."""
        edges = {
            (edge.source, edge.target)
            for edge in build_investigation_graph().get_graph().edges
        }
        assert edges - EXPECTED_EDGES == {
            ("input", END),
            ("extraction", END),
            ("red_flags", END),
            ("verification", END),
            ("evidence", END),
        }

    def test_every_stage_can_reach_the_end_on_failure(self) -> None:
        """A recorded error anywhere ends the run rather than continuing it."""
        edges = {
            (edge.source, edge.target)
            for edge in build_investigation_graph().get_graph().edges
        }
        for stage in NODE_NAMES:
            assert (stage, END) in edges

    def test_no_stage_can_jump_past_its_neighbour(self) -> None:
        """There is no shortcut from a stage to a later one."""
        edges = {
            (edge.source, edge.target)
            for edge in build_investigation_graph().get_graph().edges
        }
        order = {name: index for index, name in enumerate(NODE_NAMES)}
        for source, target in edges:
            if target in (END, START) or source == START:
                continue
            assert order[target] == order[source] + 1


class TestErrorRouting:
    """The predicate that guards every stage."""

    def test_a_recorded_error_aborts_the_run(self) -> None:
        state: InvestigationState = {"errors": ("anything",)}
        assert route_on_recorded_error(state) == ABORT

    def test_no_recorded_error_continues(self) -> None:
        state: InvestigationState = {}
        assert route_on_recorded_error(state) == CONTINUE

    def test_an_empty_error_tuple_is_not_an_error(self) -> None:
        """Accumulated-but-empty is the same as absent."""
        state: InvestigationState = {"errors": ()}
        assert route_on_recorded_error(state) == CONTINUE

    def test_a_warning_does_not_abort_the_run(self) -> None:
        """A limitation is exactly the thing that must not stop a pipeline."""
        state: InvestigationState = {"warnings": ("something degraded",)}
        assert route_on_recorded_error(state) == CONTINUE