# CURRENT_STATE — InvestShield AI

> **This is the most important file for session recovery.** Read it first.
> Update it at the end of every meaningful work session.

---

## Current Project State

**Current Phase:** Phase 4 — Verification Agent — **COMPLETE**
**Current Subphase:** Phase 5 — Evidence Engine — **NOT STARTED**
**Last Completed Task:** Phase 4 — `VerificationService` with a deterministic
decision table over Phase 3 search results: authoritative source registry and
tiering, claim-type-aware query construction, identity/authority/relevance
comparison, the five controlled statuses, and fixed explanation templates.
**793 tests passing** (480 prior + 313 Phase 4).
**Currently Working On:** Idle. Awaiting instruction to begin Phase 5.
**Latest Commit:** `feat: implement external search infrastructure`
(Phase 4 committed separately as `feat: implement claim verification`)
**Working Tree:** Phase 4 uncommitted at the time of writing.

### Phase Status Summary

| Phase | Scope | Status |
| --- | --- | --- |
| Phase 0 | Project foundation | **COMPLETE** |
| Phase 1 | Red flag engine | **COMPLETE** |
| Phase 2 | Claim & entity extraction | **COMPLETE** |
| Phase 3 | External services & search infrastructure | **COMPLETE (search only; see limitations)** |
| Phase 4 | Verification agent | **COMPLETE** |
| Phase 5 | Evidence engine | **NEXT** |

### Files Recently Changed

```
backend/app/schemas/verification.py                     (new — statuses, tiers, reason codes)
backend/app/services/verification/__init__.py           (new — public surface)
backend/app/services/verification/authority_registry.py (new — SEBI/RBI/IRDAI/NFRA/SFIO/MCA/NSE/BSE)
backend/app/services/verification/target.py             (new — claim → target resolution)
backend/app/services/verification/query_builder.py      (new — deterministic queries)
backend/app/services/verification/comparator.py         (new — identity/authority/relevance gates)
backend/app/services/verification/decision_engine.py    (new — decision table + explanations)
backend/app/services/verification/verification_service.py (new — orchestration)
backend/app/scripts/manual_verification.py              (new — runnable smoke test)
backend/tests/verification_factories.py                 (new — shared builders)
backend/tests/test_verification_schemas.py              (new)
backend/tests/test_verification_authority_registry.py   (new)
backend/tests/test_verification_target.py               (new)
backend/tests/test_verification_query_builder.py        (new)
backend/tests/test_verification_comparator.py           (new)
backend/tests/test_verification_decision_engine.py      (new)
backend/tests/test_verification_service.py              (new)
backend/tests/test_verification_package.py              (new — package invariants)
docs/IMPLEMENTATION_PLAN.md / CURRENT_STATE.md / DEVELOPMENT_LOG.md
docs/ARCHITECTURE.md / AI_PIPELINE.md / API_SPEC.md / DECISIONS.md
```

### Tests Passing

```
cd backend && python -m pytest
793 passed, 4 deselected
```

### Tests Failing

None.

### Known Bugs

None open. Phase 4 review found and fixed one issue, recorded in
`DEVELOPMENT_LOG.md`:

1. **Site-restricted queries were crowded out of the query cap.** The cap was
   being consumed by unconstrained topic and authority-vocabulary queries, so
   the most useful queries — `site:sebi.gov.in` — never ran. Query order now
   puts them first, and a test asserts a registry host is always targeted.

### Blocked Items

None.

### Available Services

| Service | Status | Evidence |
| --- | --- | --- |
| `GROQ_API_KEY` | **present** | configured; Phase 2 extraction uses it |
| `SERPAPI_KEY` | **absent** | `SearchService.available = false`; verification degrades to `SEARCH_UNAVAILABLE` |
| SQLite | working | `/api/health` → `database.connected = true` |
| `httpx` | installed | used by both `LLMService` and `SerpAPIProvider` |
| Tesseract binary | present, not on `PATH` | `resolve_tesseract_cmd()` finds it; `OCRService` not built |
| `pytesseract` / `Pillow` / PyMuPDF / `sentence-transformers` | **not installed** | deliberately deferred; see limitations |

**Consequence for Phase 5:** the whole Phase 4 suite runs against a **fake**
`SearchProvider`. Without `SERPAPI_KEY` the live path has still never executed,
so real SerpAPI ranking and snippet quality remain unverified assumptions.

### Phase 4 Design Decisions

| Decision | Rationale |
| --- | --- |
| Five statuses, no verdict vocabulary | Verification answers a checkable factual question; risk is Phase 6 (D-020). |
| `confidence` = confidence in the status | It is not a probability of fraud, loss, or truth. "We could not look" scores 0.0 honestly. |
| Three gates: identity, authority, relevance | One entity's record must never confirm another's claim, and a regulator's register cannot establish a promised return (D-021). |
| Longer names are *ambiguous*, not matches | `ABC Capital` vs `ABC Capital Advisors` — refusing costs an unverified claim; wrongly matching transfers a registration. |
| An unlisted official host is uncitable | Its tier is still reported, but it cannot settle a claim (D-018). |
| `matched_result_ids` are strings, not `Evidence` objects | The evidence model belongs to Phase 5; introducing it here would be premature. |
| Explanations from fixed templates | Wording a user reads must be auditable, not sampled (D-022). |
| `MAX_QUERIES_PER_CLAIM = 5`, site-restricted first | Bounds cost per claim against a paid provider while always asking a register the question it can answer. |
| No LLM anywhere in Phase 4 | Statuses, confidence and wording are deterministic and reproducible (D-008, project rule 4). |

### Known Limitations (Phase 4)

- **No live SerpAPI run.** `SERPAPI_KEY` is absent, so the live path is
  unexercised. Transport and normalisation are Phase 3's, tested against mocks.
- **Verification is snippet-based.** Only title and snippet text are read; no
  page is fetched and no document is parsed. A claim confirmed on page three of
  a PDF will not be found.
- **Cue detection is lexical.** "Registration cancelled" contradicts;
  a regulator's more roundabout phrasing may not be detected. The failure mode
  is a missed contradiction (`UNVERIFIED` rather than `CONTRADICTED`), never a
  false accusation.
- **The registry is eight bodies.** Unlisted official hosts are reported with a
  tier but cannot settle a claim.
- **No `source_type: TIER_4_OFFICIAL_ENTITY` sources.** The tier exists for
  later phases; nothing populates it yet.
- **Only the first identity entity drives identity matching**, with the others
  tried in turn. A claim naming three parties resolves to whichever matches.
- **Query text is never paraphrased**, so a badly phrased claim produces a
  badly targeted query. That is a deliberate trade (D-019).
- **Persisting verification results is Phase 9** work; nothing is written to the
  database yet.

### Environment Reality Check (verified)

| Capability | Status | Evidence |
| --- | --- | --- |
| `GROQ_API_KEY` | **present** in OS environment | Phase 2 extraction configured |
| `SERPAPI_KEY` | **absent** | `SearchService.available = false` |
| Tesseract binary | present at `C:\Program Files\Tesseract-OCR\tesseract.exe`, not on `PATH` | resolved by `resolve_tesseract_cmd()` |
| `pytesseract` / `Pillow` | not installed | deferred |
| PyMuPDF (`fitz`) | not installed | deferred |
| `sentence-transformers` | not installed | deferred |
| `langgraph` / `langchain-groq` | not installed | Phase 7 |
| SQLite | working | `/api/health` → `database.connected = true` |

### Important Decisions

Full rationale lives in [`DECISIONS.md`](DECISIONS.md). The ones that most affect
the next phase:

| ID | Decision |
| --- | --- |
| D-006 | Absence of evidence is never an accusation — `CONTRADICTED` requires direct authoritative contradiction |
| D-009 | Every external service degrades into a typed error code surfaced in report Limitations |
| D-017 | Search infrastructure retrieves; it does not interpret. `OK`/`UNAVAILABLE`/`ERROR` stay distinct |
| D-018 | Source authority recognised by hostname identity, never by keyword |
| D-019 | Query normalization is deterministic and never rewrites terms |
| D-020 | Verification reports a claim's factual status, never a verdict |
| D-021 | Only a direct authoritative conflict produces `CONTRADICTED` |
| D-022 | Explanation wording is rendered from fixed templates, never generated |

---

## Next Exact Task

**Phase 5 — Evidence Engine.**

1. Create `backend/app/schemas/evidence.py`: `EvidenceItem` with
   `relationship` ∈ `SUPPORTS` / `CONTRADICTS` / `CONTEXT`, plus
   `source_type`, `credibility_tier` and `retrieved_at`. It must hold the
   `src_`/`res_` ids Phase 4 already synthesised rather than recomputing them.
2. Build `EvidenceEngine` that consumes `VerificationResponse` and produces
   `EvidenceBundle`: claim → evidence → source wiring.
3. **Hard invariant:** every evidence item must trace to a source that was
   actually retrieved. No fabricated sources, ever — enforce with a test that
   every `source_id` in a bundle exists in the results Phase 4 received.
4. Source credibility ranking and tiering. Ranking is *weight*, not truth: a
   `GENERAL_WEB` page may be excellent context and must never be promoted to
   settle a claim.
5. Deliberately keep `context` items that did **not** support or contradict —
   suppressing them would hide the searches that found nothing.
6. Do **not** add a numeric "evidence strength" that later feeds a risk score
   without a decision record; D-007 requires transparent weighting.
7. Run `python -m pytest`, fix failures, update all docs, commit.

### Do not start before Phase 5 is green

- No risk scoring (Phase 6), LangGraph (Phase 7), API routes (Phase 8),
  database persistence (Phase 9).

---

## Recovery Checklist

If you are reading this in a fresh session:

1. [x] Read `docs/CURRENT_STATE.md` (this file)
2. [ ] Read `docs/IMPLEMENTATION_PLAN.md`
3. [ ] Read `docs/ARCHITECTURE.md` and `docs/DECISIONS.md`
4. [ ] Read the last entry in `docs/DEVELOPMENT_LOG.md`
5. [ ] Run `git status` and `git log --oneline -5`
6. [ ] Run `cd backend && python -m pytest` — expect **793 passed, 4 deselected**
7. [ ] Confirm the test count still matches "Tests Passing" above
8. [ ] Execute **Next Exact Task**

