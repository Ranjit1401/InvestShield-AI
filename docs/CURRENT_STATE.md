# CURRENT_STATE — InvestShield AI

> **This is the most important file for session recovery.** Read it first.
> Update it at the end of every meaningful work session.

---

## Current Project State

**Current Phase:** Phase 3 — External Services & Search Infrastructure — **COMPLETE (search scope)**
**Current Subphase:** Phase 4 — Verification Agent — **NOT STARTED**
**Last Completed Task:** Phase 3 — `SearchService` / `SearchProvider` /
`SerpAPIProvider` with deterministic query normalization, hostname-identity
source classification, canonical-URL de-duplication, bounded result limits, and
explicit `OK` / `UNAVAILABLE` / `ERROR` states. **480 tests passing** (220
prior + 260 Phase 3).
**Currently Working On:** Idle. Awaiting instruction to begin Phase 4.
**Latest Commit:** `feat: implement external search infrastructure`
**Working Tree:** clean at the time of writing.

### Phase Status Summary

| Phase | Scope | Status |
| --- | --- | --- |
| Phase 0 | Project foundation | **COMPLETE** |
| Phase 1 | Red flag engine | **COMPLETE** |
| Phase 2 | Claim & entity extraction | **COMPLETE** |
| Phase 3 | External services & search infrastructure | **COMPLETE (search only; see limitations)** |
| Phase 4 | Verification agent | **NEXT** |

### Files Recently Changed

```
backend/app/schemas/search.py                    (new — SearchResult/Response/SourceType)
backend/app/services/search/__init__.py          (new — public surface)
backend/app/services/search/base.py              (new — SearchProvider ABC, NullSearchProvider)
backend/app/services/search/query.py             (new — deterministic query normalization)
backend/app/services/search/domain.py            (new — extract_domain, canonicalize_url)
backend/app/services/search/source_classifier.py (new — hostname-identity classification)
backend/app/services/search/serpapi_provider.py  (new — SerpAPI transport)
backend/app/services/search/search_service.py    (new — orchestration + policy)
backend/app/scripts/manual_search.py             (new — runnable smoke test)
backend/app/core/config.py                       (+serpapi_engine)
backend/pytest.ini                               (+integration marker, offline default)
backend/tests/test_search_schemas.py             (new — 29 tests)
backend/tests/test_search_query.py               (new — 38 tests)
backend/tests/test_search_domain.py              (new — 37 tests)
backend/tests/test_search_source_classifier.py   (new — 48 tests)
backend/tests/test_serpapi_provider.py           (new — 55 tests)
backend/tests/test_search_service.py             (new — 54 tests)
backend/tests/test_search_integration.py         (new — 4 opt-in live tests)
.env.example                                     (search settings + credential removed)
docs/IMPLEMENTATION_PLAN.md / CURRENT_STATE.md / DEVELOPMENT_LOG.md
docs/ARCHITECTURE.md / AI_PIPELINE.md / API_SPEC.md / DECISIONS.md
```

### Tests Passing

```
cd backend && python -m pytest
480 passed, 4 deselected

# opt-in live check (skips without a key)
python -m pytest -m integration
```

### Tests Failing

None.

### Known Bugs

None open. Phase 3 verification found and fixed four issues, all recorded in
`DEVELOPMENT_LOG.md`:

1. **Unbounded call-site recursion in the HTTP mock fixture** — the patched
   `httpx.Client` factory called itself, hanging the suite instead of failing it.
2. **Under-classification of unlisted public suffixes** — `example.co.uk`
   returned `UNKNOWN`; now `GENERAL_WEB` once the official registries are ruled
   out by exact matching.
3. **Silent result truncation** — a full result page gave no indication that
   results had been capped.
4. **A live database credential was committed in `.env.example`** — replaced with
   a placeholder. **The credential remains in Git history and must be rotated.**

### Blocked Items

None.

### Available Services

| Service | Status | Evidence |
| --- | --- | --- |
| `GROQ_API_KEY` | **present** | configured; Phase 2 extraction uses it |
| `SERPAPI_KEY` | **absent** | `SearchService.available = false`; searches degrade to `UNAVAILABLE` |
| SQLite | working | `/api/health` → `database.connected = true` |
| `httpx` | installed | used by both `LLMService` and `SerpAPIProvider` |
| Tesseract binary | present, not on `PATH` | `resolve_tesseract_cmd()` finds it; `OCRService` not built |
| `pytesseract` / `Pillow` / PyMuPDF / `sentence-transformers` | **not installed** | deliberately deferred; see limitations |

**Consequence for Phase 4:** claim verification must run against a **fake search
provider** in tests. Without `SERPAPI_KEY` the live path cannot be exercised, so
the no-search path must produce a valid report stating that external verification
could not be completed — and must never produce `CONTRADICTED`.

### Phase 3 Design Decisions

| Decision | Rationale |
| --- | --- |
| `SearchProvider` ABC, SerpAPI behind it | Same rule as `LLMService` (D-004): the provider is the most likely thing to be swapped, rate-limited or paid for. Consumers depend on `SearchService` only. |
| Transport in the provider, policy in the service | The provider converts its wire format and nothing more; query rules, limits, classification and de-duplication are provider-independent. |
| `OK` / `UNAVAILABLE` / `ERROR` are distinct, schema-enforced | "Found nothing" and "could not look" lead to opposite conclusions. `SearchResponse` refuses to represent a failure as an empty success (D-017). |
| Authority by hostname identity only | `fake-sebi-example.com` must never be a regulator; an over-claiming classifier is worse than none (D-018). |
| Query normalization is deterministic, no LLM | Paraphrasing a query would verify a claim nobody wrote (D-019). |
| De-duplication by canonical URL only | Merging on title similarity would silently discard sources from an evidence review. |
| Hard cap of 50 results | Bounds the request a bug can make of a paid provider. |
| Reuse `SERPAPI_KEY` rather than adding `SERPAPI_API_KEY` | The brief suggested the longer name, but `serpapi_key` already exists in `Settings` from Phase 0. Adding a second name for one setting would split configuration across two keys; the mapping is documented in `.env.example`. |
| OCR/PDF/embeddings deferred | No consumer until Phase 8+, and the packages are not installed. Stub wrappers would be untestable code. |

### Known Limitations (Phase 3)

- **`SERPAPI_KEY` is absent on this machine**, so the live SerpAPI path has never
  run. Transport, normalization and classification are verified against mocks;
  parameter names follow SerpAPI's documented API. Run
  `pytest -m integration` once a key is available.
- **The source registry is small and explicit** by design. Unlisted official
  bodies classify as `GENERAL_WEB` — safe, but it loses authority ordering.
- **No DNS or WHOIS.** `classify_domain` decides who *published* a page, not
  whether a host is *legitimate*. That is a Phase 4 question.
- **`OFFICIAL_ENTITY` and `TRUSTED_SECONDARY`** are defined but unused, so Phase
  4/5 can extend the registry without changing the enum.
- **Near-duplicates are not merged.** Only canonical-URL equality collapses two
  results; different URLs with identical titles are both kept.
- **OCR, PDF, embeddings and vector store are not built.** See the scope decision
  in `DEVELOPMENT_LOG.md`.
- **No claim/entity → query construction yet.** Phase 3 takes an arbitrary query;
  building a targeted query from a claim type is Phase 4's first task.

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
| D-007 | Risk is a transparent weighted heuristic, never a probability |
| D-009 | Every external service degrades into a typed error code surfaced in report Limitations |
| D-013 | One structured extraction call per input; deterministic extractors always run alongside it |
| D-014 | Evidence spans index the **original** input; normalisation is reversible for offsets |
| D-015 | Model-supplied text must be re-located in the input or dropped |
| D-016 | `ExtractionResult` has no verification field |
| D-017 | Search infrastructure retrieves; it does not interpret. `OK`/`UNAVAILABLE`/`ERROR` stay distinct |
| D-018 | Source authority recognised by hostname identity, never by keyword |
| D-019 | Query normalization is deterministic and never rewrites terms |

---

## Next Exact Task

**Phase 4 — Verification Agent.**

1. Create `backend/app/schemas/verification.py`: `VerificationStatus`
   (`VERIFIED` / `UNVERIFIED` / `CONTRADICTED` / `INSUFFICIENT_EVIDENCE` /
   `NOT_APPLICABLE`), `ClaimVerification`, `SourceTier`, `AuthoritativeSource`.
   **There must be no `SCAM`/`FRAUD` verdict anywhere.**
2. Build the authoritative source registry/tiering: SEBI, NSE, BSE, MCA and
   government bodies, with per-tier query templates. Reuse
   `classify_domain()` for source tiers; do not reimplement domain logic.
3. Build targeted query construction from `Claim` — claim-type-aware, derived
   from the claim text and linked entities. Use `SearchService.search()`; never
   call SerpAPI directly.
4. Compare claim vs. retrieved evidence and emit a status. **Hard rules:**
   - no results ⇒ `UNVERIFIED` or `INSUFFICIENT_EVIDENCE`, never `CONTRADICTED`
   - `SearchStatus.UNAVAILABLE`/`ERROR` ⇒ `INSUFFICIENT_EVIDENCE`
   - `CONTRADICTED` only when an authoritative source directly disputes the claim
   - `UNVERIFIED` is not an accusation
5. Reason text must be hedged: *"No matching registration could be independently
   verified from the searched authoritative records."*
6. Write a guard test asserting no accusation language is ever produced, and
   tests mocking `SearchProvider` for every path (available, no key, zero
   results, timeout, error).
7. Run `python -m pytest`, fix failures, update all docs, commit.

### Do not start before Phase 4 is green

- No evidence ranking/strength (Phase 5), risk scoring (Phase 6), LangGraph
  (Phase 7), API routes (Phase 8), database persistence (Phase 9).

---

## Recovery Checklist

If you are reading this in a fresh session:

1. [x] Read `docs/CURRENT_STATE.md` (this file)
2. [ ] Read `docs/IMPLEMENTATION_PLAN.md`
3. [ ] Read `docs/ARCHITECTURE.md` and `docs/DECISIONS.md`
4. [ ] Read the last entry in `docs/DEVELOPMENT_LOG.md`
5. [ ] Run `git status` and `git log --oneline -5`
6. [ ] Run `cd backend && python -m pytest` — expect **480 passed, 4 deselected**
7. [ ] Confirm the test count still matches "Tests Passing" above
8. [ ] Execute **Next Exact Task**

