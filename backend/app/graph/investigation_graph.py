"""Investigation graph factory and execution API (Phase 7).

This module owns two things and nothing else: how the graph is assembled, and how
a caller starts a run.

```
START ─► input ─┬─(error)─► END
                └─(ok)────► extraction ─┬─(error)─► END
                                        └─(ok)────► red_flags ─► … ─► risk ─► END
```

Every stage guards the same way: a stage that records a structured error ends the
run rather than letting the remaining stages proceed on incomplete input. This
matters most for the stages that feed the score — a red-flag pass that failed and
then reported "no patterns found", or a verification pass that failed and then
reported no contradictions, would both understate risk while looking complete.

The factory compiles a **fresh** graph on every call rather than exposing a
module-level compiled instance. A shared global would be a mutable object
reachable from every test and every request; rebuilding is cheap (six node
registrations) and removes the question of whether one test's wiring leaked into
another's.

`run_investigation` is the library entry point. It is deliberately a thin
wrapper: it seeds the two fields a caller always supplies, invokes the compiled
graph with the context, and returns the state. A future FastAPI route (Phase 8)
and a CLI script will both call it, so it must not grow request handling,
persistence or presentation — that is what the other phases are for.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.core.logging import get_logger
from app.graph.context import GraphContext, GraphDependencies, build_default_context
from app.graph.edges import ABORT, CONTINUE, route_on_recorded_error
from app.graph.nodes import (
    evidence_node,
    extraction_node,
    input_node,
    red_flags_node,
    risk_node,
    verification_node,
)
from app.graph.state import InvestigationInputType, InvestigationState, semantic_view

logger = get_logger(__name__)

__all__ = [
    "NODE_NAMES",
    "build_investigation_graph",
    "run_investigation",
]

#: The nodes registered on the graph, in execution order. Exported so tests and
#: the future timeline UI share one definition of "the stages".
NODE_NAMES: tuple[str, ...] = (
    "input",
    "extraction",
    "red_flags",
    "verification",
    "evidence",
    "risk",
)

#: The stages in execution order, each joined to the next.
_STAGE_CHAIN: tuple[tuple[str, str], ...] = tuple(
    zip(NODE_NAMES[:-1], NODE_NAMES[1:], strict=True)
)

#: Node to function, paired. Kept beside `NODE_NAMES` so a stage added to the
#: pipeline is added in one place.
_NODE_IMPLEMENTATIONS = (
    ("input", input_node),
    ("extraction", extraction_node),
    ("red_flags", red_flags_node),
    ("verification", verification_node),
    ("evidence", evidence_node),
    ("risk", risk_node),
)


def _as_graph_node(function: Any) -> Any:
    """Adapt a `(state, context)` node into LangGraph's `(state, runtime)` shape.

    Nodes are written as plain two-argument functions taking a `GraphContext`
    directly, which lets a test call one in isolation without constructing a
    LangGraph runtime. This adapter is the only place the two calling
    conventions meet.

    Args:
        function: A node taking `(state, context)` and returning a partial state.

    Returns:
        A callable LangGraph can register, reading the context from its runtime.
    """

    def wrapped(state: InvestigationState, runtime: Any) -> dict[str, Any]:
        """Invoke the node with the context LangGraph supplied."""
        return function(state, runtime.context)

    wrapped.__name__ = function.__name__
    wrapped.__doc__ = function.__doc__
    return wrapped


def build_investigation_graph() -> Any:
    """Build and compile the investigation graph.

    Registers the six stage nodes, wires them in pipeline order, and guards each
    stage so that a recorded error ends the run.

    The compiled graph takes its services from the runtime context rather than
    closing over them, so the same compiled structure serves both production and
    a fully injected test.

    Returns:
        A `CompiledStateGraph` whose `invoke` accepts a partial state and a
        `GraphContext`.
    """
    builder: StateGraph = StateGraph(
        InvestigationState, context_schema=GraphContext
    )

    for name, function in _NODE_IMPLEMENTATIONS:
        builder.add_node(name, _as_graph_node(function))

    builder.add_edge(START, "input")

    for source, target in _STAGE_CHAIN:
        builder.add_conditional_edges(
            source,
            route_on_recorded_error,
            {CONTINUE: target, ABORT: END},
        )
    builder.add_edge("risk", END)

    return builder.compile()


def run_investigation(
    raw_input: str,
    *,
    input_type: InvestigationInputType | str = InvestigationInputType.TEXT,
    context: GraphContext | None = None,
    dependencies: GraphDependencies | None = None,
) -> InvestigationState:
    """Run one investigation end to end.

    This is the library entry point for the pipeline. It is used by scripts,
    tests and, from Phase 8, the HTTP layer — which is why it accepts a
    prebuilt context: an API process injects the real services once, while tests
    inject fakes.

    Args:
        raw_input: The content to investigate, exactly as submitted.
        input_type: The declared input kind. Only `TEXT` is analysed in this
            version; the others are recognised and refused with a typed reason.
        context: A fully built context. Mutually exclusive with `dependencies`.
        dependencies: Dependencies to wrap in a fresh context, keeping the
            default clock. Mutually exclusive with `context`.

    Returns:
        The final `InvestigationState`. On success it carries a
        `risk_assessment`; on a rejected or failed run it carries `errors`, and
        `risk_assessment` is absent or `None`. The function does not raise for
        an investigation that could not be completed — the failure is in the
        state, because a caller must be able to render it.

    Raises:
        ValueError: If both `context` and `dependencies` are supplied, since
            there would be no principled way to choose between them.
    """
    if context is not None and dependencies is not None:
        raise ValueError(
            "pass either context or dependencies, not both: they are two ways "
            "of supplying the same thing"
        )

    resolved = context
    if resolved is None:
        if dependencies is not None:
            resolved = GraphContext(dependencies=dependencies)
        else:
            resolved = build_default_context()

    kind = (
        input_type.value
        if isinstance(input_type, InvestigationInputType)
        else str(input_type)
    )

    logger.info(
        "Investigation started",
        extra={"input_type": kind, "input_length": len(raw_input or "")},
    )

    graph = build_investigation_graph()
    final = graph.invoke(
        {"raw_input": raw_input or "", "input_type": kind},
        context=resolved,
    )
    return final


def investigation_summary(state: InvestigationState) -> dict[str, Any]:
    """Reduce a final state to a small, comparable summary.

    A convenience for tests and scripts. It deliberately exposes counts and the
    presence of a result, never a restatement of the risk score as a percentage
    or a plain-language verdict: the score is a Phase 6 heuristic indicator
    count, and a summary helper is exactly the kind of place where such a
    restatement would creep in (D-025).

    Args:
        state: A final investigation state.

    Returns:
        Counts of claims, entities, red flags, evidence items and risk factors,
        plus the recorded stage and status totals from the timeline.
    """
    reduced = semantic_view(state)
    timeline = reduced.get("timeline") or ()
    assessment = state.get("risk_assessment")

    return {
        "investigation_id": state.get("investigation_id"),
        "current_stage": state.get("current_stage"),
        "claims": len(state.get("claims") or ()),
        "entities": len(state.get("entities") or ()),
        "red_flags": len(state.get("red_flags") or ()),
        "verification_results": len(state.get("verification_results") or ()),
        "evidence_items": sum(
            len(response.evidence) for response in state.get("evidence") or ()
        ),
        "risk_factors": len(assessment.factors) if assessment is not None else 0,
        "has_risk_assessment": assessment is not None,
        "error_count": len(state.get("errors") or ()),
        "warning_count": len(state.get("warnings") or ()),
        "stage_statuses": tuple(status for _, status, _ in timeline),
    }