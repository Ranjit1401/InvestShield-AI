# CURRENT_STATE — InvestShield AI

> **This is the most important file for session recovery.** Read it first.
> Update it at the end of every meaningful work session.

---

## Current Project State

**Current Phase:** Phase 8 — API layer — **COMPLETE**
**Current Subphase:** Phase 9 — Database models & repositories — **NOT STARTED**
**Last Completed Task:** Phase 8 — the HTTP surface over the Phase 7 graph, plus
the dependency-boundary fix that had been holding the suite red. Request and
response contracts in `app/schemas/api.py`; a state-to-response adapter; three
endpoints (`POST /api/investigations/text`, `POST /api/investigations`,
`GET /api/investigations/limits`); a typed error taxonomy mapping graph failures
onto 422 versus 500; and the graph warnings surfaced as a deduplicated
`limitations` code array alongside readable messages. `/api/health` now probes a
`Database` built from the settings the app was given, and the database probe no
longer returns a driver message. The suite is fully green for the first time.
**Latest Commit:** `feat: implement investigation API layer` (Phase 8)
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
| Phase 8 | API layer (FastAPI endpoints) | **COMPLETE** |
| Phase 9 | Database models & repositories | **NEXT** |

### Files Recently Changed

```
backend/app/schemas/api.py                     (new — request/response contracts)
backend/app/api/adapters.py                    (new — InvestigationState -> response)
backend/app/api/errors.py                      (new — typed failures, 422 vs 500 mapping)
backend/app/api/routes/investigations.py       (new — three endpoints)
backend/app/api/routes/health.py               (probes app.state.database, not a global)
backend/app/api/routes/__init__.py             (+ investigations_router)
backend/app/api/deps.py                        (+ get_database_dep, get_graph_context_dep)
backend/app/main.py                            (Database + graph context on app.state)
backend/app/services/capabilities.py           (probe_database no longer leaks str(exc))
backend/tests/api/conftest.py                  (new — offline client fixtures)
backend/tests/api/test_investigation_endpoints.py
backend/tests/api/test_api_adapters.py
backend/tests/api/test_api_errors.py
backend/tests/api/test_api_schemas.py
backend/tests/api/test_health_boundary.py      (regression tests for the health fix)
docs/ARCHITECTURE.md / AI_PIPELINE.md / API_SPEC.md / DECISIONS.md
docs/IMPLEMENTATION_PLAN.md / DEVELOPMENT_LOG.md / CURRENT_STATE.md / README.md
```

### Tests Passing

```
cd backend && python -m pytest
1997 passed, 4 deselected
```

**Zero failures.** Baseline before Phase 8 was `1882 passed, 1 failed`. Phase 8
added **114 tests** (`tests/api/`) and fixed the one outstanding failure. No
Phase 1-7 test was modified or removed.

### Tests Failing

None.

### Known Bugs

1. **Phase 4 discards the search responses Phase 5 needs.** Phase 7 works around
   this with `RecordingSearchService` rather than editing Phase 4 (D-031). The
   clean fix is for `VerificationService` to return its responses, which should be
   done if Phase 4 is ever reopened.
2. **Phase 1's `SUSPICIOUS_URL` description asserts the word "fraudulent".**
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
| SQLite | working | used by the tests via `sqlite:///:memory:` and per-test tmp files |
| `httpx` | installed | used by `LLMService` and `SerpAPIProvider` |
| `langgraph` | **installed, 1.2.12** | `requirements.txt`; verified on CPython 3.14 |
| Tesseract binary | present, not on `PATH` | `resolve_tesseract_cmd()` finds it; `OCRService` not built |
| `pytesseract` / `Pillow` / PyMuPDF / `sentence-transformers` | **not installed** | deliberately deferred |

> A repository-root `.env` exists (gitignored, contains real credentials). It is
> **not** committed. Both `tests/graph/graph_factories.py` and
> `tests/api/conftest.py` pass `_env_file=None` so the suites stay offline
> regardless of what it contains.

### Phase 8 Architecture

```
POST /api/investigations/text ─┐
                               ├─► validate ─► run_investigation ─► raise_for_graph_errors ─► serialize ─► 200
POST /api/investigations ─────┘                                   │                │
                                                                   │                └─ absent keys become empty lists;
                                                                   │                   status read off the timeline
                                                                   └─ only when an ERROR was recorded:
                                                                      INPUT_* → 422, anything else → 500
```

| Concern | Where |
| --- | --- |
| Request/response contracts | `app/schemas/api.py`, `extra="forbid"` on every body |
| State to response | `app/api/adapters.py::serialize_investigation` |
| Run status | `investigation_status()` — read off `TimelineStatus`, never off the warning list |
| Limitation codes | `dedupe_codes()` — first-seen order, one entry per code |
| Error taxonomy | `app/api/errors.py`; `ApiError` handler registered in `app/main.py` |
| Dependency injection | `get_database_dep`, `get_graph_context_dep`, both read `app.state` |
| Offline testing | `app.dependency_overrides[get_graph_context_dep]` with a fake context |

### Phase 8 Design Decisions

| Decision | Rationale |
| --- | --- |
| The run returns synchronously and whole; no `202` and no job handle | Persistence is Phase 9. Returning an id with nothing behind it invites a lookup that cannot succeed (D-036) |
| `status` is derived from the stage timeline, not from the warnings | `NO_RED_FLAGS_DETECTED` is recorded on clean content; treating any warning as a degradation would report every benign submission as a partial run (D-037) |
| A degraded run is a `200` with `status: PARTIAL` | A search provider being down says nothing about the request; failing it makes the product look broken while it is being truthful (D-009) |
| Domain models are embedded, not re-projected | A second definition of "a claim" in the API would drift from Phase 2's (D-038) |
| Warnings carry no `error_type` | It is a log-side diagnostic and must not appear in a body a user may read |
| 422 for caller faults, 500 for contract violations | Only the second is our defect; softening it would dress a bug up as a normal outcome |
| `RiskAssessment` forwarded verbatim | The heuristic indicator count must not be restated as a probability on its way out (D-025) |
| `language` accepted and echoed, not honoured | Keeps the contract stable when Phase 15 ships translations without pretending Phase 8 translates |

### Known Limitations (Phase 8)

- **Only `TEXT` input is analysed.** `URL`, `IMAGE` and `PDF` are recognised and
  refused with `422`, and the error detail names the kinds that do work. There is
  no ingestion stage yet.
- **No persistence.** The run is returned in memory; there is no
  `GET /api/investigations/{id}` and nothing to list. Phase 9.
- **`investigation_id` is a digest of the input**, so two different investigations
  of identical content share an id. That is what makes a re-run comparable, but it
  means the id is not unique per investigation. Phase 9 will need a real id.
- **`extraction` is not exposed.** The raw Phase 2 result stays internal; claims,
  entities, red flags, verification, evidence and the assessment are forwarded.
- **No report layer.** The response is evidence and indicators, not prose. Phase 10.
- **No translation.** `language` is recorded and echoed; every `message` is English.
- **`limitations` is a flat code array with no per-claim attribution.** A client
  that needs to know *which* claim went unchecked must read `verification_results`.
- **No live SerpAPI run.** `SERPAPI_KEY` is absent, so real ranking and snippet
  quality remain unverified assumptions. The end-to-end API tests run against the
  genuine Phase 3, 4 and 5 services with a fixture search provider.
- **No LLM-backed extraction has succeeded.** The local `.env` supplies a key the
  API rejects, so Phase 2 degrades to deterministic patterns in every manual run.
  That path is honest — it reports `EXTRACTION_FALLBACK` — but the model-assisted
  path is untested against a live model.

### Environment Reality Check (verified)

| Capability | Status | Evidence |
| --- | --- | --- |
| `langgraph` | **1.2.12, installed** | `python -m pip show langgraph`; graph compiles and runs |
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

---

## Next Exact Task

**Phase 9 — Database models & repositories.**

1. Build the ORM models and repositories behind `Database`, which Phase 8 now
   parks on `app.state.database` and disposes on shutdown. That lifecycle is the
   seam Phase 9 plugs into — see `app/db/session.py` and the `lifespan` handler in
   `app/main.py`.
2. `investigation_id` is currently a digest of the input, so two runs of the same
   content share an id. Phase 9 needs a real unique id, and Phase 8's response
   field is where it will surface.
3. Decide what persists: the investigation and its stages, and which of the Phase
   1-6 objects are stored whole versus by reference. `docs/DATABASE_SCHEMA.md`
   already sketches this.
4. The synchronous POST does not need to change when persistence lands. Add
   `GET /api/investigations/{id}` and a list endpoint; keeping the POST returning
   the whole investigation means a client that persists nothing still works.
5. Do not let persistence introduce a verdict, a probability or advice.

### Do not start before Phase 9 is green

- No report generation (Phase 10), no frontend (11+).

---

## Recovery Checklist

If you are reading this in a fresh session:

1. [x] Read `docs/CURRENT_STATE.md` (this file)
2. [ ] Read `docs/IMPLEMENTATION_PLAN.md`
3. [ ] Read `docs/ARCHITECTURE.md` (§2.3d for the graph, §2.3e for the API) and
       `docs/DECISIONS.md` (D-030…D-039)
4. [ ] Read the last entry in `docs/DEVELOPMENT_LOG.md`
5. [ ] Run `git status` and `git log --oneline -5`
6. [ ] Run `cd backend && python -m pytest` — expect **1997 passed, 4 deselected,
       0 failed**
7. [ ] Execute **Next Exact Task**