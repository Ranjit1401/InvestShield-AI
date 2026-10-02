"""Content and helpers for the persistence tests (Phase 9).

Kept apart from the fixtures in `tests/conftest.py` for the same reason
`evidence_factories.py` and `risk_factories.py` exist: a fixture decides *how* a
test is set up, and this module decides *what the data is*. Tests import the
content from here so two suites cannot quietly start using different fixtures for
the same "suspicious investment content".
"""

from __future__ import annotations

from app.graph.investigation_graph import run_investigation
from app.graph.state import InvestigationInputType, InvestigationState
from app.repositories.investigations import InvestigationRepository

#: Text that trips several Phase 1 rules and yields extractable claims.
#:
#: Its claims are guarantees and a payment instruction, which Phase 4 correctly
#: reports as `NOT_A_FACTUAL_CLAIM` — the claim family that cannot be settled by
#: an external record. That is the right content for testing degradation and
#: storage of a partial run, and the wrong content for testing evidence.
MESSY_CONTENT = (
    "Acme Capital Advisors guarantees 30% monthly returns with no risk. "
    "Act now: pay the 50000 INR activation fee today only to reserve your slot."
)

#: A regulatory-status claim, which *is* externally verifiable.
#:
#: Phase 4 therefore actually searches for it, the search recorder records the
#: fixture regulator result, and the evidence node has something real to
#: assemble. A test that wants evidence, sources or a scored risk assessment needs
#: this rather than `MESSY_CONTENT`, which produces none of the three.
REGULATORY_CONTENT = "Acme Capital Advisors is SEBI registered."


def store_run(
    repo: InvestigationRepository,
    context: object,
    text: str = MESSY_CONTENT,
) -> str:
    """Run one investigation, store it, and return its public id.

    Args:
        repo: The repository to write through.
        context: The `GraphContext` to run against.
        text: Content to investigate.

    Returns:
        The Phase 7 public id the run was stored under.
    """
    state = run_investigation(
        text, input_type=InvestigationInputType.TEXT, context=context
    )
    repo.save(state)
    repo.commit()
    return str(state["investigation_id"])


def run_only(
    context: object,
    text: str = MESSY_CONTENT,
) -> InvestigationState:
    """Run one investigation without storing it.

    Args:
        context: The `GraphContext` to run against.
        text: Content to investigate.

    Returns:
        The final investigation state, exactly as the graph produced it.
    """
    return run_investigation(
        text, input_type=InvestigationInputType.TEXT, context=context
    )


__all__ = [
    "MESSY_CONTENT",
    "REGULATORY_CONTENT",
    "run_only",
    "store_run",
]
