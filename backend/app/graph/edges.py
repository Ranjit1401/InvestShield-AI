"""Graph wiring and conditional routing (Phase 7).

Routing is deliberately minimal, and it exists for one reason.

**No stage may continue past a recorded error.** The predicate is applied after
every stage, not only where branching seemed useful. An earlier draft routed only
the input node and treated a failed extraction, a failed red-flag pass or a
failed verification as something to note and walk past — and the result was the
worst possible behaviour: a run whose extraction had raised still went on to
produce a full `risk_assessment`, which reads to any caller exactly like a
complete investigation. A red-flag pass that failed and then reported "no
patterns found" understates risk, which is the one direction this product must
never err in.

So the same predicate guards every stage. A node that records a `GraphError` ends
the run, whether or not the remaining stages could technically have proceeded.

The predicate tests for a *recorded error* rather than for any specific failure
code, so a new failure mode added to a node is routed correctly by default instead
of being accidentally let through.

There is deliberately **no** other branch. In particular "no claims were
extracted, so skip verification and evidence" is not an edge here: it is a guard
inside those two nodes, because a guard makes no service call at all and can
record its own `SKIPPED` timeline entry, where a branch would need a third
destination or a pass-through node to say what was skipped. Same saving, one less
piece of control flow.
"""

from __future__ import annotations

from app.graph.state import InvestigationState

__all__ = [
    "ABORT",
    "CONTINUE",
    "route_on_recorded_error",
]

#: Destination used while no stage has recorded a failure.
CONTINUE = "continue"

#: Destination used once any stage has recorded a structured error.
ABORT = "abort"


def route_on_recorded_error(state: InvestigationState) -> str:
    """Decide whether the run may proceed past the stage that just ran.

    Args:
        state: State after a stage has completed.

    Returns:
        `ABORT` when any stage has recorded a `GraphError`, meaning the run can
        no longer stand behind its own output, and `CONTINUE` otherwise.
    """
    if state.get("errors"):
        return ABORT
    return CONTINUE