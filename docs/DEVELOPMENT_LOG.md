# InvestShield AI — Development Logs

Append-only. One entry per meaningful implementation step.
Newest entries at the bottom.

---

## Phase 0 — Project foundation

**Date:** 2026-10-01
**Phase:** 0 — Project foundation

### What was implemented

- Git repository initialised on branch `main`.
- Monorepo directory structure (frontend reserved, backend, data, docs).
- Persistent documentation set (`docs/`): project context, architecture,
  implementation plan, decisions, API spec, database schema, AI pipeline, and
  current-state recovery files.
- Root configuration: `.gitignore`, `.env.example`, `README.md`,
  `docker-compose.yml`.
- Backend package skeleton with `app/{api,core,db,models,schemas,services,agents,graph,tools}`.
- `app/core/config.py` — pydantic-settings configuration loaded from `.env`,
  with derived helpers (CORS list, upload allow-list, provider-configured
  flags, repo-anchored data dir).
- `app/core/logging.py` — structured logging setup.
- `app/db/base.py` + `app/db/session.py` — SQLAlchemy 2.x declarative base,
  engine factory with cwd-independent SQLite path resolution, foreign-key pragma
  for SQLite, session factory, and the `get_db` FastAPI dependency.
- `app/services/capabilities.py` — non-invasive runtime probes for LLM,
  search, OCR, PDF and embeddings, plus Tesseract binary resolution.
- `app/api/routes/health.py` + `app/api/deps.py` — `GET /api/health` reporting
  database reachability and per-service availability without exposing secrets.
- `app/main.py` — application factory, CORS, lifespan, and structured error
  handlers (no stack traces in responses).
- Test suite: `test_config.py`, `test_health.py`, `test_db.py`,
  `test_capabilities.py`, plus `conftest.py` fixtures.

### Files created

```
.env.example
.gitignore
README.md
docker-compose.yml
backend/requirements.txt
backend/requirements-ai.txt
backend/pytest.ini
backend/app/__init__.py
backend/app/main.py
backend/app/core/__init__.py
backend/app/core/config.py
backend/app/core/logging.py
backend/app/db/__init__.py
backend/app/db/base.py
backend/app/db/session.py
backend/app/api/__init__.py
backend/app/api/deps.py
backend/app/api/routes/__init__.py
backend/app/api/routes/health.py
backend/app/services/capabilities.py
backend/app/models/__init__.py
backend/app/schemas/__init__.py
backend/app/agents/__init__.py
backend/app/graph/__init__.py
backend/app/tools/__init__.py
backend/tests/conftest.py
backend/tests/test_config.py
backend/tests/test_health.py
backend/tests/test_db.py
backend/tests/test_capabilities.py
docs/PROJECT_CONTEXT.md
docs/ARCHITECTURE.md
docs/IMPLEMENTATION_PLAN.md
docs/DECISIONS.md
docs/API_SPEC.md
docs/DATABASE_SCHEMA.md
docs/AI_PIPELINE.md
docs/CURRENT_STATE.md
```

### Tests run

```
cd backend
python -m pytest
```

### Test results

All tests passing (see `CURRENT_STATE.md` for the exact count).

### Problems encountered

1. `faiss-cpu` has no CPython 3.14 wheel → replaced with a NumPy
   cosine-similarity `VectorStore` behind a replaceable interface (D-003).
2. Tesseract is installed at `C:\Program Files\Tesseract-OCR\tesseract.exe` but
   is **not on `PATH`** → added `TESSERACT_CMD` support plus Windows fallback
   candidate paths in `resolve_tesseract_cmd()`.
3. No local PostgreSQL → SQLite default with a portable schema (D-001).
4. No API keys present → health reports `configured: false` as a healthy state,
   and every service probe degrades instead of raising (D-009).

### Problems fixed

- Health route initially used a `Settings = None` default instead of FastAPI
  `Depends`; corrected to proper dependency injection.
- Removed a redundant `tests/fixtures.py` in favour of standard `conftest.py`
  fixtures.
- Cleaned up a leftover helper stub in `db/session.py` after an edit.

### Remaining issues

None blocking. `frontend/` is intentionally empty until Phase 11.

### Next step

Phase 1 — RedFlagEngine: 15 deterministic rules with configurable weights,
evidence-span capture, and unit tests including false-positive guards.

---

## Phase 1 — Red Flag Engine

**Date:** 2026-10-01
**Phase:** 1 — Red Flag Engine

### What was implemented

- `app/schemas/red_flags.py` — `Severity`, `RedFlagCode` (15 stable codes),
  `EvidenceSpan` (exact offsets + verbatim text), and `RedFlag`
  (`code`, `name`, `description`, `severity`, `weight`, `matched_text`,
  `evidence_span`, `rule_reason`, `additional_spans`, plus computed
  `occurrence_count` and `sort_key`). Frozen Pydantic models; a validator
  enforces `matched_text == evidence_span.text`.
- `app/services/red_flag_rules.py` — the **centralised** rule catalogue. All 15
  rules, their severities, default weights, plain-language reasons, and six
  detector types:
  - `RegexDetector` — literal patterns with negation and sentence-exclusion guards
  - `PercentThresholdDetector` — promised % return vs configurable bound, with
    automatic suppression of historical/reported figures
  - `MultiplierDetector` — "Nx in a period" and word multiples ("double your money")
  - `CurrencyGrowthDetector` — "₹10,000 becomes ₹1,00,000"
  - `ShortPeriodProfitDetector` — "100% profit in 7 days"
  - `ContextKeywordDetector` — anchor + nearby investment context, used for the
    Telegram/WhatsApp rules and plain-HTTP investment links
- `app/services/red_flag_engine.py` — `RedFlagEngine.detect(text)`,
  `detect_codes(text)`, `weight_for(code)`. Deduplicates to one finding per
  code, selects the strongest occurrence as primary evidence, and sorts
  deterministically by severity → weight → code.
- `app/core/config.py` — 15 per-rule `risk_weight_*` settings and 6 thresholds
  (`unrealistic_monthly_return_threshold`, `unrealistic_annual_return_threshold`,
  `unrealistic_multiplier_threshold`, `unrealistic_short_period_pct_threshold`,
  `unrealistic_short_period_days_threshold`,
  `unrealistic_currency_growth_multiple`, `red_flag_context_window`).
- `.env.example` — same settings documented with defaults.
- Tests: `tests/test_red_flag_rules.py` (catalogue invariants, text helpers) and
  `tests/test_red_flag_engine.py` (per-rule positives, per-rule negatives, edge
  cases, spans, dedupe, sorting, configurability, determinism).

### Files created

```
backend/app/schemas/red_flags.py
backend/app/services/red_flag_rules.py
backend/app/services/red_flag_engine.py
backend/tests/test_red_flag_rules.py
backend/tests/test_red_flag_engine.py
```

### Files modified

```
backend/app/core/config.py      (15 per-rule weights + 6 thresholds)
backend/requirements.txt       (corrected pytest pins to the installed 9.1.1 / 1.4.0)
.env.example                   (weight + threshold settings)
docs/IMPLEMENTATION_PLAN.md
docs/CURRENT_STATE.md
docs/DEVELOPMENT_LOG.md
docs/ARCHITECTURE.md
docs/AI_PIPELINE.md
```

### Tests run

```
cd backend
python -m pytest
```

### Test results

**148 passed** (43 from Phase 0, 105 new in Phase 1).

- Every one of the 15 rules has positive samples that all fire.
- Every one of the 15 rules has negative (legitimate-content) samples that all
  stay clean.
- The synthetic demo input produces exactly the 8 expected indicators.
- The critical regression case `"High returns are possible"` does **not** raise
  `GUARANTEED_RETURN`, as do 6 further disclaimer-style sentences.

### Problems encountered

Six defects were found by testing, of which two were real engine bugs rather
than test bugs:

1. **Ungrouped alternation corruption (engine bug, high impact).**
   `_CREDENTIAL_STATUS` and `_REGULATORS` were interpolated into larger patterns
   as bare `a|b|c` strings. Concatenation split those patterns at the top
   level, so e.g. `SEBI requires investment advisers to be registered` matched
   `FAKE_REGULATORY_CLAIM`, and the fifth detector matched the bare word
   "registered" anywhere. Both vocabularies are now pre-grouped, and
   `test_red_flag_rules.py` asserts every detector reason and rule metadata
   field is free of regex syntax.
2. **One-sided negation window (engine bug).** Only cues *before* a match
   suppressed it, so "Returns cannot be guaranteed in any market" was flagged.
   `is_negated()` now inspects a short window before the match *and* the match
   itself.
3. **Sentence boundary too aggressive.** Splitting on every `\n` broke
   evidence spans on line-broken marketing posts. Sentences now end at
   `[.!?।]`, an ellipsis, or a **blank line**.
4. **Cross-sentence evidence bleed.** `ContextKeywordDetector` searched a wide
   window, so "Telegram" could be paired with "returns" from a previous
   paragraph, producing a nonsense span. Search is now sentence-scoped and picks
   the nearest keyword.
5. **`WITHDRAWAL_FEE` false positive.** "You can withdraw your balance at any
   time" matched the unlock/release rule. That rule now requires an explicit
   gating verb (unlock / unblock / release / free up / access).
6. **Defective test expectations.** Three assertions used wrong offsets or
   expected the wrong span text; corrected. Two initial test fixtures also
   contained placeholder expressions that were replaced.

Minor pattern gaps found while building the positive corpus and fixed:
`authorized` (US spelling) was not matched by `authoris?ed`; `8x in a single
month` was not matched; `registration closes tonight` / `our SEBI expert` /
`transfer to this UPI id` / `I work as a government investment adviser` were
not matched; `offer closes on 15 March 2027` was a false positive because no
immediacy cue was required.

### Remaining issues

None blocking. Documented limitations (English + partial Hindi patterns, text-only
proof detection, pattern-based URL indicators, accepted adviser-credential false
positives, heuristic historical-figure suppression) are recorded in
`CURRENT_STATE.md`.

### Next step

Phase 2 — Claim & Entity Extraction: Pydantic claim/entity schemas, a
deterministic claim splitter and entity extractor (URLs, domains, registration
numbers, UPI/IFSC, amounts, percentages, phone numbers, capitalised names), and
only then the `LLMService.structured_generate()` Groq path with a deterministic
fallback.

---

## Phase 2 — Claim & Entity Extraction (2026-10-01)

**Commit:** `feat: implement claim and entity extraction`
**Status:** complete — 220 tests passing (43 Phase 0 + 105 Phase 1 + 72 Phase 2)

### What was built

Pipeline:

```
Raw Text
  → Input Normalisation      (reversible offset map)
  → Claim Extraction         (one structured LLM call + deterministic)
  → Entity Extraction        (merged with deterministic identifiers)
  → Claim ↔ Entity Linking
  → ExtractionResult
```

New modules:

| Module | Role |
| --- | --- |
| `app/schemas/common.py` | `EvidenceSpan` + `make_span()` + `span_slice_matches()`, promoted out of `red_flags.py` so both phases cite evidence identically |
| `app/schemas/claims.py` | 14 `ClaimType` members, `VERIFIABLE_CLAIM_TYPES`, `PROMISE_CLAIM_TYPES`, frozen `Claim` |
| `app/schemas/entities.py` | 18 `EntityType` members, `IDENTITY_ENTITY_TYPES`, `PAYMENT_ENTITY_TYPES`, `REGULATOR_EXPANSIONS`, `normalize_entity_name()`, frozen `Entity` |
| `app/schemas/extraction.py` | `ExtractionMode`, `RelationshipType`, `ClaimEntityLink`, frozen `ExtractionResult` |
| `app/services/text_normalization.py` | `NormalizedText` with reversible offsets |
| `app/services/llm_service.py` | `LLMService` gateway, `LLMProvider`, `GroqProvider`, `NullProvider`, typed error codes |
| `app/prompts/extraction.py` | `EXTRACTION_PROMPT_VERSION = "extraction-v1"`, multilingual + no-hallucination instructions |
| `app/services/claim_extractor.py` | Deterministic sentence/clause segmentation + typed signals |
| `app/services/entity_extractor.py` | Deterministic URLs, emails, UPI, IFSC, phones, registration ids, handles, regulators, platforms, instruments, companies, persons |
| `app/services/extraction_service.py` | The merge/align/link orchestration |

`red_flag_rules.py` exports `SENTENCE_BOUNDARY` (keeping `_SENTENCE_SPLIT` as a
compatibility alias) so both stages segment text identically.
`RedFlagEngine.detect()` behaviour is **unchanged**.

Decisions D-013 … D-016 record the load-bearing choices.

### Verification

- The synthetic demo input yields exactly 6 atomic claims and 2 entities, with
  all spans slicing their own text out of the original input.
- Every span in every result is asserted against the original string
  (`span_slice_matches`) across English, Hindi, Marathi and Hinglish inputs, and
  across inputs with tabs, multi-spaces, blank lines and zero-width characters.
- `RecordingProvider` proves **exactly one** structured request per input, and
  that the request carries a JSON schema.
- Hallucinated claims and entities are dropped with a warning; a payload where
  only some items survive reports `PARTIAL`.
- Groq is never contacted by the suite. `NullProvider`, `GroqProvider` failure,
  malformed JSON and schema violation all degrade to `FALLBACK` with claims still
  extracted.

### Problems encountered

Four defects were found while testing. All four were real implementation bugs,
not test bugs:

1. **Whitespace leaked into evidence spans (high impact).** The
   normalised→original map assigned an emitted space the offset of the
   *following* character, so every span ending before a space included that
   space — `'Telegram '`, `'Our SEBI-approved expert team '`. Offsets were valid
   and consistent, so nothing crashed; the spans were simply wrong, which is the
   failure the whole offset map exists to prevent. An emitted space now maps to
   the last whitespace character of its run.
2. **NFC never actually composed.** `normalize_text` called
   `unicodedata.normalize("NFC", char)` per character, but a combining mark
   cannot compose in isolation — `"cafe" + U+0301` passed through unchanged
   despite the docstring promising NFC. Fixed by composing base-plus-combining
   units (`_iter_grapheme_units`), which is the scope over which composition is
   meaningful.
3. **`LLMService(settings=None)` crashed.** The dataclass signature allows
   `settings=None` and `build_extraction_service()` passes it through, but
   `__post_init__` dereferenced it before falling back to `get_settings()`.
4. **Identifiers collapsed when normalised.** `normalize_entity_name` stripped
   all punctuation, turning `acmefunds@okhdfcbank` into
   `acmefunds okhdfcbank` — merging distinct payment identifiers, which defeats
   de-duplication. `@`, `.` and `-` are now preserved, with edge punctuation
   trimmed instead.

Two further quality problems were fixed on evidence rather than left as accepted
limitations:

5. **"Verified Fraud" extracted as a `PERSON`.** Capitalised-word heuristics
   cannot distinguish a name from a capitalised accusation. Fixed with
   `CLAIM_VOCABULARY`, derived from the claim signal patterns, plus explicit
   stopwords for accusation vocabulary.
6. **`PARTIAL` was unreachable in practice.** Mode resolution only returned
   `PARTIAL` when the model produced nothing usable, so a payload where *some*
   items were hallucinated and dropped was reported as a clean `LLM` result.
   Discarded model output now downgrades the mode.

Test-only corrections: several assertions encoded wrong expectations (wrong
slice arithmetic, a `key` shape, punctuation-preservation behaviour, a weak
Marathi sample with no claim signal). The one weak Marathi sample was replaced
with a promise-bearing sentence; the rest were aligned to actual behaviour
after confirming that behaviour was correct.

### Remaining issues

None blocking. Documented limitations (English-led claim typing with thin
Devanagari coverage, deliberately high-precision deterministic entity typing,
pattern-based amount extraction, library-only service with no API route yet, LLM
path validated structurally rather than against live Groq) are recorded in
`CURRENT_STATE.md`.

No lint or type-check tooling is configured in this project (`pytest.ini` only),
so `python -m pytest` is the sole gate.

### Next step

Phase 3 — External Services: `SearchService` (SerpAPI + `NullProvider`),
`EmbeddingService`, `VectorStore`, `OCRService`, `PDFService`, each degrading
into a typed error code.

---

## Phase 3 — External Services & Search Infrastructure (2026-10-01)

**Commit:** `feat: implement external search infrastructure`
**Status:** search infrastructure complete — **480 tests passing** (220 prior +
260 Phase 3). `OCRService`, `PDFService`, `EmbeddingService` and `VectorStore`
are explicitly deferred (see Scope below).

### Scope decision

The phase brief listed all external services. Only search was built, for a
concrete reason: OCR, PDF and embeddings have **no consumer until Phase 8+**,
and `pytesseract`, `Pillow` and `PyMuPDF` are not installed on this machine.
Adding three untested wrappers around absent packages would create code that
cannot be exercised, and `sentence-transformers` would pull `torch` into the
import path of every test. They are recorded as pending rather than stubbed.

### What was built

```
Claim / Entity
      ↓
SearchService        ← policy: normalize, validate, classify, dedupe, limit
      ↓
SearchProvider (ABC) ← abstraction
      ↓
SerpAPIProvider      ← transport: only module that speaks SerpAPI's wire format
      ↓
Normalized SearchResult
```

| Module | Role |
| --- | --- |
| `app/schemas/search.py` | `SourceType`, `SearchStatus`, `SearchResult`, `SearchResponse`, `SOURCE_PRIORITY`, 7 error codes |
| `app/services/search/base.py` | `SearchProvider` ABC, `NullSearchProvider`, `results_or_empty()` |
| `app/services/search/query.py` | `normalize_query()`, `is_meaningful_query()` |
| `app/services/search/domain.py` | `extract_domain()`, `canonicalize_url()` |
| `app/services/search/source_classifier.py` | `classify_domain()`, `classify_url()` |
| `app/services/search/serpapi_provider.py` | `SerpAPIProvider`, `clean_text()` |
| `app/services/search/search_service.py` | `SearchService`, `build_search_service()` |
| `app/scripts/manual_search.py` | runnable smoke test (§27) |
| `app/core/config.py` | added `serpapi_engine` |

Decisions D-017 … D-019 record the load-bearing choices.

### Verification

- 260 new tests, **completely offline**. HTTP is mocked with
  `httpx.MockTransport`; failure paths that are otherwise hard to trigger
  (timeout, 429, 401/403, non-JSON, HTTP-200-with-`error`) are all covered.
- Default `python -m pytest` deselects the `integration` marker via `pytest.ini`
  (`-m "not integration"`), so the suite can never call a paid service.
  `pytest -m integration` runs the live check and skips without a key.
- `fake-sebi-example.com` and nine other lookalikes are asserted **not** to
  classify as regulator/government/exchange.
- API-key absence is asserted across the response body, warnings, and captured
  log output.
- Determinism: two identical runs over identical provider output produce
  byte-identical normalized results (excluding retrieval timestamps).
- Manual script confirms graceful degradation with no key:
  `UNAVAILABLE` / `SEARCH_NOT_CONFIGURED` / `provider_query_sent = false`.

### Problems encountered

Three defects were found while testing, plus one security issue:

1. **Unbounded call-site recursion (test bug, severe).** The fixture replaced
   `httpx.Client` module-wide, and the replacement factory called
   `httpx.Client(...)` — itself. The suite hung rather than failing. Fixed by
   capturing the real class at import time (`_REAL_HTTPX_CLIENT`).
2. **Under-classification of unlisted TLDs.** `classify_domain` only recognised a
   fixed suffix list, so `example.co.uk` and `site.com.br` fell through to
   `UNKNOWN`. A real domain with an unrecognised public suffix should be ordinary
   web content once the official registries have been ruled out by exact
   matching. Now any multi-label host with an alphabetic TLD is `GENERAL_WEB`;
   bare intranet names and IP literals stay `UNKNOWN`.
3. **Silent result truncation.** A full page produced no indication that results
   had been capped, so a truncated list could read as "everything that exists".
   The service now reports when a page came back at the limit.
4. **Live credential committed to `.env.example` (security).** The file contained
   a populated Neon `DATABASE_URL` with a password, in a tracked file. Replaced
   with a `sqlite:///` default and a `USER:PASSWORD@HOST/DBNAME` template.
   **This credential is still in Git history and should be rotated.**

Test-data corrections: one entry in the "unusable provider result" parametrize
list (`{"title": "t", "link": "https://example.com/a"}`) was in fact a valid
result and was wrongly expected to be skipped.

### Remaining issues / limitations

- **`SERPAPI_KEY` is not set on this machine**, so the live path has not been
  exercised against the real service. Transport, normalization and
  classification are verified against mocks; prompt/param names follow SerpAPI's
  documented API.
- The source registry is small and explicit by design (D-018). Unlisted official
  bodies classify as `GENERAL_WEB`, which is safe but loses authority ordering.
- `classify_domain` does no DNS/WHOIS. Whether a host is *legitimate* rather than
  *official* is a Phase 4 question.
- `OFFICIAL_ENTITY` and `TRUSTED_SECONDARY` are defined but unused; they exist so
  Phase 4/5 can extend the registry without changing the enum.
- Result de-duplication is exact-canonical-URL only; near-duplicate pages with
  different URLs are both kept (intentional — see D-017 rationale in code).

### Next step

Phase 4 — Verification Agent: authoritative source registry/tiering, targeted
query construction per claim type, claim ↔ evidence comparison, and the
controlled statuses `VERIFIED` / `UNVERIFIED` / `CONTRADICTED` /
`INSUFFICIENT_EVIDENCE` / `NOT_APPLICABLE`, consuming Phase 3's `SearchResponse`
without collapsing "no results" into "contradicted".
