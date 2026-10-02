# CURRENT_STATE — InvestShield AI

> **This is the most important file for session recovery.** Read it first.
> Update it at the end of every meaningful work session.

---

## Current Project State

**Current Phase:** Phase 9 — Database persistence — **COMPLETE**
**Current Subphase:** Phase 10 — Report generation — **NOT STARTED**
**Last Completed Task:** Phase 9 — the persistence layer. Fourteen tables in
`app/models/investigation.py` registered on `Base.metadata`; a repository in
`app/repositories/investigations.py` that round-trips a finished
`InvestigationState` through them and back into an `InvestigationState`; schema
creation in the `lifespan` handler; and two new endpoints,
`GET /api/investigations/{id}` and `GET /api/investigations`. The `POST`
endpoints now store what they return. The load-bearing property is that a
retrieved investigation is byte-identical to the live one, because both are
shaped by the same Phase 8 adapter from the same domain models — there is no
second rendering path that could disagree.
**Latest Commit:** `feat: implement investigation persistence` (Phase 9)
**Working Tree:** see `git status`.

### Phase Status Summary

| Phase | Scope | Status |
| --- | --- | --- |
| Phase 0 | Project foundation | **COMPLETE** |
| Phase 1 | Red flag engine | **COMPLETE** |
| Phase 2 | Claim & entity extraction | **COMPLETE** |
| Phase 3 | External services & search infrastructure | **COMPLETE (search only; see limitations)** |
| Phase 4 | Verification agent | **COMPLETE** |
| Phase 5 | Evidence engine | **COMPLETE** |
| Phase 6 | Risk engine | **COMPLETE** |
| Phase 7 | LangGraph orchestration | **COMPLETE** |
| Phase 8 | API layer (FastAPI endpoints) | **COMPLETE** |
| Phase 9 | Database models & repositories | **COMPLETE** |
| Phase 10 | Report generation | **NEXT** |

### Files Recently Changed

```
backend/app/models/__init__.py                   (new — registers every table on Base.metadata)
backend/app/models/investigation.py              (new — 14 tables)
backend/app/db/types.py                          (new — UtcDateTime for SQLite/PostgreSQL parity)
backend/app/repositories/__init__.py             (new)
backend/app/repositories/investigations.py       (new — state <-> rows, history, delete)
backend/app/api/routes/investigations.py         (+ 2 GET endpoints, stores on write)
backend/app/api/deps.py                          (+ get_repository_dep)
backend/app/api/errors.py                        (+ InvestigationNotFound, 404)
backend/app/schemas/api.py                       (+ InvestigationSummaryResponse, InvestigationListResponse)
backend/app/db/session.py                        (get_db reads app.state.database, not the global)
backend/app/main.py                              (lifespan creates the schema, tolerating failure)
backend/tests/conftest.py                        (+ 6 persistence fixtures)
backend/tests/persistence_factories.py           (new — content and run helpers)
backend/tests/db/__init__.py                     (new)
backend/tests/db/test_round_trip.py              (new)
backend/tests/db/test_history.py                 (new)
backend/tests/db/test_schema.py                  (new)
backend/tests/api/test_persistence_endpoints.py  (new)
docs/DATABASE_SCHEMA.md                          (rewritten to match the implemented schema)
docs/ARCHITECTURE.md / DECISIONS.md / DEVELOPMENT_LOG.md / CURRENT_STATE.md
```

### Tests Passing

```
cd backend && python -m pytest
2085 passed, 4 deselected
```

**Zero failures.** Baseline before Phase 9 was `1997 passed, 4 deselected`. Phase 9
added **88 tests** (`tests/db/`, `tests/api/test_persistence_endpoints.py`) and
modified **no** Phase 1-8 test.

### Tests Failing

None.

### Known Bugs

1. **Phase 7 declares `InvestigationState.completed_at` and no node ever writes
   it.** The Phase 8 response exposes the field and Phase 9 persists it, so it is
   stored and returned faithfully — but it is always `None`. This is a Phase 7
   gap, deliberately not fixed in Phase 9: inventing a finish time at storage time
   would be a claim the pipeline never made. Pinned by
   `test_run_timings_are_preserved_exactly_as_recorded`.
2. **Phase 4 discards the search responses Phase 5 needs.** Phase 7 works around
   this with `RecordingSearchService` rather than editing Phase 4 (D-031). The
   clean fix is for `VerificationService` to return its responses, which should be
   done if Phase 4 is ever reopened.
3. **Phase 1's `SUSPICIOUS_URL` description asserts the word "fraudulent".**
   Known, pinned by `TestPhaseOneCatalogueTripwire`, and a Phase 1 fix.

> The Phase 7 revision's Known Bug 1 — `/api/health` ignoring injected settings —
> was **fixed in Phase 8** and is now covered by
> `tests/api/test_health_boundary.py`.

### Blocked Items

None.

### Available Services

| Service | Status | Evidence |
| --- | --- | --- |
| `GROQ_API_KEY` | present but **not working** | a local `.env` supplies one; the LLM returns an error status and Phase 2 degrades to deterministic patterns, which it reports honestly |
| `SERPAPI_KEY` | **absent** | `SearchService.available = false`; verification degrades to `SEARCH_UNAVAILABLE` |
| SQLite | working | tests use per-test temporary files; `PRAGMA foreign_keys=ON` is set per connection |
| PostgreSQL | **not installed** | DDL is verified by compiling every table and index against the PostgreSQL dialect in `tests/db/test_schema.py`, but no live PostgreSQL run has been made |
| `httpx` | installed | used by `LLMService` and `SerpAPIProvider` |
| `langgraph` | **installed, 1.2.12** | `requirements.txt`; verified on CPython 3.14 |
| Tesseract binary | present, not on `PATH` | `resolve_tesseract_cmd()` finds it; `OCRService` not built |
| `pytesseract` / `Pillow` / PyMuPDF / `sentence-transformers` | **not installed** | deliberately deferred |

> A repository-root `.env` exists (gitignored, contains real credentials). It is
> **not** committed. `tests/graph/graph_factories.py`, `tests/api/conftest.py` and
> `tests/conftest.py` all pass `_env_file=None` so the suites stay offline
> regardless of what it contains.

### Phase 9 Architecture

```
POST /api/investigations/text ─┐
                                ├─► run_investigation ─► raise_for_graph_errors ─► repo.save() + commit
POST /api/investigations ─────┘                                                          │
                                                                                          ▼
                                                          14 tables, one transaction
                                                                                          │
GET /api/investigations/{id} ──► repo.load() ──► InvestigationState ─┐                    │
GET /api/investigations ──────► repo.list_page() ──► summaries (1 table)                   │
                                                                                          ▼
                                                        serialize_investigation() ──► 200
```

| Concern | Where |
| --- | --- |
| Table definitions | `app/models/investigation.py`, registered by `app/models/__init__.py` |
| State ⇄ rows mapping | `app/repositories/investigations.py` |
| Datetime portability | `app/db/types.py::UtcDateTime` — naive UTC in storage, aware UTC out |
| Session lifecycle | `get_db(request)` reads `app.state.database`; the route commits, never the dependency |
| Schema creation | `lifespan` calls `database.create_all()`, logging and continuing on failure |
| Response shaping | **unchanged** — `app/api/adapters.py::serialize_investigation` serves both paths |
| 404 on a missing id | `app/api/errors.py::InvestigationNotFound` |

### Phase 9 Design Decisions

| Decision | Rationale |
| --- | --- |
| The repository returns an `InvestigationState`, not a bespoke result type | Both the write and read paths then feed the same Phase 8 adapter. A `GET` that disagreed with the `POST` that stored the run would be worse than no retrieval (D-039) |
| `id` is the primary key; `public_id` is **not** unique | `investigation_id_for` digests the submitted content, so a unique constraint would make re-running content an error and would destroy the evidence that it was run twice (D-040) |
| Retrieval returns the newest run under a shared id | A client posts and then retrieves with the id it was just handed; returning the older run would show it a result disagreeing with the one it was just given |
| `red_flags` unique on `(investigation_id, code, span_start, span_end)` | Phase 1 emits one flag per span with its own `rf_` id and Phase 6 cites those ids. The sketch's "one row per indicator" would destroy findings the risk trace points at |
| `sources` keyed on `(investigation_id, source_id, result_id)` | Phase 5 derives `ev_` ids from both. Keying on `source_id` alone would force one retrieval to borrow another's `result_id` (D-023) |
| No `caveat` column on `risk_assessments` | The caveat is `SCORE_NOT_A_PROBABILITY`, which `RiskAssessment` appends to `warnings` during validation. A second column would be a copy free to drift from the one the model emits (D-025) |
| `investigation_warnings` has no `error_type` | A warning is something a user may read; Phase 8 already established that the log-side diagnostic does not cross that boundary |
| `investigation_errors` stores `error_type` but never a message | An error diagnoses our own defect and a class name is not a secret; driver text routinely embeds a DSN (D-024) |
| No `users` and no `reports` tables | `reports` is Phase 10; an empty placeholder table looks decided when nothing has been asked to design it |
| Seven denormalised counts on `investigations` | Listing a hundred runs is one indexed query instead of a thousand joins. Pinned by a test so a count cannot drift from its children |
| `sequence` on every ordered collection | Phase 5's ordering is a function the database cannot reproduce; inferring it would list a retrieved run's findings in a different order with nothing to detect it |
| `create_all()` at startup, failures tolerated | A database that is reachable but not writable must not stop the read-only endpoints. `create_all` is idempotent, so this is safe on every boot |
| A storage failure is a `500`, not a swallowed error | Returning `200` while discarding the result would tell a client its investigation is safe to look up later when it is not |
| The Phase 8 response shape is unchanged | A client written against Phase 8 keeps working. No `persisted` flag: the id was already there and is what the client uses |

### Known Limitations (Phase 9)

- **No authentication, therefore no ownership check.** Anyone holding an id can
  read that investigation. Acceptable only because there is nothing to
  authenticate against; it must be revisited before there is.
- **No `DELETE` endpoint.** The repository exposes `delete()`, which removes every
  run under a public id, but no route calls it.
- **No migrations.** `create_all()` creates missing tables and will **not** add a
  column to an existing one. Fine while the schema is still changing and the
  database is disposable; Alembic is the right tool the first time a deployed
  database has to be migrated in place. Recorded in `docs/DATABASE_SCHEMA.md`
  rather than glossed over.
- **No live PostgreSQL run.** DDL compiles against the PostgreSQL dialect and
  `UtcDateTime` handles the offset asymmetry, but no test has connected to a real
  PostgreSQL server. `SERPAPI_KEY` is absent and there is no database URL for one.
- **`limit`/`offset` paging only.** Fine at this scale; offset paging would need
  replacing for a table large enough for it to hurt.
- **`limitations` is still a flat code array** with no per-claim attribution, so a
  limitation cannot be joined back to the claim it affected. Phase 10 may need a
  junction table.
- **`completed_at` is always `NULL`** — see Known Bugs.
- **Only `TEXT` input is analysed.** `URL`, `IMAGE` and `PDF` are recognised and
  refused with `422`.

### Environment Reality Check (verified)

| Capability | Status | Evidence |
| --- | --- | --- |
| `langgraph` | **1.2.12, installed** | `python -m pip show langgraph`; graph compiles and runs |
| `sqlalchemy` | **installed** | 14 tables create on SQLite; all DDL compiles for PostgreSQL |
| `GROQ_API_KEY` | present but rejected by the API | extraction falls back, reports `EXTRACTION_FALLBACK` |
| `SERPAPI_KEY` | **absent** | `SearchService.available = false` |
| Tesseract binary | present, not on `PATH` | `resolve_tesseract_cmd()` |
| `pytesseract` / `Pillow` / PyMuPDF | not installed | deferred |
| `sentence-transformers` | not installed | deferred |
| Repository-root `.env` | **exists, gitignored** | holds real credentials; no longer breaks the suite |
| `ruff` / `mypy` | **not installed, not configured** | this project has no linter or typechecker step; import hygiene was checked by hand |

### Important Decisions

Full rationale lives in [`DECISIONS.md`](DECISIONS.md). The ones that most affect
the next phase:

| ID | Decision |
| --- | --- |
| D-006 | Absence of evidence is never an accusation |
| D-009 | Every external service degrades into a typed error code surfaced as a Limitation |
| D-020 | Verification reports a claim's factual status, never a verdict |
| D-023 | Evidence never re-decides; it cannot make a finding easier |
| D-025 | The risk score is a heuristic indicator sum, never a probability |
| D-030 | The graph is the orchestrator and owns no business logic |
| D-032 | A recorded error ends the run; a recorded limitation does not |
| D-034 | Determinism is asserted on the semantic view, not on the clock |
| D-036 | Phase 8 returns the whole investigation synchronously; no job handle |
| D-037 | Run status is derived from the stage timeline, never from the warning list |
| D-038 | The API embeds domain models rather than re-projecting them |
| D-039 | The API layer interprets; it never computes a score, threshold or verdict |
| D-040 | `public_id` is a content fingerprint, so a surrogate key owns identity |
| D-041 | Persistence returns an `InvestigationState`; the adapter shapes both paths |
| D-042 | Order is stored, never inferred from a value the database already stores |

---

## Next Exact Task

**Phase 10 — Report generation.**

1. Build the report layer over a *stored* investigation. The retrieval path
   `Phase 9` established — `repo.load()` returning an `InvestigationState` — is
   the input a report generator needs, and it is the first consumer that reads an
   investigation some time after the run that produced it.
2. A report renders evidence and indicators. It must not introduce a probability,
   a verdict or advice, and it must not restate `risk_score` as a likelihood
   (D-025). The `RiskAssessment` caveat has to survive into the rendered text.
3. Decide where reports live. `docs/DATABASE_SCHEMA.md` sketches a `reports` table;
   it was deliberately **not** created in Phase 9, so this is an open decision
   rather than a continuation of existing work.
4. Rendering is multilingual in intent — `Language` accepts `en`/`hi`/`mr` and the
   value is already stored on the run — but Phase 10 should still ship English
   only and record the requested language, as Phase 8 did.
5. A report that cites a risk factor must be able to walk to the claim, red flag
   and source behind it. Phase 9 stored that trace, so this is a read, not a
   reconstruction.

### Do not start before Phase 10 is green

- No frontend (Phase 11+).

---

## Recovery Checklist

If you are reading this in a fresh session:

1. [x] Read `docs/CURRENT_STATE.md` (this file)
2. [ ] Read `docs/IMPLEMENTATION_PLAN.md`
3. [ ] Read `docs/ARCHITECTURE.md` (§2.3d for the graph, §2.3e for the API,
       §2.3f for persistence) and `docs/DECISIONS.md` (D-030…D-042)
4. [ ] Read `docs/DATABASE_SCHEMA.md` and the last entry in `docs/DEVELOPMENT_LOG.md`
5. [ ] Run `git status` and `git log --oneline -5`
6. [ ] Run `cd backend && python -m pytest` — expect **2085 passed, 4 deselected,
       0 failed**
7. [ ] Execute **Next Exact Task**