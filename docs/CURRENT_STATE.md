# CURRENT_STATE — InvestShield AI

> **This is the most important file for session recovery.** Read it first.
> Update it at the end of every meaningful work session.

---

## Current Project State

**Current Phase:** Phase 1 — Red Flag Engine — **COMPLETE**
**Current Subphase:** Phase 2 — Claim & Entity Extraction — **NOT STARTED**
**Last Completed Task:** Phase 1 verification — 148 tests passing (43 Phase 0 +
105 Phase 1); all 15 rules fire on their positive samples; every per-rule
negative sample stays clean; backend still imports and boots.
**Currently Working On:** Idle. Awaiting instruction to begin Phase 2.
**Phase Started:** — (Phase 2 not yet started)

### Files Recently Changed

```
backend/app/schemas/red_flags.py              (new — typed result schemas)
backend/app/services/red_flag_rules.py        (new — 15-rule catalogue)
backend/app/services/red_flag_engine.py       (new — detection engine)
backend/app/core/config.py                    (15 per-rule weights + 6 thresholds)
backend/tests/test_red_flag_rules.py          (new — catalogue invariants)
backend/tests/test_red_flag_engine.py         (new — detection behaviour)
backend/requirements.txt                      (corrected pytest pins to 9.1.1)
.env.example                                  (new threshold settings)
docs/CURRENT_STATE.md / DEVELOPMENT_LOG.md / IMPLEMENTATION_PLAN.md
docs/ARCHITECTURE.md / AI_PIPELINE.md
```

### Tests Passing

```
cd backend && python -m pytest
148 passed
```

### Tests Failing

None.

### Known Bugs

None open. Six defects were found and fixed during Phase 1 verification (all
recorded in `DEVELOPMENT_LOG.md`). The two most significant were real engine
bugs, not test bugs:

1. **Ungrouped alternation corruption.** `_CREDENTIAL_STATUS` and `_REGULATORS`
   were interpolated as bare `a|b|c` strings. Concatenating them into a larger
   pattern split that pattern at the top level, so e.g. "SEBI requires
   investment advisers to be registered" matched a rule it should never match.
   Both vocabularies are now pre-grouped and `test_red_flag_rules.py` guards it.
2. **Negation window was one-sided.** Only cues *before* a match suppressed it,
   so "Returns cannot be guaranteed in any market" was flagged. Negation is now
   checked both before and inside the match.

### Blocked Items

None.

### Phase 1 Design Decisions

| Decision | Rationale |
| --- | --- |
| Rules live in one module (`red_flag_rules.py`) | The brief forbids scattering scam regexes; all pattern knowledge is now in one catalogue. |
| Six detector *types* rather than 100 raw regexes | Numeric rules ("30% monthly") need threshold comparison and historical-figure exclusion, which a bare regex cannot express correctly. |
| Negation handling | "Past performance does not guarantee returns" is a disclaimer, not a promise. Suppressing it is a false-positive fix, not a loophole. |
| Sentence = `[.!?…]` or **blank line** | Marketing posts are line-broken; treating a single `\n` as a sentence boundary would break evidence spans. |
| ContextKeywordDetector for Telegram/WhatsApp/URL | A bare "Telegram" is not an indicator. Investment context must appear in the same sentence. |
| `weight` configurable, `severity` fixed per rule | Weight tunes risk contribution; severity is the intrinsic seriousness of the indicator type. |
| `verification_note` on credential rules | Forces the wording "requires verification" rather than "is fake" (D-006). |

### Known Limitations (Phase 1)

- **English + partial Hindi patterns only.** Marathi and other scripts are not
  covered yet; Phase 2/15 handle presentation-layer translation, and the
  analysis layer needs more script work before this is genuinely
  language-independent.
- **Text only.** `FAKE_PROFIT_SCREENSHOT` reads the *language* referencing
  proof; no image authenticity analysis (out of scope until Phase 13).
- **`SUSPICIOUS_URL` is pattern-based.** A shortener, raw IP, punycode or
  plain-HTTP investment link is an indicator, never a malware verdict. No
  reputation/DNS lookup yet (that belongs to Phase 4 search).
- **Adviser-credential false positives are accepted.** "Consult a certified
  financial planner" will raise `UNVERIFIED_ADVISER`. This is deliberate: the
  rule detects a credential claim needing verification, and Phase 4 resolves it.
- **Historical-figure exclusion is heuristic.** "Our fund returned 95% in 2023"
  is suppressed by the year/reported cues. A promotional claim that happens to
  mention a year could be missed.

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

**Phase 2 — Claim & Entity Extraction.**

1. Create `backend/app/schemas/claims.py` and `backend/app/schemas/entities.py`:
   `ClaimType` (`REGULATORY`, `RETURN`, `PAYMENT`, `ENTITY_IDENTITY`, `URGENCY`,
   `PLATFORM`, `COST`, `PERFORMANCE`, `OTHER`), `EntityType` (11 members), and
   the `Claim` / `Entity` models (`claim_id`, `claim_text`, `claim_type`,
   `entities`, `confidence`, `verification_required` / `entity_id`, `name`,
   `entity_type`, `registration_number`, `mentions`, `confidence`).
2. Create `backend/app/services/claim_extractor.py` with a **deterministic**
   sentence/claim splitter as the baseline. It must split the demo input into
   ~6 atomic claims, never the whole message as one claim.
3. Create `backend/app/services/entity_extractor.py` with deterministic
   extraction for: URLs, domains, registration numbers, UPI/IFSC identifiers,
   currency amounts, percentages, phone numbers, and capitalised person/company
   names.
4. Only then add the LLM path: `LLMService.structured_generate()` with Groq and a
   `NullProvider` fallback to the deterministic extractors.
5. Write `backend/tests/test_claims.py`, `test_entities.py` and
   `test_extractors.py`. Required cases: the demo input splits into multiple
   atomic claims; `"High returns are possible"` produces no fabricated
   guarantee claim; entities typed correctly; no-key mode still extracts.
6. Run `python -m pytest`, fix failures, update all docs, commit.

### Do not start before Phase 2 is green

- Phase 3 (external services) beyond the minimum `LLMService` needed here.
- No LangGraph, no frontend, no auth, no DB persistence.

---

## Recovery Checklist

If you are reading this in a fresh session:

1. [x] Read `docs/CURRENT_STATE.md` (this file)
2. [ ] Read `docs/IMPLEMENTATION_PLAN.md`
3. [ ] Read `docs/ARCHITECTURE.md` and `docs/DECISIONS.md`
4. [ ] Read the last entry in `docs/DEVELOPMENT_LOG.md`
5. [ ] Run `git status` and `git log --oneline -10`
6. [ ] Run `cd backend && python -m pytest` — expect **148 passed**
7. [ ] Confirm the test count still matches "Tests Passing" above
8. [ ] Execute **Next Exact Task**
