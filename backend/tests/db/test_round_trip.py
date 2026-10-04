"""Round-trip tests: a run survives storage unchanged (Phase 9).

The contract these tests protect is the one the API depends on: `GET
/api/investigations/{id}` must return exactly what the `POST` that stored the run
returned. That holds only if a stored investigation rebuilds into the same
`InvestigationState` the graph produced, so the assertion throughout is
`semantic_view(reloaded) == semantic_view(live)` — Phase 7's own definition of
"the same run", with only the wall-clock metadata excluded.

Anything weaker would pass while storage quietly lost a field, and the loss
would surface as a report that no longer matches the investigation behind it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.graph.context import GraphContext
from app.graph.investigation_graph import run_investigation
from app.graph.state import InvestigationInputType, semantic_view
from app.models.investigation import (
    ClaimEntityLinkRow,
    ClaimRow,
    EntityRow,
    EvidenceItemRow,
    EvidenceResponseRow,
    InvestigationRow,
    RedFlagRow,
    RiskFactorRow,
    SourceRow,
    TimelineEventRow,
    VerificationResultRow,
)
from app.repositories.investigations import InvestigationRepository, NotFound
from app.schemas.risk import SCORE_NOT_A_PROBABILITY
from app.schemas.verification import VerificationStatus
from tests.persistence_factories import MESSY_CONTENT, run_only
from tests.graph.graph_factories import FIXED_INSTANT


def _run(context: GraphContext) -> dict:
    """Run the graph over the fixture content.

    Args:
        context: The graph context to run against.

    Returns:
        The final investigation state, exactly as the graph produced it.
    """
    return run_only(context, MESSY_CONTENT)

def test_a_stored_run_reloads_identically(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """Every semantic field survives a write/read cycle.

    The single test the whole layer exists to make true. It is deliberately
    whole-state rather than field-by-field: a per-field assertion suite would
    happily pass while a field nobody thought to list was being dropped.
    """
    live = _run(real_context)
    repo.save(live)
    repo.commit()

    reloaded = repo.load(live["investigation_id"])

    assert semantic_view(reloaded) == semantic_view(live)


def test_a_run_with_entities_links_and_a_score_reloads_identically(
    repo: InvestigationRepository, rich_context: GraphContext
) -> None:
    """The populated case round-trips too, not just the sparse one.

    The default offline context emits no entities and no claim-to-entity links,
    so a suite built only on it would leave both tables untested while reporting
    full coverage. This context produces claims, an entity, a link, a red flag
    and a scored risk assessment, which is every collection the schema stores.
    """
    live = _run(rich_context)
    assert live["entities"], "fixture must produce an entity or the test proves nothing"
    assert live["claim_entity_links"], "fixture must produce a link or the table goes untested"
    assert live["risk_assessment"] is not None

    repo.save(live)
    repo.commit()
    reloaded = repo.load(live["investigation_id"])

    assert semantic_view(reloaded) == semantic_view(live)


def test_claim_entity_links_survive_verbatim(
    repo: InvestigationRepository, rich_context: GraphContext
) -> None:
    """The link table stores the relationship the extractor recorded.

    Not the relationship the link table could have inferred from
    `Claim.entity_ids`: a claim that names two entities does not imply the
    extractor judged both as `MENTIONS`, and inventing a verb the extractor never
    chose would make the stored graph disagree with the extraction it came from.
    """
    live = _run(rich_context)
    repo.save(live)
    repo.commit()

    reloaded = repo.load(live["investigation_id"])

    assert reloaded["claim_entity_links"] == live["claim_entity_links"]


def test_claim_entity_ids_are_stored_unchanged(
    repo: InvestigationRepository, rich_context: GraphContext
) -> None:
    """`Claim.entity_ids` round-trips alongside the link table, not through it.

    Both exist in the Phase 2 schema and both are stored. Reconciling them on
    read would make a stored run differ from the run that produced it, in the one
    place where the two representations are meant to be independently checkable.
    """
    live = _run(rich_context)
    repo.save(live)
    repo.commit()

    reloaded = repo.load(live["investigation_id"])

    assert [claim.entity_ids for claim in reloaded["claims"]] == [
        claim.entity_ids for claim in live["claims"]
    ]


def test_risk_assessment_keeps_its_caveat(
    repo: InvestigationRepository, rich_context: GraphContext
) -> None:
    """A stored assessment still refuses to read as a probability.

    The caveat is `SCORE_NOT_A_PROBABILITY`, appended to `warnings` by
    `RiskAssessment` itself during validation. There is no column for it, and
    that is the point: rebuilding the assessment *through the model* reproduces
    the caveat whether or not the stored warnings list still carries it, so it
    cannot be lost by a storage bug (D-025).
    """
    live = _run(rich_context)
    assert live["risk_assessment"] is not None
    assert live["risk_assessment"].risk_score > 0

    repo.save(live)
    repo.commit()
    reloaded = repo.load(live["investigation_id"])

    assessment = reloaded["risk_assessment"]
    assert assessment is not None
    assert assessment.risk_score == live["risk_assessment"].risk_score
    assert assessment.raw_score == live["risk_assessment"].raw_score
    assert SCORE_NOT_A_PROBABILITY in assessment.warnings


def test_risk_weights_and_thresholds_are_snapshotted(
    repo: InvestigationRepository, rich_context: GraphContext
) -> None:
    """A stored assessment carries the configuration that produced it.

    A reader asking "why is this 25?" needs the weights in effect at the time,
    not the weights as configured today. Snapshotted onto the row rather than
    looked up, so a later config change cannot retroactively explain an old score
    (D-007).
    """
    live = _run(rich_context)
    repo.save(live)
    repo.commit()

    reloaded = repo.load(live["investigation_id"])

    assert reloaded["risk_assessment"].weights == live["risk_assessment"].weights
    assert reloaded["risk_assessment"].thresholds == live["risk_assessment"].thresholds


def test_risk_factor_trace_survives(
    repo: InvestigationRepository, rich_context: GraphContext
) -> None:
    """A factor still points at the claim, flag and evidence behind it.

    The trace is the whole reason a factor is stored rather than recomputed: a
    reader must be able to walk from a risk factor to the document that produced
    it, which is the audit path D-026 requires.
    """
    live = _run(rich_context)
    repo.save(live)
    repo.commit()

    reloaded = repo.load(live["investigation_id"])

    assert [factor.id for factor in reloaded["risk_assessment"].factors] == [
        factor.id for factor in live["risk_assessment"].factors
    ]
    for stored, original in zip(
        reloaded["risk_assessment"].factors, live["risk_assessment"].factors, strict=True
    ):
        assert stored.red_flag_ids == original.red_flag_ids
        assert stored.claim_ids == original.claim_ids
        assert stored.contribution == original.contribution
        assert stored.absorbed_into == original.absorbed_into


def test_evidence_source_order_is_preserved(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """An evidence response lists its sources in the order Phase 5 built them.

    Order is a property of the answer, not a display detail: the sources are the
    documents a reader is expected to open in the order the engine presented
    them. A set-derived order would make a retrieved investigation list the same
    documents differently from the live one.
    """
    live = _run(real_context)
    repo.save(live)
    repo.commit()

    reloaded = repo.load(live["investigation_id"])

    for before, after in zip(live["evidence"], reloaded["evidence"], strict=True):
        assert [source.source_id for source in after.sources] == [
            source.source_id for source in before.sources
        ]
        assert [source.result_id for source in after.sources] == [
            source.result_id for source in before.sources
        ]


def test_evidence_items_keep_their_exact_provenance(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """An item's source is the result it actually cited.

    A document can be returned by more than one search result. `ev_` ids are
    derived from both the source id and the result id, so an item that round-trips
    to the wrong `result_id` would carry an id no reader could recompute from the
    provenance shown beside it.
    """
    live = _run(real_context)
    repo.save(live)
    repo.commit()

    reloaded = repo.load(live["investigation_id"])

    reloaded_items = [
        item for response in reloaded["evidence"] for item in response.evidence
    ]
    live_items = [item for response in live["evidence"] for item in response.evidence]
    assert reloaded_items, "fixture must produce evidence or the test proves nothing"

    for after, before in zip(reloaded_items, live_items, strict=True):
        assert after.id == before.id
        assert after.source.source_id == before.source.source_id
        assert after.source.result_id == before.source.result_id
        assert after.source.url == before.source.url
        assert after.excerpt == before.excerpt
        assert after.excerpt_origin == before.excerpt_origin


def test_red_flag_spans_survive_so_ids_stay_recomputable(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """A stored flag still hashes to the `rf_` id Phase 6 cited.

    `red_flag_id_for` hashes the code, the span and the matched text. Losing any
    of the three would leave a stored risk factor citing an id that no longer
    corresponds to any row — a dangling trace that looks intact.
    """
    from app.services.risk import red_flag_id_for

    live = _run(real_context)
    repo.save(live)
    repo.commit()

    reloaded = repo.load(live["investigation_id"])

    assert reloaded["red_flags"], "fixture must produce a flag or the test proves nothing"
    for after, before in zip(reloaded["red_flags"], live["red_flags"], strict=True):
        assert red_flag_id_for(after) == red_flag_id_for(before)


def test_a_duplicate_public_id_stores_two_separate_runs(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """Re-running the same content is a second event, not an overwrite.

    `investigation_id_for` digests the submitted text, so the two runs share a
    public id by construction. Collapsing them would erase the only evidence that
    the investigation was ever run twice, and would make a client's "has this been
    checked before?" question unanswerable in the one case where it matters most.
    """
    first = _run(real_context)
    repo.save(first)
    repo.commit()
    second = _run(real_context)
    repo.save(second)
    repo.commit()

    stored = repo.session.scalars(select(InvestigationRow)).all()

    assert len(stored) == 2
    assert {row.public_id for row in stored} == {first["investigation_id"]}
    # Retrieval returns the newer run; both remain in history.
    assert repo.load(first["investigation_id"])["investigation_id"] == first["investigation_id"]


def test_a_refused_submission_is_never_stored(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """A run the input node refused leaves no row behind.

    The graph returns a state carrying a recorded error rather than raising, so a
    caller that saved every state without checking would fill the history with
    entries describing investigations that never happened. Storing is the route's
    decision, and it makes that decision only after the error check.
    """
    state = run_investigation(
        "   ",
        input_type=InvestigationInputType.TEXT,
        context=real_context,
    )
    assert state["errors"], "whitespace-only input must be refused"

    assert not repo.session.scalars(select(InvestigationRow)).all()


def test_loading_an_unknown_id_raises_not_found(repo: InvestigationRepository) -> None:
    """A miss is a typed failure, not a crash and not an empty response.

    `NotFound` lets the route answer `404` with a fixed message. Returning an
    empty state instead would produce a `200` carrying an investigation with no
    claims and no errors — indistinguishable from a real run that found nothing.
    """
    with pytest.raises(NotFound):
        repo.load("inv_deadbeefdeadbeef")


def test_find_returns_none_for_an_unknown_id(repo: InvestigationRepository) -> None:
    """`find` is the non-raising form, and returns `None` rather than a state."""
    assert repo.find("inv_deadbeefdeadbeef") is None


def test_denormalised_counts_match_the_stored_rows(
    repo: InvestigationRepository, rich_context: GraphContext
) -> None:
    """The counts on the run agree with the rows beneath it.

    They are stored rather than aggregated so the history endpoint can list runs
    cheaply. A count that drifts from its children is worse than no count, so the
    agreement is pinned here rather than assumed.
    """
    live = _run(rich_context)
    repo.save(live)
    repo.commit()

    row = repo.session.scalars(select(InvestigationRow)).one()
    counts = _row_counts(repo.session, row.id)

    assert row.claim_count == len(live["claims"]) == counts[ClaimRow]
    assert row.entity_count == len(live["entities"]) == counts[EntityRow]
    assert row.red_flag_count == len(live["red_flags"]) == counts[RedFlagRow]
    verified = sum(
        1
        for result in live["verification_results"]
        if result.status is VerificationStatus.VERIFIED
    )
    # The denormalised count is the confirmed subset — the same number
    # the report summary interpolates — while the child rows hold every
    # result regardless of status.
    assert row.verification_count == verified
    assert len(live["verification_results"]) == counts[VerificationResultRow]
    assert row.evidence_count == sum(
        len(response.evidence) for response in live["evidence"]
    ) == counts[EvidenceItemRow]
    assert row.source_count == sum(len(response.sources) for response in live["evidence"]) == (
        counts[SourceRow]
    )
    assert row.factor_count == len(live["risk_assessment"].factors) == counts[RiskFactorRow]


def _row_counts(session: Session, investigation_pk: int) -> dict[type, int]:
    """Count the stored child rows of one investigation, per table.

    Args:
        session: An open session on the database under test.
        investigation_pk: The surrogate key of the parent run.

    Returns:
        A mapping of row class to the number of its rows for that run.
    """
    totals: dict[type, int] = {}
    for model in (
        ClaimRow,
        EntityRow,
        RedFlagRow,
        VerificationResultRow,
        EvidenceItemRow,
        SourceRow,
        RiskFactorRow,
    ):
        statement = select(model).where(model.investigation_id == investigation_pk)
        totals[model] = len(session.scalars(statement).all())
    return totals


def test_every_collection_is_actually_written(
    repo: InvestigationRepository, rich_context: GraphContext
) -> None:
    """Each child table receives rows, rather than being silently skipped.

    A mapping bug that dropped, say, `claim_entity_links` would leave the
    round-trip test failing only in a fixture that produces links — which is why
    this test counts rows directly against the tables.
    """
    live = _run(rich_context)
    repo.save(live)
    repo.commit()

    session = repo.session
    assert session.scalars(select(ClaimRow)).all()
    assert session.scalars(select(EntityRow)).all()
    assert session.scalars(select(ClaimEntityLinkRow)).all()
    assert session.scalars(select(RedFlagRow)).all()
    assert session.scalars(select(VerificationResultRow)).all()
    assert session.scalars(select(EvidenceResponseRow)).all()
    assert session.scalars(select(EvidenceItemRow)).all()
    assert session.scalars(select(SourceRow)).all()
    assert session.scalars(select(RiskFactorRow)).all()
    assert session.scalars(select(TimelineEventRow)).all()


def test_a_run_with_no_risk_assessment_reloads_as_absent(
    repo: InvestigationRepository
) -> None:
    """A run with no assessment reports no assessment, not a zero score.

    Built by hand because no offline fixture produces one: the risk node only
    runs after verification, so every fixture here scores. A stored `0` would read
    as "we checked and it was fine" for a run that never checked.
    """
    from app.graph.state import (
        GraphStage,
        InvestigationState,
        TimelineEvent,
        TimelineStatus,
    )

    state: InvestigationState = {
        "input_type": InvestigationInputType.TEXT.value,
        "raw_input": MESSY_CONTENT,
        "extracted_text": MESSY_CONTENT,
        "investigation_id": "inv_0000000000000abc",
        "started_at": FIXED_INSTANT,
        "completed_at": None,
        "current_stage": GraphStage.EXTRACTION.value,
        "red_flags": (),
        "timeline": (
            TimelineEvent(
                stage=GraphStage.INPUT,
                status=TimelineStatus.COMPLETED,
                message="Input accepted.",
                at=FIXED_INSTANT,
            ),
        ),
    }

    repo.save(state)
    repo.commit()
    reloaded = repo.load(state["investigation_id"])

    assert reloaded["risk_assessment"] is None
    assert reloaded["completed_at"] is None
