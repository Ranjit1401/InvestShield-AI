# CURRENT_STATE — InvestShield AI

> **This is the most important file for session recovery.** Read it first.
> Update it at the end of every meaningful work session.

---

## Current Project State

**Current Phase:** Phase 7 — LangGraph Orchestration — **COMPLETE**
**Current Subphase:** Phase 8 — API layer — **NOT STARTED**
**Last Completed Task:** Phase 7 — a LangGraph graph connecting Phases 1-6 into
one investigation, owning order and bookkeeping only: a typed state carrying the
Phase 1-6 objects themselves, six nodes that each call exactly one service,
dependency injection through LangGraph's runtime context, structured warnings and
errors, a stage timeline for the future UI, and a uniform rule that a recorded
error ends the run while a recorded limitation never does. No Phase 1-6 file was
modified.
**Latest Commit:** `feat: implement LangGraph investigation orchestration` (Phase 7)
**Working Tree:** clean.

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
| Phase 8 | API layer (FastAPI endpoints) | **NEXT** |

### Files Recently Changed

```
backend/app/graph/__init__.py                 (new — public surface)
backend/app/graph/state.py                    (new — InvestigationState, stage/timeline vocabulary)
backend/app/graph/context.py                  (new — GraphDependencies, GraphContext, RecordingSearchService)
backend/app/graph/nodes.py                    (new — six stage nodes, fixed message tables)
backend/app/graph/edges.py                    (new — route_on_recorded_error)
backend/app/graph/investigation_graph.py      (new — build_investigation_graph, run_investigation)
backend/app/scripts/manual_graph.py           (new — offline three-pass demo)
backend/tests/graph/graph_factories.py        (new — recording fakes + real offline builders)
backend/tests/graph/test_graph_construction.py
backend/tests/graph/test_graph_state.py
backend/tests/graph/test_graph_recorder.py
backend/tests/graph/test_graph_nodes.py
backend/tests/graph/test_graph_execution.py
backend/tests/graph/test_graph_safety.py
backend/tests/graph/test_graph_package.py
backend/requirements.txt                      (+ langgraph==1.2.12)
docs/ARCHITECTURE.md / AI_PIPELINE.md / API_SPEC.md / DECISIONS.md
docs/IMPLEMENTATION_PLAN.md / DEVELOPMENT_LOG.md / PROJECT_CONTEXT.md
```

### Tests Passing

```
cd backend && python -m pytest
1882 passed, 4 deselected, 1 failed
```

**The one failure is `tests/test_health.py::test_health_reports_database_state`,
and it is pre-existing and environment-caused, not a Phase 7 regression.** See
Known Bugs item 1.

Baseline before Phase 7 was `1592 passed, 4 deselected`. Phase 7 added **291
tests** (`tests/graph/`). No Phase 1-6 test was modified or removed.

### Tests Failing

One, and it is not caused by Phase 7:

1. **`test_health_reports_database_state`** — asserts `/api/health` reports the
   `sqlite` dialect, but the route probes the **module-level** `engine` in
   `app/db/session.py`, which is built from `get_settings()` rather than from the
   settings injected into `create_app`. A repository-root `.env` setting
   `DATABASE_URL` to PostgreSQL makes that engine PostgreSQL regardless of how
   the app was constructed.

   **Evidence that Phase 7 did not cause it:**
   `python -m pytest --ignore=tests/graph` → `1591 passed, 1 failed` — the same
   single failure with every Phase 7 test excluded.

   **Left unfixed deliberately.** The correct fix belongs to the API layer
   (Phase 8), which is where the health route's database resolution should be
   decided. Patching it from here would scope-creep into a completed phase. The
   local `.env` was also not modified: it holds real credentials.

### Known Bugs

1. **`/api/health` ignores injected settings for its database probe** — see
   Tests Failing above. Phase 0/8 scope.
2. **Phase 4 discards the search responses Phase 5 needs.** Phase 7 works around
   this with `RecordingSearchService` rather than editing Phase 4 (D-031). The
   clean fix is for `VerificationService` to return its responses, which should be
   done if Phase 4 is ever reopened.
3. **Phase 1's `SUSPICIOUS_URL` description asserts the word "fraudulent".**
   Known, pinned by `TestPhaseOneCatalogueTripwire`, and a Phase 1 fix.

### Blocked Items

None.

### Available Services

| Service | Status | Evidence |
| --- | --- | --- |
| `GROQ_API_KEY` | present but **not working** | a local `.env` now supplies one; the LLM returns an error status and Phase 2 degrades to deterministic patterns, which it reports honestly |
| `SERPAPI_KEY` | **absent** | `SearchService.available = false`; verification degrades to `SEARCH_UNAVAILABLE` |
| SQLite | working | used by the Phase 7 tests via `sqlite:///:memory:` |
| `httpx` | installed | used by `LLMService` and `SerpAPIProvider` |
| `langgraph` | **installed, 1.2.12** | `requirements.txt`; verified on CPython 3.14 |
| Tesseract binary | present, not on `PATH` | `resolve_tesseract_cmd()` finds it; `OCRService` not built |
| `pytesseract` / `Pillow` / PyMuPDF / `sentence-transformers` | **not installed** | deliberately deferred |

> A repository-root `.env` now exists (gitignored, contains real credentials).
> It appeared during the Phase 7 session and is the cause of Known Bug 1. It is
> **not** committed. `tests/graph/graph_factories.py` passes `_env_file=None` so
> the graph tests stay offline regardless of what it contains.

### Phase 7 Architecture

```
START ─► input ─┬─(error)─► END
                └─(ok)────► extraction ─► red_flags ─► verification ─► evidence ─► risk ─► END
```

| Concern | Where |
| --- | --- |
| State | `InvestigationState` (`TypedDict`), `warnings`/`errors`/`timeline` carry `operator.add` reducers |
| Stages | `GraphStage` enum, doubling as node names and timeline labels |
| Timeline | `TimelineEvent(stage, status, message, at)`; status ∈ STARTED / COMPLETED / PARTIAL / FAILED / SKIPPED |
| Warnings | `GraphWarning(code, stage, message, error_type)` with fixed wording |
| Errors | `GraphError(code, stage, message, error_type)`; any entry ends the run |
| Dependency injection | `GraphDependencies` (frozen) + `GraphContext` via LangGraph `context_schema` |
| Search seam | `RecordingSearchService` wraps `SearchService`; the graph issues no search |
| Determinism | `semantic_view` + `TIMESTAMP_FIELDS`; `GraphContext.clock` injectable |
| Entry point | `run_investigation(raw_input, input_type=..., context=...)` |
| Factory | `build_investigation_graph()` — a fresh compiled graph per call |

### Phase 7 Design Decisions

| Decision | Rationale |
| --- | --- |
| Nodes call one service and return its output | Keeps the boundary between *what was found* and *what runs in what order* (D-030) |
| `RecordingSearchService`, not a second search pass | Duplicating searches could present evidence the verification never saw (D-031, D-023) |
| One error predicate guards every stage | A failed red-flag pass that reports "no patterns found" understates risk (D-032) |
| Evidence failure degrades instead of stopping | Phase 6 established evidence carries no weight, only provenance ids |
| `operator.add` reducers on the three accumulators | Appending becomes a schema property, not a per-node convention (D-033) |
| Timestamp names listed, not detected by type | A real datetime finding (a claim's publication date) must not be discarded with the metadata (D-034) |
| All four input types declared, three refused | OCR/PDF/image are out of scope; silently analysing a path as text would be dishonest (D-035) |

### Known Limitations (Phase 7)

- **Only `TEXT` input is analysed.** `URL`, `IMAGE` and `PDF` are recognised and
  refused with a typed reason. There is no ingestion stage yet.
- **No live SerpAPI run.** `SERPAPI_KEY` is absent, so real ranking and snippet
  quality remain unverified assumptions. The recorder is proven against a fixture
  provider with the genuine Phase 3, 4 and 5 services.
- **No LLM-backed extraction has succeeded.** A local `.env` supplies a key that
  the API rejects, so Phase 2 degrades to deterministic patterns in every manual
  run. That path is honest — it reports `EXTRACTION_FALLBACK` — but the
  model-assisted path is untested against a live model.
- **No persistence, no report, no endpoint.** The result is returned in memory.
- **`semantic_view` is how determinism is asserted.** Whole states never compare
  equal because Phases 3, 5 and 6 stamp their own timestamps.
- **The timeline is metadata only.** No natural-language stage explanations, by
  design — see the D-035 and D-030 notes on vocabulary.
- **`investigation_id` is a digest of the input**, so two different investigations
  of identical content share an id. That is what makes a re-run comparable, but it
  means the id is not unique per investigation. Phase 9 will need a real id.

### Environment Reality Check (verified)

| Capability | Status | Evidence |
| --- | --- | --- |
| `langgraph` | **1.2.12, installed** | `python -m pip show langgraph`; graph compiles and runs |
| `GROQ_API_KEY` | present but rejected by the API | extraction falls back, reports `EXTRACTION_FALLBACK` |
| `SERPAPI_KEY` | **absent** | `SearchService.available = false` |
| Tesseract binary | present, not on `PATH` | `resolve_tesseract_cmd()` |
| `pytesseract` / `Pillow` / PyMuPDF | not installed | deferred |
| `sentence-transformers` | not installed | deferred |
| Repository-root `.env` | **exists, gitignored** | cause of Known Bug 1; holds real credentials |

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
| D-031 | Phase 4's searches are recorded, not repeated |
| D-032 | A recorded error ends the run; a recorded limitation does not |
| D-033 | State accumulates structurally; services are injected through a context |
| D-034 | Determinism is asserted on the semantic view, not on the clock |
| D-035 | Only text is analysed in Phase 7; other input types are refused |

---

## Next Exact Task

**Phase 8 — API layer (FastAPI endpoints).**

1. Expose the investigation pipeline over HTTP. `run_investigation` is the library
   entry point and is already usable from a route; Phase 8 wraps it, not rewrites
   it. See `docs/API_SPEC.md` for the intended resource shapes.
2. **Decide the `/api/health` database question first** (Known Bug 1). The route
   currently probes a module-level engine built from `get_settings()` rather than
   from the request's settings. Fixing it belongs here, not in Phase 7.
3. `GraphWarning.code` and `GraphError.code` are stable strings. Surface them as
   the machine-readable `limitations` array rather than flattening them to prose.
4. Carry `RiskAssessment`'s standing caveat through the response unchanged. The
   score is a heuristic indicator count, never a probability (D-025).
5. `InvestigationState` holds Pydantic models and is serialisable. Decide whether
   the endpoint returns the whole state or a projection; the whole state is large
   and includes `extraction`, which a client has no use for.
6. No persistence yet (Phase 9). A Phase 8 endpoint must work in-memory, so think
   about what happens when Phase 9 lands rather than designing around it.
7. Do not let the API layer introduce a verdict, a probability or advice. The
   vocabulary ban in `tests/graph/test_graph_safety.py` and
   `tests/test_risk_safety.py` must keep passing unchanged.

### Do not start before Phase 8 is green

- No persistence (Phase 9), no report generation (Phase 10), no frontend (11+).

---

## Recovery Checklist

If you are reading this in a fresh session:

1. [x] Read `docs/CURRENT_STATE.md` (this file)
2. [ ] Read `docs/IMPLEMENTATION_PLAN.md`
3. [ ] Read `docs/ARCHITECTURE.md` (§2.3d for the graph) and `docs/DECISIONS.md` (D-030…D-035)
4. [ ] Read the last entry in `docs/DEVELOPMENT_LOG.md`
5. [ ] Run `git status` and `git log --oneline -5`
6. [ ] Run `cd backend && python -m pytest` — expect **1882 passed, 4 deselected, 1 failed**
      (the one failure is Known Bug 1, pre-existing)
7. [ ] Execute **Next Exact Task**