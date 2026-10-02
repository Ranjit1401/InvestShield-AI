"""Schema shape and backend portability (Phase 9).

The schema is the one artefact in this project that has to be right on **two**
databases. SQLite is what the tests and local development use; PostgreSQL is what
a hosted deployment uses, per D-001 and D-028. A schema that only works on the
first would pass the entire suite here and fail on the day `DATABASE_URL` changed.

So these tests do two things: pin the *shape* against the Phase 1-7 models (the
tables that must exist, the constraints that must hold, the ones that must not),
and compile the whole schema against the PostgreSQL dialect, which catches the
constructs SQLite tolerates and PostgreSQL rejects.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import UniqueConstraint, inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import Database
from app.db.types import UtcDateTime, as_utc
from app.models.investigation import (
    ClaimEntityLinkRow,
    ClaimRow,
    EntityRow,
    EvidenceItemRow,
    EvidenceResponseRow,
    InvestigationErrorRow,
    InvestigationRow,
    InvestigationWarningRow,
    RedFlagRow,
    RiskAssessmentRow,
    RiskFactorRow,
    SourceRow,
    TimelineEventRow,
    VerificationResultRow,
)

#: Every table Phase 9 declares. Listed explicitly rather than derived from
#: `Base.metadata`, so a table added by accident fails a test instead of being
#: silently accepted.
EXPECTED_TABLES = frozenset(
    {
        "investigations",
        "claims",
        "entities",
        "claim_entity_links",
        "red_flags",
        "verification_results",
        "sources",
        "evidence_responses",
        "evidence",
        "risk_assessments",
        "risk_factors",
        "timeline_events",
        "investigation_warnings",
        "investigation_errors",
    }
)


def test_the_declared_tables_are_exactly_the_expected_set() -> None:
    """No table is missing, and none was added without being decided on."""
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_deferred_tables_are_not_created(database: Database) -> None:
    """`reports` and `users` do not exist yet.

    The Phase 0 schema sketch described both. `reports` belongs to Phase 10 and
    `users` to authentication, and creating either now would mean either building
    a report layer ahead of its phase or committing to a user model nothing has
    been asked to design. An empty placeholder table is worse than none: it looks
    decided.
    """
    created = set(inspect(database.engine).get_table_names())

    assert "reports" not in created
    assert "users" not in created


def test_every_table_is_created_in_sqlite(database: Database) -> None:
    """SQLite materialises the whole schema, indexes included."""
    inspector = inspect(database.engine)

    assert set(inspector.get_table_names()) == EXPECTED_TABLES
    for table in EXPECTED_TABLES:
        assert inspector.get_indexes(table), f"{table} has no indexes"


def test_the_whole_schema_compiles_for_postgresql() -> None:
    """Every table and index renders as valid PostgreSQL DDL.

    Compiling against the dialect is the check that matters: it resolves the
    dialect-specific type names and rejects constructs PostgreSQL will not
    accept, without needing a server. A column type SQLite silently accepts and
    PostgreSQL rejects is exactly the defect that would otherwise surface only
    after deployment.
    """
    dialect = postgresql.dialect()

    for table in Base.metadata.sorted_tables:
        create = str(CreateTable(table).compile(dialect=dialect))
        assert create.upper().count("CREATE TABLE") == 1
        for index in table.indexes:
            rendered = str(CreateIndex(index).compile(dialect=dialect))
            assert rendered.startswith(("CREATE INDEX", "CREATE UNIQUE INDEX")), rendered


def test_no_column_uses_a_backend_specific_type() -> None:
    """Every column type is one both backends understand.

    `UtcDateTime` is the only custom type and it wraps plain `DateTime`; the rest
    are the portable core types. Asserted explicitly because a future table
    reaching for `JSONB`, `UUID` or a SQLite-only spelling would pass every test
    in this suite and fail the moment `DATABASE_URL` pointed at PostgreSQL.
    """
    portable = {
        "INTEGER",
        "VARCHAR",
        "TEXT",
        "FLOAT",
        "BOOLEAN",
        "JSON",
        "NUMERIC",
        # What `DateTime` compiles to on PostgreSQL. Spelled out rather than
        # aliased to "DATETIME" so the assertion states what the hosted database
        # will actually be handed.
        "TIMESTAMP WITHOUT TIME ZONE",
    }

    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            rendered = " ".join(
                column.type.compile(dialect=postgresql.dialect()).upper().split()
            )
            # Strip any length or precision, e.g. VARCHAR(64) -> VARCHAR.
            base = rendered.split("(", 1)[0].strip()
            assert base in portable, (
                f"{table.name}.{column.name} renders as {rendered} on PostgreSQL"
            )


def test_red_flags_are_not_unique_on_code_alone() -> None:
    """Two occurrences of one rule are two findings, not one.

    Phase 1 emits a separate `RedFlag` per span, each with its own `rf_` id, and
    Phase 6 cites those ids individually. The Phase 0 sketch's
    "one row per distinct indicator" would collapse them, destroying findings the
    risk trace points at. The span is part of the identity instead, because that
    is what `red_flag_id_for` hashes.
    """
    constraints = _unique_column_sets(RedFlagRow)

    assert ("investigation_id", "code") not in constraints
    assert ("investigation_id", "code", "span_start", "span_end") in constraints


def test_sources_are_keyed_by_source_and_result() -> None:
    """A document returned by two searches is two rows, not one.

    `source_id` names the document and `result_id` names the retrieval. Phase 5
    derives `ev_` ids from both, so collapsing them onto `source_id` alone would
    force one retrieval to borrow another's `result_id` — and every evidence item
    citing it would then report provenance that was never fetched.
    """
    constraints = _unique_column_sets(SourceRow)

    assert ("investigation_id", "source_id") not in constraints
    assert ("investigation_id", "source_id", "result_id") in constraints


def _unique_column_sets(model: type) -> set[tuple[str, ...]]:
    """Return the column tuples of every unique constraint and unique index.

    Both are included because SQLAlchemy renders a column declared
    `unique=True` as a unique *index* rather than a `UniqueConstraint`. Checking
    only one of the two would report the constraint missing while the database
    enforces it perfectly well.

    Args:
        model: A `Base` subclass.

    Returns:
        One tuple of column names per uniqueness rule, in declaration order.
    """
    from_constraints = {
        tuple(column.name for column in constraint.columns)
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    from_indexes = {
        tuple(column.name for column in index.columns)
        for index in model.__table__.indexes
        if index.unique
    }
    return from_constraints | from_indexes


def test_a_shared_public_id_does_not_collide(session: Session) -> None:
    """The same content investigated twice stores two runs, not one.

    `public_id` is a content digest, so two runs of identical text share it by
    construction — which is why nothing keys on it. The surrogate `id` is the
    only unique identity in the schema, and this asserts that a second run
    inserts cleanly rather than being rejected as a duplicate.
    """
    moment = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def make() -> InvestigationRow:
        return InvestigationRow(
            public_id="inv_shared",
            content_hash="inv_shared",
            input_type="TEXT",
            raw_input="text",
            extracted_text="text",
            status="COMPLETED",
            current_stage="completed",
            started_at=moment,
            completed_at=moment,
        )

    session.add_all([make(), make()])
    session.commit()

    stored = session.scalars(select(InvestigationRow)).all()
    assert len(stored) == 2
    assert {row.public_id for row in stored} == {"inv_shared"}
    assert len({row.id for row in stored}) == 2


def test_timestamps_are_aware_utc_on_both_backends() -> None:
    """`UtcDateTime` writes naive UTC and reads back aware UTC.

    SQLite has no timezone type, so a bare `DateTime` would return naive values
    there and aware ones on PostgreSQL. That asymmetry does not fail here — it
    fails much later, as `can't subtract offset-naive and offset-aware datetimes`
    in code that only runs on the hosted database.
    """
    aware = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    dialect = postgresql.dialect()
    column_type = UtcDateTime()

    stored = column_type.process_bind_param(aware, dialect)
    assert stored is not None and stored.tzinfo is None
    assert column_type.process_result_value(stored, dialect) == aware


def test_comparing_timestamps_ignores_the_offset_asymmetry() -> None:
    """An equality filter matches on SQLite and on PostgreSQL.

    SQLite round-trips through a naive value and PostgreSQL through an aware one.
    Comparing the raw stored attributes would make `WHERE created_at = :ts` hit on
    one backend and miss on the other — the same filter, a different answer, and
    no test on the SQLite path to catch it.
    """
    naive = datetime(2026, 1, 1, 12, 0)
    aware = naive.replace(tzinfo=timezone.utc)
    column_type = UtcDateTime()

    assert column_type.compare_values(naive, aware) is True
    assert column_type.compare_values(naive, naive.replace(year=2027)) is False


def test_naive_timestamps_are_read_as_utc_not_local_time() -> None:
    """A naive value is *assumed* UTC rather than localised.

    Every naive value this layer stores was converted from aware UTC on the way
    in, so interpreting it in the server's local zone would move timestamps by
    the machine's offset — and would make two deployments of the same application
    disagree about when a run happened.
    """
    value = as_utc(datetime(2026, 1, 1, 12, 0))

    assert value == datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert as_utc(None) is None


def test_no_column_can_hold_a_secret() -> None:
    """No table has a free-text column for an exception or a connection string.

    The safeguard is structural. Every string in this schema comes from a Phase
    1-7 schema field or from fixed wording; there is nowhere to put a driver's
    message, a DSN or a stack trace, so none can leak by accident later. This
    asserts the column names rather than trusting the convention to hold.
    """
    forbidden = {"stack_trace", "traceback", "dsn", "connection_string", "secret", "api_key"}

    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            assert column.name not in forbidden, (
                f"{table.name}.{column.name} is a channel for a secret"
            )


def test_warnings_do_not_store_an_exception_type() -> None:
    """`investigation_warnings` has no `error_type`; `investigation_errors` does.

    A warning is something a user may read, and Phase 8 already decided the
    log-side diagnostic does not cross that boundary. An error is our own defect
    being diagnosed, where the exception class name is the useful part and is
    not a secret. Storing the diagnostic in both would put it one round trip away
    from reappearing in a response.
    """
    warning_columns = {column.name for column in InvestigationWarningRow.__table__.columns}
    error_columns = {column.name for column in InvestigationErrorRow.__table__.columns}

    assert "error_type" not in warning_columns
    assert "error_type" in error_columns


def test_risk_assessments_have_no_caveat_column() -> None:
    """The caveat lives in `warnings`, and the model puts it there.

    `SCORE_NOT_A_PROBABILITY` is appended by `RiskAssessment` during validation,
    so storing it a second time in its own column would create a copy free to
    drift from the one the schema emits. Rebuilding the assessment through the
    model reproduces the caveat without needing a column to hold it (D-025).
    """
    columns = {column.name for column in RiskAssessmentRow.__table__.columns}

    assert "caveat" not in columns
    assert "warnings_json" in columns


def test_a_risk_assessment_is_unique_per_run() -> None:
    """A run has at most one assessment, and it is a real constraint.

    Two assessments for one run would make "what did this investigation score?"
    have two answers. The uniqueness is declared on the table rather than left as
    a convention, so the second insert fails loudly instead of depending on every
    caller remembering.
    """
    unique_column_sets = _unique_column_sets(RiskAssessmentRow)
    unique_indexes = {
        tuple(column.name for column in index.columns)
        for index in RiskAssessmentRow.__table__.indexes
        if index.unique
    }

    assert ("investigation_id",) in unique_column_sets | unique_indexes


@pytest.mark.parametrize(
    "model",
    [
        ClaimRow,
        EntityRow,
        ClaimEntityLinkRow,
        RedFlagRow,
        VerificationResultRow,
        SourceRow,
        EvidenceResponseRow,
        EvidenceItemRow,
        RiskFactorRow,
        TimelineEventRow,
        InvestigationWarningRow,
        InvestigationErrorRow,
    ],
)
def test_every_child_table_cascades_from_its_run(model: type) -> None:
    """Every child is removed with its investigation.

    A child that outlived its parent would be an orphan that no endpoint can
    reach and no query filters by — invisible, but also undeletable without
    knowing it exists. The cascade is declared in the schema so it does not
    depend on the ORM having loaded the collection first.

    `evidence` is excluded from the assertion because it has a second parent: an
    item belongs to both its run and its evidence response, and only the run
    cascade is universal.
    """
    foreign_keys = list(model.__table__.foreign_key_constraints)

    assert foreign_keys, f"{model.__tablename__} references nothing"
    for constraint in foreign_keys:
        assert constraint.ondelete == "CASCADE"


def test_ordered_collections_carry_an_explicit_sequence() -> None:
    """Every collection the API returns in order has a `sequence` column.

    The state carries tuples, and Phase 5's evidence ordering is a deterministic
    function the database cannot reproduce from the values it stores. Inferring
    order from a timestamp or an id would make a retrieved investigation list its
    findings in a different order than the live one, with nothing to detect it.
    """
    ordered = [
        ClaimRow,
        EntityRow,
        ClaimEntityLinkRow,
        RedFlagRow,
        VerificationResultRow,
        EvidenceResponseRow,
        EvidenceItemRow,
        RiskFactorRow,
        TimelineEventRow,
        InvestigationWarningRow,
        InvestigationErrorRow,
    ]

    for model in ordered:
        assert "sequence" in model.__table__.columns, f"{model.__tablename__} has no sequence"


def test_evidence_items_reach_the_run_through_their_response() -> None:
    """`evidence` is the one child with two parents, and both are declared.

    An item is reachable from its run directly and from the per-claim response
    that groups it. The second relationship is what lets the response be
    rehydrated with its items already ordered, and the first is what guarantees
    an item is deleted with its run even if the response was never loaded.
    """
    tables = {column.name for column in EvidenceItemRow.__table__.columns}
    foreign_key_tables = {
        foreign_key.column.table.name
        for constraint in EvidenceItemRow.__table__.foreign_key_constraints
        for foreign_key in constraint.elements
    }

    assert {"evidence_response_id", "investigation_id"} <= tables
    assert foreign_key_tables == {"evidence_responses", "investigations"}
    for constraint in EvidenceItemRow.__table__.foreign_key_constraints:
        assert constraint.ondelete == "CASCADE"
