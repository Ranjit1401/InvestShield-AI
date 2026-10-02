# CURRENT_STATE — InvestShield AI

> **This is the most important file for session recovery.** Read it first.
> Update it at the end of every meaningful work session.

---

## Current Project State

**Current Phase:** Phase 6 — Risk Engine — **COMPLETE**
**Current Subphase:** Phase 7 — LangGraph Orchestration — **NOT STARTED**
**Last Completed Task:** Phase 6 — `RiskService` producing a deterministic,
explainable risk assessment from Phase 1 red flags, Phase 2 claims, Phase 4
verification statuses and Phase 5 evidence: risk schemas whose invariants are
enforced by validation rather than convention, weights resolved through Phase 1's
own rule table, four inclusive bands with a documented threshold table, and
cross-stage de-duplication so that one behaviour noticed by four stages scores
once and the other three views are kept as provenance.
**1546 tests passing** (1161 prior + 385 Phase 6).
**Currently Working On:** Idle. Awaiting instruction to begin Phase 7.
**Latest Commit:** `feat: implement evidence engine` (Phase 5),
`feat: implement claim verification` (Phase 4)
**Working Tree:** Phase 6 uncommitted at the time of writing.

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
| Phase 7 | LangGraph orchestration | **NEXT** |

### Files Recently Changed

```
backend/app/schemas/risk.py                             (new — levels, factors, weights, thresholds, assessment)
backend/app/services/risk/__init__.py                   (new — public surface)
backend/app/services/risk/risk_scoring.py               (new — weight snapshot, bands, score arithmetic)
backend/app/services/risk/risk_factors.py               (new — status → factor mapping, rsk_/rf_ ids)
backend/app/services/risk/risk_aggregation.py           (new — de-duplication and cross-stage linking)
backend/app/services/risk/risk_service.py               (new — RiskService orchestration)
backend/app/scripts/manual_risk.py                      (new — runnable smoke test)
backend/tests/risk_factories.py                         (new — shared builders)
backend/tests/test_risk_schemas.py                      (new)
backend/tests/test_risk_scoring.py                      (new)
backend/tests/test_risk_factors.py                      (new)
backend/tests/test_risk_aggregation.py                  (new)
backend/tests/test_risk_service.py                      (new — end to end)
backend/tests/test_risk_safety.py                       (new — vocabulary ban, negation-aware)
backend/tests/test_risk_package.py                      (new — package invariants)
docs/IMPLEMENTATION_PLAN.md / CURRENT_STATE.md / DEVELOPMENT_LOG.md
docs/ARCHITECTURE.md / AI_PIPELINE.md / API_SPEC.md / DECISIONS.md
```

### Tests Passing

```
cd backend && python -m pytest
1546 passed, 4 deselected
```

### Tests Failing

None.

### Known Bugs

None open. The Phase 6 test suite surfaced and fixed three issues in the new code,
recorded in `DEVELOPMENT_LOG.md`:

1. **Every red-flag weight resolved to `0`.** `RiskWeights` was keyed by
   `code.value.lower()` but looked up with `RedFlagCode.code.value`, so no code
   ever matched. The symptom was an assessment that scored 0 while reporting
   red-flag factors. The table is now keyed by the enum itself, and weights are
   resolved through Phase 1's `RULES_BY_CODE`/`weight_attr` rather than by
   reconstructing setting names, so the two mappings cannot diverge again.
2. **`RISK_RELEVANT_CLAIM_TYPES` was imported from the wrong module.** It is
   defined in `app.schemas.risk`, not `app.schemas.claims`; the import was a
   package-level `ImportError` and surfaced immediately.
3. **An `UNVERIFIED_CLAIM` factor was exempted from de-duplication.** The first
   rule exempted every factor with `is_uncertainty`, which includes
   `UNVERIFIED_CLAIM` — a weighted factor that genuinely double-counts against
   the matching red flag. The rule is now "a factor that already weighs nothing
   is not absorbed", which covers `INSUFFICIENT_EVIDENCE` without letting a real
   finding count twice.

One behaviour was also **narrowed** after review, recorded in `DEVELOPMENT_LOG.md`:

4. `INSUFFICIENT_EVIDENCE` factors are no longer given an `absorbed_into` pointer.
   The primary red flag counted a *pattern*; pointing at it implied it had also
   accounted for a verification it never performed. Such factors now carry no
   pointer and are kept visible at zero contribution.

One **pre-existing Phase 1 wording issue** was found and deliberately not fixed
here, recorded in `DEVELOPMENT_LOG.md`:

5. The `SUSPICIOUS_URL` rule description reads "A link was found that uses a
   pattern commonly seen in fraudulent campaigns." That asserts the word
   *fraudulent*, which the product's own vocabulary standard discourages. Phase 6
   reuses Phase 1's text verbatim by design, so the phrase reaches every risk
   report. Changing it is a Phase 1 decision with its own tests, so it is pinned
   by `TestPhaseOneCatalogueTripwire` rather than silently reworded here.

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

**Consequence for Phase 7:** the whole Phase 4, 5 and 6 suites run against a
**fake** `SearchProvider`. Without `SERPAPI_KEY` the live path has still never
executed, so real SerpAPI ranking and snippet quality remain unverified
assumptions. The risk engine itself needs no network and was verified fully
offline.

### Phase 6 Design Decisions

| Decision | Rationale |
| --- | --- |
| One weight per rule, resolved through Phase 1's `RULES_BY_CODE` | Reconstructing `risk_weight_<code>` here would be a second mapping that could silently resolve to `0` (D-007). |
| `RiskWeights` keyed by the `RedFlagCode` enum, not a string | A key-casing mismatch is invisible and understates risk. The type makes it impossible. |
| `contribution <= weight` enforced by validation | Turns "do not double count" from an aspiration into an arithmetic invariant. |
| A de-duplicated factor is **kept** at `0`, never dropped | Dropping hides that a claim was contradicted; scoring it double-counts. |
| Absorption needs a **scoring** primary | A zero-weight red flag must not silence the claim's own finding. |
| `VERIFIED` and `NOT_APPLICABLE` produce no factor at all | Neither is a risk indicator, and no member may encode a risk *reduction* (D-020). |
| `INSUFFICIENT_EVIDENCE` weights `0` by default | Not being able to look is not a finding. Uncertainty is reported, not scored (D-006). |
| `UNVERIFIED` scores only for **completed-search** reason codes | `SEARCH_UNAVAILABLE` + `UNVERIFIED` is representable and would turn an offline run into an accusation. The reason-code gate is where it is caught. |
| Claims link to red flags by span overlap **or** claim-type map | Phase 1 and Phase 2 segment text differently, so overlap alone misses real duplicates. |
| Evidence never contributes; it only attaches ids | The same document reachable from ten queries cannot move a score (D-007). |
| `SCORE_NOT_A_PROBABILITY` is a **schema** invariant | The assessment appends it during validation, so no caller — service, test, or future Phase 7 rehydration — can omit it. |
| The score is capped, never rescaled | Two documents far past the ceiling must score identically for one number to mean anything. |
| Factor and red-flag ids are digests, not counters | A re-run must be comparable to a stored report, and `absorbed_into` must point at something durable. |

### Known Limitations (Phase 6)

- **No live SerpAPI run.** `SERPAPI_KEY` is absent, so a real Phase 4 result has
  never been scored. Every test drives the engine with synthetic but schema-valid
  Phase 4 and Phase 5 objects.
- **Bands are product heuristics.** `20/50/90/100` were chosen to be
  explainable and monotonic. They are **not** empirically validated, not derived
  from any dataset, and no score at any level is a probability of anything.
- **Weights are not calibrated.** Each `risk_weight_*` is a declared judgement
  about relative seriousness. Reasonable to argue with; not measured.
- **Claim-type → red-flag linking is a hand-built map.** `CLAIM_TYPE_RED_FLAG_CODES`
  is deliberately narrow, but a claim family that is genuinely the same signal as
  a rule and is missing from the map will be scored twice.
- **The score is not persisted.** `RiskAssessment` is returned in memory only;
  `RiskService.assess_batch` exists for Phase 7, and writing is Phase 9 work.
- **`evidence_coverage` counts claims, not source diversity.** Phase 5 noted
  cross-claim source diversity as unaddressed; Phase 6 measures the share of
  assessed claims with at least one proof-grade document, which is a
  transparency measure and not a confidence.
- **Phase 1's `SUSPICIOUS_URL` description asserts the word "fraudulent".** Known,
  pinned by a tripwire test, and a Phase 1 fix. See Known Bugs item 5.

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
| D-025 | The risk score is a transparent heuristic indicator sum, never a probability; bands are product heuristics |

---

## Next Exact Task

**Phase 7 — LangGraph Orchestration.**

1. Compose Phases 2–6 into a single stateful graph, with a typed state carrying
   `ExtractionResult` → `VerificationResult` → `EvidenceResponse` →
   `RiskAssessment` so no stage re-derives another's work.
2. `RiskService.assess_batch` is the shape Phase 7 should call: one
   investigation, many claims.
3. `risk_weight_*` and `risk_band_*` stay in `Settings` and must be mirrored in
   `.env.example` (D-007). Phase 6 added three verification weights
   (`risk_weight_contradicted_claim`, `risk_weight_unverified_claim`,
   `risk_weight_insufficient_evidence`) and `risk_score_ceiling`.
4. Preserve determinism: the same inputs must produce the same factor ids and the
   same score on every run, or stored reports are not auditable.
5. Surface every typed error code from Phases 3–5 as a report Limitation
   (D-009). `RiskAssessment.warnings` is the existing place for risk-stage
   limitations; do not fold them into the score.
6. Do **not** let the graph introduce a verdict, a probability, or advice. The
   vocabulary ban in `test_risk_safety.py` must keep passing unchanged.
7. No API routes (Phase 8), no database persistence (Phase 9).

### Do not start before Phase 7 is green

- No FastAPI endpoints (Phase 8), no persistence (Phase 9).

---

## Recovery Checklist

If you are reading this in a fresh session:

1. [x] Read `docs/CURRENT_STATE.md` (this file)
2. [ ] Read `docs/IMPLEMENTATION_PLAN.md`
3. [ ] Read `docs/ARCHITECTURE.md` and `docs/DECISIONS.md`
4. [ ] Read the last entry in `docs/DEVELOPMENT_LOG.md`
5. [ ] Run `git status` and `git log --oneline -5`
6. [ ] Run `cd backend && python -m pytest` — expect **1546 passed, 4 deselected**
7. [ ] Confirm the test count still matches "Tests Passing" above
8. [ ] Execute **Next Exact Task**

