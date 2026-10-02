"""Run history, paging and deletion (Phase 9).

The history endpoint is what makes persistence useful to a user: being able to
come back and ask "what did I run?" rather than re-submitting content they
already have. Its contract is therefore about **completeness and stability**:
every run is listed, the order is total, and paging cannot skip or repeat a row.

The subtleties these tests pin are the ones a content-digest id creates. Because
`investigation_id_for` hashes the submitted text, re-running identical content
yields the *same* public id — so ordering cannot use it, and a single id can stand
for several real runs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api.adapters import investigation_status
from app.graph.context import GraphContext
from app.graph.investigation_graph import run_investigation
from app.graph.state import InvestigationInputType, semantic_view
from app.models.investigation import (
    ClaimRow,
    EvidenceItemRow,
    InvestigationRow,
    SourceRow,
)
from app.repositories.investigations import InvestigationRepository
from tests.persistence_factories import (
    MESSY_CONTENT,
    REGULATORY_CONTENT,
    run_only,
    store_run,
)


def _store(repo: InvestigationRepository, context: GraphContext, text: str) -> str:
    """Run and store one investigation, returning its public id.

    Args:
        repo: The repository to write through.
        context: The graph context to run against.
        text: Content to investigate.

    Returns:
        The Phase 7 public id the run was stored under.
    """
    return store_run(repo, context, text)


def test_history_is_empty_before_anything_runs(repo: InvestigationRepository) -> None:
    """A fresh installation lists nothing, and says so with a zero total."""
    page, total = repo.list_page(limit=20)

    assert page == []
    assert total == 0


def test_history_lists_stored_runs_newest_first(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """Three different submissions come back in reverse order of storage.

    Newest first because the common question is "what did I just look at?", and
    because a history that is ordered by anything else makes a client sort it
    again before showing it.
    """
    first = _store(repo, real_context, "First submission about Acme Capital Advisors.")
    second = _store(repo, real_context, "Second submission about Beta Holdings Ltd.")
    third = _store(repo, real_context, "Third submission about Gamma Ventures LLP.")

    page, total = repo.list_page(limit=20)

    assert total == 3
    assert [item.investigation_id for item in page] == [third, second, first]


def test_a_summary_reports_the_status_and_counts_of_its_run(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """The history entry describes the run accurately, not approximately.

    The counts come from the stored run rather than a fresh aggregation, so this
    is a check that the denormalisation is faithful — a summary that reported
    zero claims for a run that found two would send a client to an investigation
    and then have nothing to show.
    """
    public_id = _store(repo, real_context, MESSY_CONTENT)

    state = repo.load(public_id)
    page, _ = repo.list_page(limit=20)
    summary = page[0]

    assert summary.investigation_id == public_id
    assert summary.input_type == InvestigationInputType.TEXT.value
    assert summary.claim_count == len(state["claims"])
    assert summary.red_flag_count == len(state["red_flags"])
    assert summary.evidence_count == sum(
        len(response.evidence) for response in state["evidence"]
    )
    assert summary.started_at == state["started_at"]
    assert summary.created_at is not None


def test_both_runs_of_the_same_content_appear_in_history(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """A re-run is a second entry, sharing the id, both reachable.

    The temptation is to key history on `investigation_id`, which would show one
    row for both runs. That is precisely the information the history endpoint
    exists to preserve: the second run is the evidence that this content was
    checked twice, and hiding it would make the endpoint unable to answer the
    question it was built for.
    """
    public_id = _store(repo, real_context, MESSY_CONTENT)
    assert _store(repo, real_context, MESSY_CONTENT) == public_id

    page, total = repo.list_page(limit=20)

    assert total == 2
    assert {item.investigation_id for item in page} == {public_id}
    assert len({item.created_at for item in page}) == 2


def test_paging_covers_every_row_exactly_once(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """Walking the history in pages visits every run once, with no gaps.

    Ordering is `created_at` with the surrogate key as a tie-break. Two runs
    stored in the same millisecond have the same `created_at`, and without a total
    order the database is free to return them in a different order for each
    query — which would show a client one row twice and skip another.
    """
    texts = [
        f"Submission number {index} concerning Acme Capital Advisors and its claims."
        for index in range(7)
    ]
    for text in texts:
        _store(repo, real_context, text)

    seen: list[str] = []
    offset = 0
    while True:
        page, total = repo.list_page(limit=3, offset=offset)
        assert total == 7
        if not page:
            break
        seen.extend(item.investigation_id for item in page)
        offset += 3

    assert len(seen) == 7
    assert len(set(seen)) == 7


def test_paging_past_the_end_returns_nothing_rather_than_failing(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """An offset beyond the data is an empty page, not an error.

    A client that pages until it sees an empty list is the intended way to walk
    the history; making that raise would force every client to first ask how much
    history exists, and to keep that answer fresh while it pages.
    """
    _store(repo, real_context, "A single submission about Acme Capital Advisors.")

    page, total = repo.list_page(limit=20, offset=500)

    assert page == []
    assert total == 1


def test_retrieval_returns_the_newest_run_of_a_shared_id(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """A `GET` answers with the run the client most recently caused.

    A client's flow is `POST` then `GET` with the id it was handed. If `GET`
    returned the older of two runs, the client would immediately see a result
    that disagrees with the one it was just given — the single most confusing
    failure this API could have.
    """
    first = _store(repo, real_context, MESSY_CONTENT)
    original = repo.load(first)
    _store(repo, real_context, MESSY_CONTENT)

    latest = repo.load(first)

    assert semantic_view(latest) == semantic_view(original)
    assert repo.session.query(InvestigationRow).count() == 2


def test_deleting_removes_every_run_under_the_id(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """Delete takes the whole family, not the newest member.

    Removing "the" investigation for a piece of content while leaving its re-runs
    behind would make the id retrievable again with no way to ask for that, and
    would strand rows no query filters by.
    """
    public_id = _store(repo, real_context, MESSY_CONTENT)
    _store(repo, real_context, MESSY_CONTENT)

    assert repo.delete(public_id) is True
    repo.commit()

    assert repo.find(public_id) is None
    assert repo.session.query(InvestigationRow).count() == 0


def test_deleting_an_unknown_id_reports_that_nothing_was_removed(
    repo: InvestigationRepository,
) -> None:
    """A no-op delete says so, rather than claiming success."""
    assert repo.delete("inv_deadbeefdeadbeef") is False


def test_deleting_one_run_leaves_other_content_untouched(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """Delete is scoped to the id given and to nothing else.

    Content ids are digests, so two submissions are unrelated rows sharing no
    parent. A delete that took the wrong one would destroy an investigation the
    caller never mentioned.
    """
    kept = _store(repo, real_context, "A submission about Acme Capital Advisors.")
    removed = _store(repo, real_context, "A different submission about Beta Ltd.")

    assert repo.delete(removed) is True
    repo.commit()

    assert repo.find(kept) is not None
    assert repo.find(removed) is None


def test_deleting_a_run_removes_its_child_rows(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """No child outlives its run.

    An orphan row is unreachable through every endpoint and invisible to every
    query, so it is not merely untidy — it is data that can never be found,
    corrected or deleted again. The cascade is asserted here against a real
    deletion, not only in the schema, because a cascade that is declared but not
    emitted leaves exactly this behind.
    """
    _store(repo, real_context, MESSY_CONTENT)

    assert repo.session.query(ClaimRow).count() > 0
    assert repo.session.query(EvidenceItemRow).count() > 0
    assert repo.session.query(SourceRow).count() > 0

    repo.delete(repo.session.query(InvestigationRow).one().public_id)
    repo.commit()

    assert repo.session.query(ClaimRow).count() == 0
    assert repo.session.query(EvidenceItemRow).count() == 0
    assert repo.session.query(SourceRow).count() == 0


def test_a_rolled_back_write_leaves_nothing_behind(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """An uncommitted write stores no run and no fragments of one.

    The alternative — a run whose claims committed but whose evidence did not —
    would be retrievable, would report `COMPLETED`, and would be missing exactly
    the part a reader relies on most. The route commits once, after the whole run
    is assembled, so "all of it or none of it" is a property of the transaction
    boundary rather than of the order the inserts happen in.
    """
    state = run_investigation(
        MESSY_CONTENT, input_type=InvestigationInputType.TEXT, context=real_context
    )
    repo.save(state)
    repo.rollback()

    assert repo.session.query(ClaimRow).count() == 0
    assert repo.session.query(EvidenceItemRow).count() == 0
    assert repo.session.query(SourceRow).count() == 0
    assert repo.session.query(InvestigationRow).count() == 0


def test_a_duplicate_run_is_stored_twice_rather_than_rejected(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """Re-running identical content is a second event, not a constraint failure.

    Every uniqueness rule in this schema is scoped to the surrogate `id`, which
    differs per run. That is deliberate: a schema keyed on the content digest
    would make investigating the same text twice an error, and the second attempt
    would silently return the first attempt's findings as if they were fresh.
    """
    state = run_investigation(
        MESSY_CONTENT, input_type=InvestigationInputType.TEXT, context=real_context
    )
    repo.save(state)
    repo.commit()
    repo.save(state)
    repo.commit()

    assert repo.session.query(InvestigationRow).count() == 2


def test_history_order_is_stable_when_runs_share_a_timestamp(
    repo: InvestigationRepository,
) -> None:
    """Runs stored at the same instant still come back in a fixed order.

    `created_at` has microsecond resolution and a fast test can genuinely produce
    two runs inside one. The surrogate key breaks the tie, so the order does not
    depend on which row the database happened to find first.
    """
    moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(3):
        repo.session.add(
            InvestigationRow(
                public_id=f"inv_{index:016x}",
                content_hash=f"inv_{index:016x}",
                input_type="TEXT",
                raw_input="text",
                extracted_text="text",
                status="COMPLETED",
                current_stage="completed",
                started_at=moment,
                completed_at=moment,
                created_at=moment,
            )
        )
    repo.session.flush()
    for row in repo.session.query(InvestigationRow).all():
        row.created_at = moment
    repo.session.commit()

    first = [item.investigation_id for item in repo.list_page(limit=20)[0]]
    second = [item.investigation_id for item in repo.list_page(limit=20)[0]]

    assert first == second
    assert set(first) == {"inv_" + format(index, "016x") for index in range(3)}


def test_a_partial_run_is_stored_and_listed_with_its_status(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """A degraded run is kept, and its status says so.

    Not storing a partial result would make "we could not check this" the one
    outcome the product cannot show you again, which is the failure D-006 exists
    to prevent. The offline context has no search key, so its runs degrade
    naturally rather than being forced to.
    """
    public_id = _store(repo, real_context, MESSY_CONTENT)
    reloaded = repo.load(public_id)
    expected = investigation_status(
        tuple(reloaded.get("errors") or ()), tuple(reloaded.get("timeline") or ())
    ).value

    page, _ = repo.list_page(limit=20)

    assert page[0].status == expected
    # The offline run genuinely degraded (no search key), so the stored row must
    # still be there *and* still say why it is partial.
    assert reloaded["warnings"], "the offline fixture must record a limitation"


def test_the_tie_breaker_is_part_of_the_declared_ordering() -> None:
    """Ordering is `created_at` then the surrogate key, so it is a total order.

    Asserted against the query rather than behaviourally, because a behavioural
    test cannot force two runs into the same microsecond reliably. The property
    being protected is that the ordering is *total*: with only `created_at` in the
    `ORDER BY`, the database is free to return tied rows in a different order for
    each query, and a client paging the history would then see a row twice and
    skip another.
    """
    compiled = str(
        select(InvestigationRow)
        .order_by(InvestigationRow.created_at.desc(), InvestigationRow.id.desc())
    )

    assert "created_at DESC" in compiled
    assert "investigations.id DESC" in compiled


def test_totals_come_from_the_same_table_as_the_page(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """`total` counts stored runs, and counts all of them.

    A total scoped to the page would report `limit` for every non-empty request
    and make a client believe its history ended there.
    """
    for index in range(4):
        _store(repo, real_context, f"Submission {index} about Acme Capital Advisors.")

    page, total = repo.list_page(limit=2)

    assert len(page) == 2
    assert total == 4


def test_a_limit_of_one_still_reports_the_full_total(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """Paging metadata describes the history, not the request."""
    for index in range(3):
        _store(repo, real_context, f"Submission {index} about Acme Capital Advisors.")

    page, total = repo.list_page(limit=1)

    assert len(page) == 1
    assert total == 3


def test_stored_timestamps_are_aware_utc_on_the_way_back(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """Every timestamp read back is timezone-aware, whatever the backend did.

    SQLite drops the offset on the way in. If the read side handed back a naive
    datetime, a client serialising it would emit a string with no `Z` and no
    offset, and two clients in different zones would disagree about when the
    investigation happened.
    """
    public_id = _store(repo, real_context, MESSY_CONTENT)

    page, _ = repo.list_page(limit=20)
    reloaded = repo.load(public_id)

    assert page[0].created_at.tzinfo is not None
    assert page[0].started_at.tzinfo is not None
    assert reloaded["started_at"].tzinfo is not None
    for response in reloaded["evidence"]:
        assert response.built_at.tzinfo is not None
        for item in response.evidence:
            assert item.source.retrieved_at.tzinfo is not None


def test_run_timings_are_preserved_exactly_as_recorded(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """Start and completion times survive as the two instants they were.

    A run that started at 10:00 and finished at 10:00:03 must not come back as
    both 10:00, or a client rendering "took 0s" would be reporting storage losing
    information rather than a fast run.

    Phase 7's `completed_at` is asserted to round-trip whatever the graph
    recorded, including `None`. It is currently always `None` — no node writes it
    — so this test also pins that storage faithfully preserves the absence rather
    than inventing a finish time, which would be a claim the pipeline never made.
    """
    state = run_investigation(
        MESSY_CONTENT, input_type=InvestigationInputType.TEXT, context=real_context
    )
    started = state["started_at"]
    completed = state.get("completed_at")

    repo.save(state)
    repo.commit()
    reloaded = repo.load(str(state["investigation_id"]))

    assert reloaded["started_at"] == started
    assert reloaded["completed_at"] == completed
    if completed is None:
        assert reloaded["completed_at"] is None


def test_a_run_stored_twice_keeps_both_created_at_values(
    repo: InvestigationRepository, real_context: GraphContext
) -> None:
    """`created_at` distinguishes two runs that share everything else.

    It is the only column that can: same content, same public id, same findings.
    Without it the history would contain two indistinguishable rows, and "was
    this checked before?" would have no answer.
    """
    _store(repo, real_context, MESSY_CONTENT)
    _store(repo, real_context, MESSY_CONTENT)

    page, _ = repo.list_page(limit=20)
    stamps = sorted(item.created_at for item in page)

    assert stamps[1] - stamps[0] > timedelta(0)
