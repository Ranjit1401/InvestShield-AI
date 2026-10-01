# CURRENT_STATE — InvestShield AI

> **This is the most important file for session recovery.** Read it first.
> Update it at the end of every meaningful work session.

---

## Current Project State

**Current Phase:** Phase 5 — Evidence Engine — **COMPLETE**
**Current Subphase:** Phase 6 — Risk Engine — **NOT STARTED**
**Last Completed Task:** Phase 5 — `EvidenceService` assembling traceable,
verbatim-sourced evidence from Phase 3 search results and Phase 4 verification
decisions: evidence schemas with enforced provenance, Phase 3/4 source
normalization, relationship/relevance derivation from `AssessedSource`,
verbatim excerpt construction, first-seen-wins de-duplication, deterministic
ordering, and honest no-evidence warnings.
**1161 tests passing** (793 prior + 368 Phase 5).
**Currently Working On:** Idle. Awaiting instruction to begin Phase 6.
**Latest Commit:** `feat: implement evidence infrastructure` (Phase 3),
`feat: implement claim verification` (Phase 4)
**Working Tree:** Phase 5 uncommitted at the time of writing.

### Phase Status Summary

| Phase | Scope | Status |
| --- | --- | --- |
| Phase 0 | Project foundation | **COMPLETE** |
| Phase 1 | Red flag engine | **COMPLETE** |
| Phase 2 | Claim & entity extraction | **COMPLETE** |
| Phase 3 | External services & search infrastructure | **COMPLETE (search only; see limitations)** |
| Phase 4 | Verification agent | **COMPLETE** |
| Phase 5 | Evidence engine | **COMPLETE** |
| Phase 6 | Risk engine | **NEXT** |

### Files Recently Changed

```
backend/app/schemas/evidence.py                        (new — types, relations, relevance, frozen models)
backend/app/services/evidence/__init__.py              (new — public surface)
backend/app/services/evidence/source_normalizer.py     (new — SearchResult → EvidenceSource)
backend/app/services/evidence/relationship.py          (new — relationship/relevance/type derivation)
backend/app/services/evidence/evidence_builder.py      (new — verbatim excerpts, ev_ ids, query provenance)
backend/app/services/evidence/evidence_dedupe.py       (new — de-duplication + deterministic ordering)
backend/app/services/evidence/evidence_service.py      (new — EvidenceService orchestration)
backend/app/scripts/manual_evidence.py                 (new — runnable smoke test)
backend/tests/evidence_factories.py                    (new — shared builders)
backend/tests/test_evidence_schemas.py                 (new)
backend/tests/test_evidence_source_normalizer.py       (new)
backend/tests/test_evidence_relationship.py            (new)
backend/tests/test_evidence_builder.py                 (new)
backend/tests/test_evidence_dedupe.py                  (new)
backend/tests/test_evidence_service.py                 (new)
backend/tests/test_evidence_integration.py             (new — Phase 3→4→5 chain)
backend/tests/test_evidence_package.py                 (new — package invariants)
docs/IMPLEMENTATION_PLAN.md / CURRENT_STATE.md / DEVELOPMENT_LOG.md
docs/ARCHITECTURE.md / AI_PIPELINE.md / API_SPEC.md / DECISIONS.md
```

### Tests Passing

```
cd backend && python -m pytest
1161 passed, 4 deselected
```

### Tests Failing

None.

### Known Bugs

None open. The Phase 5 test suite surfaced and fixed two issues in the new code,
recorded in `DEVELOPMENT_LOG.md`:

1. **A source cited only by `src_` id was dropped.** `EvidenceService` filtered
   supplied results against `matched_result_ids` alone, so a document Phase 4
   recorded in `source_ids` but not in `matched_result_ids` was excluded from
   its own evidence bundle. Both id families are now checked.
2. **A list of warnings was passed where a single string was expected.** Any
   claim whose supplied results were all filtered out crashed on
   `EvidenceResponse` construction instead of returning an empty bundle.
   `_empty()` now normalises and de-duplicates its warnings.

One behaviour was also **narrowed** after review, recorded in `DEVELOPMENT_LOG.md`:

3. `CONTEXT` now requires an authoritative publisher. A similarly named party on
   an arbitrary web page is `MENTIONS`; `CONTEXT` is reserved for a relevant
   authority describing a lookalike. Previously any `AMBIGUOUS` identity produced
   `CONTEXT` regardless of who published it.

Also resolved: `manual_evidence.py` was originally handing `EvidenceService`
hand-built `SearchResult` objects that Phase 3 never classified, so the demo
printed `sebi.gov.in (UNKNOWN)` alongside `TIER_1_PRIMARY_REGULATOR`. That was the
script's shortcut, not a normalizer bug. It now retrieves through
`SearchService` exactly as Phase 4 does, and the publisher category is
`REGULATOR`.

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

**Consequence for Phase 6:** the whole Phase 4 and Phase 5 suites run against a
**fake** `SearchProvider`. Without `SERPAPI_KEY` the live path has still never
executed, so real SerpAPI ranking and snippet quality remain unverified
assumptions.

### Phase 5 Design Decisions

| Decision | Rationale |
| --- | --- |
| Three separate axes: type, relationship, relevance | The longer proposed taxonomy encoded the same fact twice; `type: contradiction, relationship: supports` is worse than no type (D-023). |
| Relationship derived **only** from Phase 4's `AssessedSource` | Evidence explains verification. It cannot re-read a snippet and reach a different conclusion (D-020, D-021). |
| Evidence may *narrow* Phase 4, never widen it | A supporting cue in a document that failed an authority or identity gate is context, not proof. The gates exist so one party's page cannot confirm another's claim. |
| `NOT_APPLICABLE` claims yield no evidence, ever | No record was ever looked up, so anything attached would have to be invented. |
| Only results Phase 4 recorded may be cited | Otherwise a caller could attach a document the verification never saw, and Phase 5 cannot know whether it was real. |
| `MENTIONS`/`CONTEXT`/`IDENTITY_REFERENCE` items are kept, not suppressed | Suppressing them would hide the searches that found nothing (D-006). |
| Excerpts are `snippet`, else `title` — never merged | The moment a snippet is rewritten, the user can no longer check it against the page. |
| `ev_` ids derived by digest, never a counter or clock | Rebuilding the same evidence must yield the same id, or de-duplication and audit trails are meaningless. |
| Ordering reuses `TIER_PRIORITY` and Phase 3 priority | No new credibility score. A second priority system is a second answer to the same question (D-012). |
| No numeric evidence strength | D-007 requires transparent weighting, and any score here would silently become the Phase 6 risk input. |

### Known Limitations (Phase 5)

- **No live SerpAPI run.** `SERPAPI_KEY` is absent, so the evidence path over
  real provider output is unexercised. Transport and normalisation are Phase 3's.
- **Evidence is snippet-based.** `excerpt_origin` records `snippet` or `title`;
  no page is fetched and no document is parsed. A reader cannot always confirm a
  finding from a snippet alone, and the report must say so.
- **Excerpts are truncated to 400 characters** on a word boundary. Always a
  prefix of real text, never a rephrasing.
- **A verified claim with no showable text still shows its status.** Phase 4
  decided; Phase 5 warns that the confirming record could not be displayed
  rather than silently downgrading the verdict.
- **Evidence is not persisted.** `EvidenceBundleResponse` is returned in memory
  only; writing it is Phase 9 work.
- **Coverage counts are per claim.** Nothing yet reports cross-claim source
  diversity, which Phase 6 will need.

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
| D-006 | Absence of evidence is never an accusation — `CONTRADICTS` requires direct authoritative contradiction |
| D-007 | Risk weighting must be transparent and configurable; no hidden scores |
| D-009 | Every external service degrades into a typed error code surfaced in report Limitations |
| D-018 | Source authority recognised by hostname identity, never by keyword |
| D-020 | Verification reports a claim's factual status, never a verdict |
| D-021 | Only a direct authoritative conflict produces `CONTRADICTED` |
| D-023 | Evidence type, relationship and relevance are three separate facts; evidence never re-decides verification |

---

## Next Exact Task

**Phase 6 — Risk Engine.**

1. Weighted, transparent indicator accumulation over Phase 1 red flags, Phase 4
   verification statuses and Phase 5 evidence. Every weight must be declared in
   `app/core/config.py` and mirrored in `.env.example` (D-007).
2. Bands `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` with an explicit, documented
   threshold table — no hidden thresholds, no silent defaults.
3. Per-factor contribution breakdown on every score, so "why was this flagged?"
   is answerable from the score alone.
4. **Do not** let `EvidenceRelevance` or `EvidenceRelation` be summed into a
   number. Phase 5 carries no numeric strength field for exactly this reason;
   use `proof_count` and `source_count` as *counts*, and show the items.
5. A `VERIFIED` claim must never raise a risk band by itself, and absence of
   evidence must never raise one either (D-006, D-021).
6. No probabilistic language in any output string; add a guard test that fails
   on "probability", "likely to be a scam", "safe investment" and similar.
7. Run `python -m pytest`, fix failures, update all docs, commit.

### Do not start before Phase 6 is green

- No LangGraph (Phase 7), API routes (Phase 8), database persistence
  (Phase 9).

---

## Recovery Checklist

If you are reading this in a fresh session:

1. [x] Read `docs/CURRENT_STATE.md` (this file)
2. [ ] Read `docs/IMPLEMENTATION_PLAN.md`
3. [ ] Read `docs/ARCHITECTURE.md` and `docs/DECISIONS.md`
4. [ ] Read the last entry in `docs/DEVELOPMENT_LOG.md`
5. [ ] Run `git status` and `git log --oneline -5`
6. [ ] Run `cd backend && python -m pytest` — expect **1161 passed, 4 deselected**
7. [ ] Confirm the test count still matches "Tests Passing" above
8. [ ] Execute **Next Exact Task**

