"""LangGraph investigation orchestration (Phase 7).

The graph connects the services that already exist. It owns **no** business
logic: no rule, no prompt, no provider call, no threshold and no score is
computed here. Every stage delegates to the Phase that owns its responsibility.

```
                 ┌──────────────────────┐
                 │  LangGraph           │
                 │  orchestration       │
                 └──────────┬───────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
ExtractionService    RedFlagEngine    VerificationService
   (Phase 2)           (Phase 1)           (Phase 4)
                                                │
                                                ▼
                                        EvidenceService
                                           (Phase 5)
                                                │
                                                ▼
                                          RiskService
                                            (Phase 6)
```

Example:
    >>> from app.graph import run_investigation
    >>> state = run_investigation("Guaranteed 30% monthly returns!")  # doctest: +SKIP
    >>> state["risk_assessment"].risk_level  # doctest: +SKIP

A run is a **partial result** by default rather than a pass/fail. Search being
unavailable, a claim that cannot be checked, an extraction that fell back to
patterns: each is recorded as a structured warning and the investigation
continues. The run ends only when a submission is rejected or a service contract
is broken, and then the failure is a structured `GraphError` in the state rather
than an exception — a caller must be able to render a run that did not complete.

A recorded error always stops the run. A stage that failed and then let the
remaining stages proceed would report an assessment that looks complete while
being built on a stage that silently did nothing, and for the stages feeding the
score that means understating risk.

The graph makes no network call of its own. External access stays inside
`SearchService`, and the searches Phase 4 performs are recorded by a
`RecordingSearchService` so Phase 5 can cite them without the graph issuing a
second, possibly differently ranked, set of queries.
"""

from app.graph.context import (
    GraphContext,
    GraphDependencies,
    RecordingSearchService,
    build_default_context,
    utc_now,
)
from app.graph.edges import ABORT, CONTINUE, route_on_recorded_error
from app.graph.investigation_graph import (
    NODE_NAMES,
    build_investigation_graph,
    investigation_summary,
    run_investigation,
)
from app.graph.nodes import ERROR_CODES, WARNING_CODES
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

__all__ = [
    "ERROR_CODES",
    "NODE_NAMES",
    "ABORT",
    "CONTINUE",
    "WARNING_CODES",
    "GraphContext",
    "GraphDependencies",
    "GraphError",
    "GraphStage",
    "GraphWarning",
    "InvestigationInputType",
    "InvestigationState",
    "RecordingSearchService",
    "TimelineEvent",
    "TimelineStatus",
    "build_default_context",
    "build_investigation_graph",
    "investigation_id_for",
    "investigation_summary",
    "route_on_recorded_error",
    "run_investigation",
    "semantic_view",
    "utc_now",
]