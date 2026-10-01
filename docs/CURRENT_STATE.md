# CURRENT_STATE — InvestShield AI

> **This is the most important file for session recovery.** Read it first.
> Update it at the end of every meaningful work session.

---

## Current Project State

**Current Phase:** Phase 2 — Claim & Entity Extraction — **COMPLETE**
**Current Subphase:** Phase 3 — External Services — **NOT STARTED**
**Last Completed Task:** Phase 2 — multilingual claim/entity extraction with
original-text evidence spans, deterministic fallback, and one structured LLM call
per input. 220 tests passing (43 Phase 0 + 105 Phase 1 + 72 Phase 2).
**Currently Working On:** Idle. Awaiting instruction to begin Phase 3.
**Phase Started:** Phase 2 completed and committed as
`feat: implement claim and entity extraction`.

### Files Recently Changed

```
backend/app/schemas/common.py                  (new — shared EvidenceSpan)
backend/app/schemas/red_flags.py               (now imports the shared span)
backend/app/schemas/claims.py                  (new — 14 ClaimTypes + Claim)
backend/app/schemas/entities.py                (new — 18 EntityTypes + Entity)
backend/app/schemas/extraction.py              (new — mode, links, result)
backend/app/services/text_normalization.py     (new — reversible offset map)
backend/app/services/llm_service.py            (new — Groq + null providers)
backend/app/prompts/extraction.py              (new — extraction-v1 prompt)
backend/app/prompts/__init__.py                (new — prompt exports)
backend/app/services/claim_extractor.py        (new — deterministic claims)
backend/app/services/entity_extractor.py       (new — deterministic entities)
backend/app/services/extraction_service.py     (new — merge, align, link)
backend/app/services/red_flag_rules.py         (shared SENTENCE_BOUNDARY)
backend/tests/test_extraction_schemas.py       (new — 27 tests)
backend/tests/test_text_normalization.py       (new — 15 tests)
backend/tests/test_extraction_service.py       (new — 30 tests)
docs/IMPLEMENTATION_PLAN.md / CURRENT_STATE.md / DEVELOPMENT_LOG.md
docs/ARCHITECTURE.md / AI_PIPELINE.md / API_SPEC.md / DECISIONS.md
```

### Tests Passing

```
cd backend && python -m pytest
220 passed
```

### Tests Failing

None.

### Known Bugs

None open. Phase 2 verification found and fixed four real defects (all recorded
in `DEVELOPMENT_LOG.md`), none of them test-only:

1. **Whitespace leaked into evidence spans.** The normalised→original map gave an
   emitted space the offset of the *following* character, so every span ending
   before a space included that space (`'Telegram '`). Spaces now map to the last
   whitespace character of their run.
2. **NFC never actually composed.** Normalisation applied `unicodedata.normalize`
   to one character at a time, and a combining mark cannot compose in isolation —
   `"cafe" + U+0301` survived unchanged. Units (base + combining marks) are now
   composed as a group.
3. **`LLMService(settings=None)` crashed.** The factory signature allows `None`,
   but `__post_init__` dereferenced it before falling back to `get_settings()`.
4. **Identifiers collapsed when normalised.** Stripping punctuation turned
   `acmefunds@okhdfcbank` into `acmefunds okhdfcbank`, merging distinct payment
   ids. `@`, `.` and `-` are now preserved, with edge punctuation trimmed.

### Blocked Items

None.

### Phase 2 Design Decisions

| Decision | Rationale |
| --- | --- |
| One structured LLM call per input | Claims and entities are extracted together in a single request; a per-sentence call loop multiplies cost and latency and makes partial failure likely. |
| Deterministic extractors always run | The guaranteed-available half. When Groq is missing or errors, extraction degrades instead of failing, and the report says so. |
| Model output is re-located in the input | Any claim or entity the service cannot find verbatim is dropped with a warning. This is the anti-hallucination gate, and it holds even when Groq is available. |
| `PARTIAL` when some model output is discarded | A mix of LLM and deterministic content must not be reported as a clean `LLM` result. |
| Normalisation preserves newlines | Collapsing them would destroy paragraph structure and merge unrelated lines into one giant claim. |
| Money amounts live on the claim, not the entity list | The entity taxonomy is for named parties, instruments and identifiers; an amount is a property of a claim. |
| `CLAIM_VOCABULARY` derived from the signal patterns | A capitalised phrase built only from claim words ("Verified Fraud") is claim language, not a person's name. Deriving the stopword set keeps the two stages in step. |
| `RedFlagEngine` untouched | Phase 2 only shared `SENTENCE_BOUNDARY`; no behaviour change, no regression. |

### Known Limitations (Phase 2)

- **Claim typing is English-led.** Devanagari cues exist (`गारंटी`, `पक्का`,
  `मुनाफा`, `लाभ`) but coverage is thin. Marathi relies on shared Devanagari
  vocabulary; a genuinely Marathi-specific promise may still fall through to
  `OTHER`.
- **Deterministic entity typing is deliberately high-precision.** A bare
  capitalised two-word phrase becomes a `PERSON` only when it is not claim
  vocabulary; ambiguous names are missed rather than guessed.
- **`Claim.metadata` amounts are pattern-extracted.** `₹25,000` and `35%` are
  recognised in their common written forms; `Rs`/`INR`/lakh/crore spellings are
  covered, unusual formats are not.
- **No verification, deliberately.** `ExtractionResult` has no verdict field.
  `VERIFIED`/`CONTRADICTED` belong to Phase 4 (D-006).
- **No API route yet.** `ExtractionService` is library-only; Phase 8 exposes it.
- **LLM path is unit-tested against a fake provider.** No live Groq call runs in
  the suite, so prompt wording is validated structurally, not empirically.

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
| D-012 | Environment configuration via pydantic-settings only |
| D-013 | One structured extraction call per input; deterministic extractors always run alongside it |
| D-014 | Evidence spans index the **original** input; normalisation is reversible for offsets |
| D-015 | Model-supplied text must be re-located in the input or dropped — extraction never trusts generated text |
| D-016 | `ExtractionResult` has no verification field; that is Phase 4's job |

---

## Next Exact Task

**Phase 3 — External Services.**

1. `LLMService` and the Phase 2 `GroqProvider` are already in place. Add
   `SearchService` (SerpAPI + `NullProvider`), normalising results into a typed
   `SearchResult` with source tiering (D-005).
2. Add `EmbeddingService` (sentence-transformers, optional at runtime — it is not
   installed and must not become a hard dependency) and `VectorStore` with NumPy
   cosine similarity as the default (D-003).
3. Add `OCRService` (pytesseract; resolve the Tesseract binary via the existing
   `resolve_tesseract_cmd()` Windows fallback) and `PDFService` (PyMuPDF).
   Neither library is installed yet — add them to `requirements.txt`.
4. Every service must degrade into a typed error code and never crash an
   investigation (D-009). Tests must cover the unavailable path for each.
5. Tests must mock every external boundary; no live network calls.
6. Run `python -m pytest`, fix failures, update all docs, commit.

### Do not start before Phase 3 is green

- No LangGraph, no verification agent, no frontend, no auth, no DB persistence.

---

## Recovery Checklist

If you are reading this in a fresh session:

1. [x] Read `docs/CURRENT_STATE.md` (this file)
2. [ ] Read `docs/IMPLEMENTATION_PLAN.md`
3. [ ] Read `docs/ARCHITECTURE.md` and `docs/DECISIONS.md`
4. [ ] Read the last entry in `docs/DEVELOPMENT_LOG.md`
5. [ ] Run `git status` and `git log --oneline -10`
6. [ ] Run `cd backend && python -m pytest` — expect **220 passed**
7. [ ] Confirm the test count still matches "Tests Passing" above
8. [ ] Execute **Next Exact Task**
