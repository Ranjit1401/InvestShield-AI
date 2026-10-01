# CURRENT_STATE — InvestShield AI

> **This is the most important file for session recovery.** Read it first.
> Update it at the end of every meaningful work session.

---

## Current Project State

**Current Phase:** Phase 0 — Project foundation — **COMPLETE**
**Current Subphase:** Phase 1 — Red Flag Engine — **NOT STARTED**
**Last Completed Task:** Phase 0 verification — full test suite green (43
passing), backend booted under Uvicorn, `GET /api/health` returning `ok`.
**Currently Working On:** Idle. Awaiting instruction to begin Phase 1.
**Phase Started:** — (Phase 1 not yet started)

### Files Recently Changed

```
backend/app/api/deps.py                     (settings dependency now reads app.state)
backend/app/main.py                         (app.state.settings, trimmed lifespan)
backend/app/api/routes/health.py            (Depends-based settings injection)
backend/tests/test_config.py                (rewritten: OS-env isolation)
backend/tests/test_health.py                (rewritten: sentinel-credential leak test)
backend/tests/test_db.py                    (create_all assertions corrected)
docs/CURRENT_STATE.md                       (this file)
docs/DEVELOPMENT_LOG.md                     (Phase 0 entry)
docs/IMPLEMENTATION_PLAN.md                 (Phase 0 marked complete)
```

### Tests Passing

```
cd backend && python -m pytest
43 passed
```

Coverage areas: config loading & derived settings, OS-env isolation, health
endpoint contract, secret non-leakage, database engine/session/pragma
behaviour, capability probes.

### Tests Failing

None.

### Known Bugs

None open. Five failures were found and fixed during Phase 0 verification; see
`DEVELOPMENT_LOG.md` for the list (settings dependency bound to the global
rather than the running app, over-strict secret-leak assertion, and a
`create_all()` assertion written against an empty table set).

### Blocked Items

None.

### Environment Reality Check (verified live)

| Capability | Status | Evidence |
| --- | --- | --- |
| `GROQ_API_KEY` | **present** in OS environment | `/api/health` → `services.llm.configured = true` |
| `SERPAPI_KEY` | **absent** | `/api/health` → `services.search.configured = false` |
| Tesseract binary | present at `C:\Program Files\Tesseract-OCR\tesseract.exe`, not on `PATH` | resolved by `resolve_tesseract_cmd()` |
| `pytesseract` / `Pillow` | not installed | install in Phase 3 |
| PyMuPDF (`fitz`) | not installed | install in Phase 3 |
| `sentence-transformers` | not installed | install in Phase 3 |
| `langgraph` / `langchain-groq` | not installed | install in Phase 7 / Phase 3 |
| SQLite | working | `/api/health` → `database.connected = true` |

**Consequence for Phase 4:** without `SERPAPI_KEY`, claim verification cannot run
live. The verification agent must still be built and unit-tested against a fake
search provider, and the no-search path must produce a valid report stating that
external verification could not be completed.

### Important Decisions

Full rationale lives in [`DECISIONS.md`](DECISIONS.md). The ones that most affect
the next phase:

| ID | Decision |
| --- | --- |
| D-003 | NumPy cosine similarity is the default `VectorStore`; FAISS is optional (no cp314 wheel) |
| D-004 | Groq behind `LLMService` with a `NullProvider` that never raises |
| D-005 | SerpAPI behind `SearchService`; results are normalised and tiered |
| D-006 | Absence of evidence is never an accusation — `CONTRADICTED` requires direct authoritative contradiction |
| D-007 | Risk is a transparent weighted heuristic, never a probability |
| D-008 | Red-flag detection is deterministic, tested, rule-based code — not LLM inference |
| D-009 | Every external service degrades into a typed error code surfaced in report Limitations |
| D-010 | Pydantic API schemas are separate from SQLAlchemy models |
| D-011 | LangGraph nodes are thin coordinators; logic lives in testable services |

---

## Next Exact Task

**Phase 1 — RedFlagEngine.**

1. Create `backend/app/services/red_flag_rules.py` holding the 15 rule
   definitions from `PROJECT_CONTEXT.md` §18, each with
   `code`, `name`, `description`, `severity`, `default_weight`, and regex
   patterns.
2. Create `backend/app/services/red_flag_engine.py`:
   - `detect(text: str, settings: Settings | None = None) -> list[RedFlag]`
   - captures the exact matched evidence span and its offsets
   - dedupes by rule code, keeping the strongest occurrence
   - reads weights from settings (`risk_weight_*`) so they stay configurable
   - returns red flags sorted by severity then weight
3. Create `backend/app/schemas/investigation.py` with the Pydantic
   `RedFlag` / `Severity` models.
4. Write `backend/tests/test_red_flag_engine.py` covering:
   - the synthetic demo input → all 8 expected indicators
   - each rule firing in isolation
   - **false-positive guards** (§43): `"High returns are possible"` must NOT
     trigger `GUARANTEED_RETURN`; `"We offer long-term mutual funds"` must not
     trigger anything; a benign financial-information text must produce zero or
     near-zero indicators
   - weights come from settings when overridden
   - evidence spans are non-empty and appear verbatim in the input
5. Run `python -m pytest`, fix failures.
6. Update `DEVELOPMENT_LOG.md`, `IMPLEMENTATION_PLAN.md`, and this file.
7. Commit: `feat: implement red flag engine` (+ `test: add red flag engine tests`).

### Do not start before Phase 1 is green

- Phase 2 (claim/entity extraction) — needs the `Claim` schema, which depends on
  the `RedFlag` schema created in step 3.
- No LLM calls, no LangGraph, no frontend, no auth.

---

## Recovery Checklist

If you are reading this in a fresh session:

1. [x] Read `docs/CURRENT_STATE.md` (this file)
2. [ ] Read `docs/IMPLEMENTATION_PLAN.md`
3. [ ] Read `docs/ARCHITECTURE.md` and `docs/DECISIONS.md`
4. [ ] Read the last entry in `docs/DEVELOPMENT_LOG.md`
5. [ ] Run `git status` and `git log --oneline -10`
6. [ ] Run `cd backend && python -m pytest`
7. [ ] Confirm the test count still matches "Tests Passing" above
8. [ ] Execute **Next Exact Task**
