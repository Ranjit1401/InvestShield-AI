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

---

## Phase 4 — Verification Agent (2026-10-01)

### What was built

| File | Responsibility |
| --- | --- |
| `app/schemas/verification.py` | Five statuses, `SourceTier` + priority, `IdentityMatch`, twelve reason codes, frozen `VerificationResult` / `VerificationResponse` |
| `app/services/verification/authority_registry.py` | Eight official bodies, hostname-identity lookup, per-claim-type relevance |
| `app/services/verification/target.py` | `VerificationTarget`: claim + entities → parties, identifiers, relevant authorities |
| `app/services/verification/query_builder.py` | Deterministic, template-driven queries, capped at 5 per claim |
| `app/services/verification/comparator.py` | Identity / authority / relevance gates, support vs contradiction cues, stable ids |
| `app/services/verification/decision_engine.py` | Pure decision table, confidence table, fixed explanation templates |
| `app/services/verification/verification_service.py` | Orchestration over Phase 3's `SearchService` |
| `app/scripts/manual_verification.py` | Offline fixture pass + live pass |

**313 new tests; 793 total, all offline.** Every `SearchProvider` path is
faked: available, no credentials, zero results, error, and a provider that
raises despite its no-raise contract.

### The decision table

Twelve rows, each pinned by at least one test, and a coverage test asserting
that all twelve reason codes are reachable:

| Condition | Status | Reason code |
| --- | --- | --- |
| Instruction or opinion | `NOT_APPLICABLE` | `NOT_A_FACTUAL_CLAIM` |
| No query built, or none issued | `INSUFFICIENT_EVIDENCE` | `NO_QUERY_BUILT` |
| Search never attempted | `INSUFFICIENT_EVIDENCE` | `SEARCH_UNAVAILABLE` |
| Search attempted and failed | `INSUFFICIENT_EVIDENCE` | `SEARCH_FAILED` |
| Nothing from a relevant authority | `INSUFFICIENT_EVIDENCE` | `NO_CLAIM_RELEVANT_SOURCE` |
| Only a similarly named organisation | `INSUFFICIENT_EVIDENCE` | `IDENTITY_AMBIGUOUS` |
| Claimed entity not in any source | `UNVERIFIED` | `IDENTITY_NOT_FOUND` |
| Zero results, registration-style claim | `UNVERIFIED` | `ZERO_RESULTS` |
| Zero results, other claim family | `INSUFFICIENT_EVIDENCE` | `NO_CONFIRMATION_FOUND` |
| Relevant authoritative support | `VERIFIED` | `AUTHORITATIVE_SOURCE_CONFIRMS` |
| Relevant authoritative conflict | `CONTRADICTED` | `AUTHORITATIVE_SOURCE_CONTRADICTS` |
| Sources both support and contradict | `INSUFFICIENT_EVIDENCE` | `CONFLICTING_AUTHORITATIVE_SOURCES` |

### Design decisions taken

- **`confidence` measures the assessment, not the claim.** A `VERIFIED` status
  sits at 0.80–0.85 depending on the tier of the source relied on; "we could not
  look" sits at 0.0. Documented on the field and in `CURRENT_STATE.md`, because
  a number placed next to a status is exactly where a fraud probability would
  otherwise be implied.
- **Three gates, not one.** Identity, authority and relevance must *all* pass
  before a document can move a status. Relevance is the gate that stops SEBI from
  rubber-stamping a promised return: `authorities_for_claim_type()` returns
  nothing for `RETURN_PROMISE`, `PAYMENT_INSTRUCTION` and friends.
- **Prefix matches are ambiguous, not matches.** `ABC Capital` is not confirmed
  by `ABC Capital Advisors`, `ABC Capital Limited` or `ABC Capital Holdings`;
  those resolve to `IDENTITY_AMBIGUOUS`. Refusing costs an `INSUFFICIENT_EVIDENCE`
  and wrongly accepting transfers one entity's registration to another.
- **An unlisted official host is reported but uncitable.** Its tier still comes
  from Phase 3's classifier, so a real `.gov.in` body appears in output with the
  right rank — but it cannot settle a claim, because nothing vouches for the
  mapping (D-018).
- **Contradiction cues are evaluated before support cues.** "The registration has
  been cancelled" contains the word "registered"; reading that as support would
  be the most damaging mistake available to this stage.
- **`source_ids` are only populated for statuses that assert a source said
  something.** `UNVERIFIED` and `INSUFFICIENT_EVIDENCE` record the results that
  were examined in `matched_result_ids` and leave `source_ids` empty, so no
  status ever appears to have support behind it that was not found.
- **Fixed explanation templates, one per reason code.** No generated prose; a
  guard test scans every template, warning and produced result for verdict
  vocabulary on word boundaries (D-022).
- **The Phase 5 `Evidence` model was deliberately not introduced.** Phase 4
  synthesises `src_`/`res_` ids from `sha256(canonical_url)[:12]` and stores them
  as plain strings, so Phase 5 can attach an evidence object without Phase 4
  guessing its shape.

### Issue found and fixed during review

**Site-restricted queries never ran.** The first query ordering emitted
unconstrained topic and authority-vocabulary queries first, and those consumed
all five slots of `MAX_QUERIES_PER_CLAIM`. The most useful queries —
`site:sebi.gov.in` — were built and then dropped. Order is now: primary topic,
site-restricted per authority, verbatim identifier, remaining topics, authority
vocabulary. A test asserts a registry host is always targeted.

Two smaller corrections fell out of the same review: a subject consisting only of
punctuation produced a query (`"... registration`), and search operators embedded
in a subject (`site:`, `filetype:`) were not stripped, letting a subject change
what the query asked for.

### Limitations

- The live SerpAPI path has still never run: `SERPAPI_KEY` is absent on this
  machine, so real ranking and snippet quality remain unverified assumptions.
- Verification reads titles and snippets only. No page is fetched, so a claim
  confirmed deeper in a document will not be found.
- Cue detection is lexical and bilingual-unaware; a regulator's roundabout
  phrasing may be missed. The failure direction is a missed `CONTRADICTED`, never
  a false accusation.
- Identity matching uses the first identity entity, then the others in turn.
- Nothing is persisted; `claims.status` / `claims.verification_reason` are
  Phase 9 work.

### Next step

Phase 5 — Evidence Engine: an `EvidenceItem` model with `relationship` ∈
`SUPPORTS` / `CONTRADICTS` / `CONTEXT`, claim → evidence → source wiring on top
of the `src_`/`res_` ids Phase 4 already produces, and source credibility
ranking. The hard invariant carries over unchanged: no fabricated sources, ever.

---

## Phase 5 — Evidence Engine

**Date:** 2026-10-01
**Phase:** 5 — Evidence Engine
**Tests:** 1161 passing (793 before Phase 5, +368), fully offline

### What was implemented

- `app/schemas/evidence.py` — `EvidenceType` (5), `EvidenceRelation` (5) and
  `EvidenceRelevance` (3) as three independent axes, plus frozen `EvidenceSource`,
  `EvidenceItem`, `EvidenceResponse` and `EvidenceBundleResponse`. Provenance is
  enforced at construction: a non-empty `excerpt`, a `claim_id`, a `source`, an
  `excerpt_origin` in `ALLOWED_EXCERPT_ORIGINS`, an `ev_`-prefixed id, and a rule
  rejecting any item that belongs to another claim or cites an unlisted source.
  Counts are recomputed, so they cannot drift from the items.
- `app/services/evidence/source_normalizer.py` — Phase 3 `SearchResult` →
  `EvidenceSource`, reusing `canonicalize_url()`, `extract_domain()`, Phase 4's
  `source_id_for()` / `result_id_for()` and `resolve_authority()`. Nothing is
  re-derived: `source_type` is Phase 3's classification and `source_tier` is
  Phase 4's registry result, both carried through.
- `app/services/evidence/relationship.py` — the layer's **only** judgement. Its
  input is Phase 4's `AssessedSource` plus the claim's status and reason code. A
  document can only be `SUPPORTS`/`CONTRADICTS` when Phase 4 read a non-neutral
  stance on a claim-relevant, identity-matched document *and* relied on such a
  document for that reason code.
- `app/services/evidence/evidence_builder.py` — verbatim excerpts (`snippet`, else
  `title`; never merged), `MAX_EXCERPT_CHARS = 400` truncation on a word boundary,
  derived `ev_` ids, `group_by_query()` query provenance, and
  `status_supports_evidence()`, which makes a `NOT_APPLICABLE` bundle structurally
  impossible to fill.
- `app/services/evidence/evidence_dedupe.py` — de-duplication keyed on claim +
  canonical URL + result id + excerpt origin + normalized excerpt + relationship,
  first seen wins; ordering on Phase 4 `TIER_PRIORITY`, relevance, relationship,
  provider position and stable id; `distinct_sources()`.
- `app/services/evidence/evidence_service.py` — `EvidenceService` and
  `build_evidence_service()`, per-claim and batch assembly, fixed no-evidence
  warnings per reason code, and coverage warnings when a `VERIFIED` or
  `CONTRADICTED` status has nothing showable.
- `app/scripts/manual_evidence.py` — offline fixture pass plus an optional live
  pass. Six scenarios: confirmation, contradiction, conflicting records, a
  similarly named company on a regulator's site, a lookalike domain, and a
  promised return with no register to check it against.
- Tests: `tests/evidence_factories.py` plus eight new modules covering schemas,
  normalization, relationship derivation, construction, de-duplication and
  ordering, service orchestration, the Phase 3 → 4 → 5 chain, and package
  invariants (no HTTP client, no provider reference, no `.search()`, no
  summariser, no `classify_domain()`, no local tier table, no randomness).

### Decisions recorded

- **D-023 — Evidence type, relationship and relevance are three separate facts.**
  The longer taxonomy considered for this phase (`CLAIM_SUPPORT`,
  `CLAIM_CONTRADICTION`, `CONTEXTUAL`, …) was rejected: it encodes what the other
  two axes already carry, and two copies of one fact eventually disagree. Three
  structural rules ship with it — verbatim excerpts with a mandatory
  `excerpt_origin`, no evidence without a retrieved document, and no citation of a
  document Phase 4 did not record.
- **D-024 — Evidence explains verification; it can narrow it, never widen it.**
  A supporting cue in a document that failed the identity or authority gate is
  `CONTEXT`/`MENTIONS`, never `SUPPORTS`. `verification_status` is copied, and
  where status and showable evidence diverge the bundle keeps Phase 4's verdict
  and warns.

### Issues found and fixed during review

**A document cited only by `src_` id was dropped from its own bundle.**
`EvidenceService._restrict_to_seen_results()` filtered supplied results against
`matched_result_ids` alone. Phase 4 records two id families — `source_ids` and
`matched_result_ids` — and a document can appear in only the first. Such a
document was therefore excluded from the evidence explaining it. Both families are
now checked.

**A list of warnings was passed where a single string was expected.** Any claim
whose supplied results were all filtered out reached `EvidenceResponse` with a
`list[str]` in `warnings`, raising `ValidationError` instead of returning an
empty bundle. `_empty()` now accepts either form and de-duplicates through the
same helper as the populated path. The failure mode was the worst available for
this product: a crash on the "nothing found" path rather than an honest empty
result.

### Behaviours narrowed after review

**`CONTEXT` now requires an authoritative publisher.** A similarly named party
documented by a relevant authority is context a reader can act on; the same
ambiguity on an arbitrary web page is only a `MENTION`. Previously any
`AMBIGUOUS` identity produced `CONTEXT` regardless of who published it.

### Corrected in the manual script

**`manual_evidence.py` bypassed Phase 3's classification.** The script handed
`EvidenceService` hand-built `SearchResult` objects, so the demo printed
`sebi.gov.in (UNKNOWN)` next to `TIER_1_PRIMARY_REGULATOR` — the publisher
category and the authority tier disagreeing in one line. That was the script's
shortcut, not a normalizer bug: `SearchService._enrich()` assigns `source_type`,
and a caller that skips the service skips the classification. The script now
retrieves through `SearchService` exactly as `VerificationService` does, so the
results handed to Phase 5 are the classified, de-duplicated ones an earlier phase
produced. Worth keeping as a reminder for Phase 7 wiring: **every real caller goes
through `SearchService`, and Phase 5 is handed its output rather than raw
provider results.**

### Deliberately not built

- No numeric "evidence strength" score, and no way to derive one: `relevance` is a
  label. D-007 requires transparent weighting, and a number here would silently
  become the Phase 6 risk input.
- No page fetching, HTML parsing or document text. The layer has only ever seen
  title and snippet, and `excerpt_origin` says so.
- No persistence, no HTTP surface, no risk scoring, no entity legitimacy
  judgement.

### Limitations

- The live SerpAPI path has still never run: `SERPAPI_KEY` is absent, so evidence
  over real provider output is unexercised.
- Evidence is snippet-based for the same reason Phase 4 is. A reader cannot always
  confirm a finding from a snippet alone, and the report must state that.
- Excerpts are truncated at 400 characters on a word boundary — always a prefix of
  real text, never a rephrasing.
- Coverage counts are per claim. Nothing yet reports cross-claim source diversity,
  which Phase 6 will need for its weighting.
- Nothing is persisted; `sources` and `evidence` rows are Phase 9 work, with the
  column list already aligned to the Phase 5 schemas.

### Next step

Phase 6 — Risk Engine: weighted, transparent indicator accumulation over Phase 1
red flags, Phase 4 statuses and Phase 5 counts, with a per-factor breakdown and no
probabilistic language. The constraint carried forward from this phase is that
`EvidenceRelevance` must never be summed into a number — Phase 6 consumes
`proof_count` and `source_count` as counts and shows the items behind them.

---

## Phase 6 — Risk engine

**Date:** 2026-10-02
**Phase:** 6 — Risk engine

### What was implemented

- `app/schemas/risk.py` — `RiskLevel`, `VerificationFactorType`,
  `RiskFactorOrigin`, `RiskFactor`, `RiskWeights`, `RiskThresholds`,
  `RiskAssessment`, plus `RISK_RELEVANT_CLAIM_TYPES` and the
  `SCORE_NOT_A_PROBABILITY` caveat. Most Phase 6 guarantees are validation rules
  rather than conventions: `contribution <= weight`, an absorbed factor must
  contribute `0`, `raw_score` must equal the sum of contributions, `risk_score`
  must be that sum capped at the ceiling, bands must be strictly increasing, and
  a zero-factor assessment must score `0`. Derived counts are recomputed from
  `factors` so a report cannot show numbers that disagree with the breakdown.
- `app/services/risk/risk_scoring.py` — weight and threshold snapshots, band
  assignment with inclusive bounds, and the score arithmetic. Red-flag weights
  are resolved through Phase 1's `RULES_BY_CODE` and each rule's `weight_attr`.
- `app/services/risk/risk_factors.py` — Phase 1 and Phase 4 objects to
  `RiskFactor`, with the status-to-factor-type mapping, the completed-search
  reason-code gate, fixed template wording, and derived `rf_`/`rsk_` ids.
- `app/services/risk/risk_aggregation.py` — the de-duplication layer. Collapses
  duplicate red flags and duplicate verification results, links claims to
  red-flag factors by span overlap or claim-type map, attaches evidence ids, and
  absorbs a verification factor that repeats an already-counted signal.
- `app/services/risk/risk_service.py` — `RiskService` composing the above, with
  `evidence_coverage`, `analysis_completeness` and five fixed warnings. The
  service holds no state, performs no I/O and makes no network call.
- `app/scripts/manual_risk.py` — offline smoke test in three passes: nothing
  found, one signal seen by four stages, and search unavailable.
- Four new `Settings` fields: `risk_weight_contradicted_claim` (25),
  `risk_weight_unverified_claim` (8), `risk_weight_insufficient_evidence` (0)
  and `risk_score_ceiling` (100).

### Files created

```
backend/app/schemas/risk.py
backend/app/services/risk/__init__.py
backend/app/services/risk/risk_scoring.py
backend/app/services/risk/risk_factors.py
backend/app/services/risk/risk_aggregation.py
backend/app/services/risk/risk_service.py
backend/app/scripts/manual_risk.py
backend/tests/risk_factories.py
backend/tests/test_risk_schemas.py
backend/tests/test_risk_scoring.py
backend/tests/test_risk_factors.py
backend/tests/test_risk_aggregation.py
backend/tests/test_risk_service.py
backend/tests/test_risk_safety.py
backend/tests/test_risk_package.py
```

### Files modified

```
backend/app/core/config.py   (four risk_weight_*/ceiling settings)
```

### Tests

385 new tests, all offline. Suite total: **1546 passed, 4 deselected** — up from
1161 with no regressions in Phases 0–5.

### Bugs found and fixed during this phase

1. **Every red-flag weight resolved to `0`.** `RiskWeights.red_flag_weights` was
   keyed by `code.value.lower()` while `weight_for` looked up with the uppercase
   `RedFlagCode.value`, so no code ever matched. The symptom was an assessment
   reporting red-flag factors and scoring `0` — the worst possible failure shape,
   because the breakdown looked correct. Two changes: the table is now keyed by
   the `RedFlagCode` enum, and weights are resolved through Phase 1's rule table
   rather than by reconstructing `f"risk_weight_{code.value.lower()}"`.
2. **`RISK_RELEVANT_CLAIM_TYPES` imported from the wrong module.** It is defined
   in `app.schemas.risk`; the import in `risk_factors.py` and `risk_service.py`
   pointed at `app.schemas.claims` and failed at package import.
3. **`UNVERIFIED_CLAIM` was wrongly exempted from de-duplication.** The first
   absorption rule skipped any factor with `is_uncertainty`, which includes
   `UNVERIFIED_CLAIM` — a weighted factor that genuinely double-counts against
   the matching red flag. Caught by `test_nothing_scores_twice_for_one_claim`.
   The rule is now "a factor that already weighs nothing is not absorbed", which
   covers `INSUFFICIENT_EVIDENCE` without letting a real finding count twice.

### Behaviour narrowed during this phase

4. **`INSUFFICIENT_EVIDENCE` factors no longer receive an `absorbed_into`
   pointer.** The primary red flag counted a *pattern*; pointing at it implied it
   had also accounted for a verification it never performed. Such factors are now
   kept visible at `contribution = 0` with no pointer. `UNVERIFIED_CLAIM`, which
   is also marked `is_uncertainty` but does carry weight, is still absorbed.
5. **`ZERO_RESULTS` is separated from the search-failure reason codes.** The two
   are different facts: "we could not look" versus "the register was reached and
   held no matching entry". They now raise different warnings
   (`SEARCH_DATA_UNAVAILABLE` versus `REGISTER_NO_MATCH`), and the second is an
   observation about the public record rather than a gap in the analysis.

### Pre-existing issue found, not fixed here

6. **Phase 1's `SUSPICIOUS_URL` description asserts the word "fraudulent".** It
   reads "A link was found that uses a pattern commonly seen in fraudulent
   campaigns." Phase 6 reuses Phase 1's rule text verbatim by design, so the
   phrase reaches every risk report. Rewording it is a Phase 1 decision with its
   own tests and rationale, so it is pinned by
   `TestPhaseOneCatalogueTripwire::test_the_known_judgement_wording_in_phase_one_is_unchanged`
   rather than silently changed from here. The tripwire fails if a new occurrence
   appears, so the set cannot grow unnoticed.

### Notable design points

- The vocabulary ban in `test_risk_safety.py` is **negation-aware**. A blunt
  word-boundary ban cannot be satisfied without suppressing the disclaimers that
  carry the product's promise — the standing caveat says the score "is not a
  probability of fraud", and Phase 1 says a credential detection is "not a
  finding that the person is unverified or fraudulent". Both name the banned
  word in order to rule it out.
- The advice ban applies only to text Phase 6 authors. A rule may name the
  activity it detects: `BORROW_TO_INVEST` is named "Borrowing to Invest", which
  describes a pattern rather than advising the reader.
- `SCORE_NOT_A_PROBABILITY` is enforced by `RiskAssessment` validation rather
  than by the service, so no caller can omit it.
- The headline end-to-end case — a "SEBI approved" claim fired on by Phase 1,
  extracted by Phase 2, unverified by Phase 4 and evidenced by Phase 5 — scores
  `25` from the red flag and `0` from the absorbed claim factor, not `33`.

### Known limitations

- No live SerpAPI run; `SERPAPI_KEY` is absent, so a real Phase 4 result has
  never been scored. Every test drives the engine with synthetic but
  schema-valid Phase 4 and Phase 5 objects.
- The bands (`20/50/90/100`) and every weight are product heuristics, not
  calibrated or empirically validated.
- `CLAIM_TYPE_RED_FLAG_CODES` is a hand-built map. A claim family that is
  genuinely the same signal as a rule but missing from the map would be scored
  twice.
- `evidence_coverage` counts claims with proof-grade evidence, not cross-claim
  source diversity, which Phase 5 flagged as unaddressed and still is.
- Nothing is persisted; `RiskAssessment` is returned in memory only.
  `RiskService.assess_batch` exists for Phase 7; writing is Phase 9 work.

### Next step

Phase 7 — LangGraph orchestration: compose Phases 2–6 into one stateful graph
over a typed state, so no stage re-derives another's work. `assess_batch` is the
call shape. Determinism must be preserved or stored reports stop being
auditable, every typed error code from Phases 3–5 must surface as a report
limitation, and the vocabulary ban must keep passing unchanged.

---

## Phase 7 — LangGraph Orchestration

**Commit:** `feat: implement LangGraph investigation orchestration`
**Tests:** 291 added. Suite result recorded in `CURRENT_STATE.md`.

### What was added

`langgraph==1.2.12`, verified to install and run on CPython 3.14.5 (Windows).
Only `StateGraph`, `START`/`END` and the runtime context are used — no agent, no
model, no tool-calling.

The graph package, seven test modules, and a runnable `manual_graph.py` that
demonstrates three passes offline: a full pipeline with a fixture provider, a
degraded run with search unavailable, and a rejected empty submission.

### The seam between Phase 4 and Phase 5

Wiring Phase 5 into the pipeline surfaced a real gap. `VerificationService`
performs the searches and discards the `SearchResponse` objects, because nothing
downstream asked for them; `EvidenceService.build_all` needs them to know which
query surfaced which document.

The obvious fix — having the graph run the searches itself — would duplicate every
network call, and the second copy could rank differently from the first, putting
evidence in front of a verification that never saw the underlying document. That
is precisely the fabrication D-023 forbids, so it was rejected.

`RecordingSearchService` wraps the real `SearchService`, delegates every call
unchanged, and keeps the responses. Recording is a side effect of delegation
rather than a separate step, so there is no way to search through it without the
response being kept. The per-claim grouping is reconstructed through
`VerificationResult.queries`, which Phase 4 populates from the responses it
actually received.

Verified end to end: a run against a fixture provider shows an evidence item
carrying the exact query Phase 4 issued.

### Bugs found and fixed

1. **A failed stage continued the run.** Only the input node was guarded. A run
   whose extraction raised still went on to produce a full `risk_assessment`, and
   a failed red-flag pass reported "no patterns found". Both understate risk
   while looking like complete investigations. Fixed by applying the same
   `route_on_recorded_error` predicate after every stage (D-032).
2. **The evidence abort edge was missing.** The first loop over the stage chain
   excluded the final pair, so an evidence-stage error would have continued to
   risk. Evidence cannot currently record an error, but the guard belongs there
   anyway rather than depending on that staying true.
3. **`analysis_completeness`/`assessed_at` broke whole-state comparison.**
   `RiskAssessment.assessed_at` is stamped by Phase 6 from a real clock, so two
   runs can never be byte-identical. Added `semantic_view` and
   `TIMESTAMP_FIELDS` (D-034).
4. **A factory named `test_settings` was collected as a test.** Pytest collects
   imported callables whose names begin with `test_`. Renamed to
   `offline_settings`.
5. **Test settings leaked the developer's `.env`.** `offline_settings()` now
   passes `_env_file=None`, so "fully offline" is a property of the tests rather
   than of the machine they run on. Without it a local `.env` supplying a real
   `SERPAPI_KEY` would have let a test quietly reach the network and still pass.

### A pre-existing issue found, not fixed here

`tests/test_health.py::test_health_reports_database_state` asserts the health
endpoint reports the `sqlite` dialect, but `/api/health` probes the
**module-level** `engine` in `app/db/session.py`, which is built from
`get_settings()` rather than from the settings injected into `create_app`. When a
repository-root `.env` sets `DATABASE_URL` to PostgreSQL, that engine is
PostgreSQL and the assertion fails however the app was constructed.

This is out of Phase 7's scope — it is a Phase 0/8 concern about how the health
route resolves its database — and it reproduces identically with the Phase 7 tests
excluded, so it is not caused by this phase. Recorded here rather than patched,
because the correct fix belongs with the API layer and changing it from here
would scope-creep into a completed phase. See `CURRENT_STATE.md`.

---

## Phase 8 — API layer (FastAPI endpoints)

**Commit:** `feat: implement investigation API layer`
**Tests:** 112 added, and the one pre-existing failure fixed. Suite is fully green.

### What was added

`app/schemas/api.py` holds every shape a client sees: two request models, the
investigation response, the timeline/limitation/error projections, and the error
envelope with its structured `detail`. All of them are `extra="forbid"`, so a
typo'd request field and a drifted response field both fail the suite instead of
passing silently.

`app/api/adapters.py` turns a final `InvestigationState` into an
`InvestigationResponse`. It is the only place that decides the top-level `status`,
and it decides it by reading the stage timeline — not the warning list.
`NO_RED_FLAGS_DETECTED` is recorded on any clean content, so treating "warnings
exist" as "the run is degraded" would badge every benign investigation as
incomplete (D-037).

`app/api/errors.py` is the error taxonomy. Every failure renders as
`{"error": {"code", "message", "detail"}}`; what differs is the status code, chosen
by *whose fault the failure is*. `INPUT_EMPTY` and `INPUT_TYPE_NOT_SUPPORTED` are
`422` — the caller must fix them. Everything else in `ERROR_CODES` is a stage
breaking a documented contract and is `500`, because the only honest description
of that is a defect.

`app/api/routes/investigations.py` exposes `POST /api/investigations/text`,
`POST /api/investigations` and `GET /api/investigations/limits`. Handlers are thin:
validate, call `run_investigation`, map recorded errors, serialize.

`tests/api/` adds five modules: the endpoint contract, the adapter, the error
mapping, the schemas, and a regression module for the health fix below.

### The `/api/health` database bug, fixed

The one outstanding suite failure. `app/api/routes/health.py` probed the
**module-level** `engine` in `app/db/session.py`, built from `get_settings()` at
import time, rather than from the settings injected into `create_app`. Two
consequences, both real rather than theoretical:

- A test passing its own `Settings` still probed the developer's actual database.
  The suite was reporting on the machine it ran on, not the app under test.
- With a repository-root `.env` pointing `DATABASE_URL` at an unreachable
  PostgreSQL, health reported `degraded` for an app whose configured SQLite
  database was perfectly fine. A wrong answer, not a missing one.

The fix builds one `Database` from the settings the factory was given, parks it on
`app.state.database`, exposes it through `get_database_dep`, and disposes it in the
`lifespan` handler so no connection pool outlives the app. `tests/api/test_health_boundary.py`
points `DATABASE_URL` at a deliberately unreachable Postgres and asserts the
endpoint still reports the app's own SQLite — the original failure, reproduced
deterministically.

### The credential leak in `probe_database`

`probe_database` returned `f"{type(exc).__name__}: {exc}"`. SQLAlchemy and most
drivers put the DSN — including credentials — in the exception text, so any caller
surfacing that string surfaced a password. It now returns the exception *type*
only and logs the full cause. `tests/api/test_health_boundary.py` builds a session
factory whose error message is a DSN with a password in it, and asserts that
neither the password nor the DSN appears in the probe result.

### Decisions recorded

- **D-036** — the run returns synchronously and whole. The spec's `202` plus
  `GET /{id}` design presupposes persistence, which is Phase 9; implementing the
  first half alone would hand a client an id with nothing behind it. Adding
  retrieval later is purely additive.
- **D-037** — run status comes from the stage timeline, never the warning list.
- **D-038** — domain models are embedded in the response, not re-projected. A
  second definition of "a claim" in the API would drift from Phase 2's, and every
  drift would be a silent wrong answer rather than a type error.
- **D-039** — the API layer interprets and never computes. `RiskAssessment` is
  forwarded verbatim, caveat included.

### Bugs found and fixed

1. **`probe_database` leaked connection details.** See above.
2. **Starlette deprecation warnings across the suite.** `HTTP_422_UNPROCESSABLE_ENTITY`
   is deprecated in favour of `HTTP_422_UNPROCESSABLE_CONTENT`. Resolved once in
   `app/api/errors.py` and exported as `UNPROCESSABLE_CONTENT`, so `app/main.py`
   and the new modules agree on one constant. The suite is now warning-free.
3. **`create_app` leaked a `Database` per app.** The factory built one per call and
   nothing disposed it. Now stored on `app.state` and disposed in `lifespan`.

### Tests

```
1997 passed, 4 deselected
```

`tests/api/` (114 tests): 39 endpoint contract, 22 adapter, 23 schemas, 21 error
mapping, 9 health-boundary regression. Baseline before Phase 8 was
`1882 passed, 1 failed`.

Every test runs offline. `tests/api/conftest.py` passes `_env_file=None`, clears
`GROQ_API_KEY`/`SERPAPI_KEY`/`DATABASE_URL` from the environment, points SQLite at
a per-test tmp file, and overrides `get_graph_context_dep` with the genuine Phase
1-6 services plus a fixture search provider. The end-to-end tests therefore assert
what the real pipeline produces, not what a stub was told to return.

The degradation test is the one that matters most: it installs a provider that
reports itself unavailable and **raises if ever queried**, then asserts `200`,
`status: PARTIAL`, and `SEARCH_UNAVAILABLE` in `limitations`. A product that fails
the request when search is down is exactly the behaviour this project exists to
avoid.

### Not implemented, deliberately

- **Persistence and `GET /api/investigations/{id}`** — Phase 9 (D-036).
- **URL, image and PDF ingestion** — recognised and refused with `422`; the error
  detail names the kinds that do work.
- **Report generation, `summary`, `why_flagged`, `safety_guidance`** — Phase 10.
  Adding any of them now would create a second, unvalidated restatement of
  judgments the pipeline already made (D-039).
- **Translation** — `language` is accepted and echoed; no message is translated.

---

## Phase 9 — Database persistence

**Date:** 2026-10-02
**Phase:** 9 — Database models & repositories

### What was implemented

**Schema — 14 tables, `backend/app/models/investigation.py`.**

`investigations`, `claims`, `entities`, `claim_entity_links`, `red_flags`,
`verification_results`, `sources`, `evidence_responses`, `evidence`,
`risk_assessments`, `risk_factors`, `timeline_events`, `investigation_warnings`,
`investigation_errors`.

`app/models/__init__.py` imports every table, which is what registers them on
`Base.metadata` and therefore what `Database.create_all()` materialises. A model
that is never imported is never registered, and a table that is never registered
is silently missing.

**Portability — `backend/app/db/types.py`.**

`UtcDateTime`, a `TypeDecorator` that writes naive UTC and reads back aware UTC.
SQLite has no timezone type, so a bare `DateTime` would return naive values there
and aware ones on PostgreSQL. The asymmetry does not fail on the SQLite path — it
fails much later, as `can't subtract offset-naive and offset-aware datetimes`, in
code that only runs on the hosted database. `compare_values` normalises before
comparing so a `WHERE created_at = :ts` filter matches on both backends.

**Repository — `backend/app/repositories/investigations.py`.**

`save(state)`, `load(public_id)`, `find(public_id)`, `list_page(limit, offset)`,
`delete(public_id)`, plus `commit()`/`rollback()` so the transaction boundary is
stated at the call site that owns it.

The load-bearing decision: `load()` returns a reconstructed `InvestigationState`
rather than a bespoke result type, so the Phase 8 adapter shapes both the live
and the retrieved investigation. There is exactly one code path that produces a
response body (D-041).

**Endpoints — `backend/app/api/routes/investigations.py`.**

- `POST /api/investigations/text` and `POST /api/investigations` now store what
  they return. The response shape is unchanged, so a Phase 8 client keeps working.
- `GET /api/investigations/{id}` — a stored run, rebuilt and shaped by the same
  adapter. `404` via a new `InvestigationNotFound`, with no detail in the body.
- `GET /api/investigations` — run history, newest first, `limit`/`offset` paged,
  reading only the `investigations` table.

`/investigations/limits` is declared **before** the parameterised route. FastAPI
matches in declaration order, and the reverse order made a Phase 8 endpoint
return a `404` about a missing investigation.

**Wiring.**

`get_db(request)` now reads the session factory from `app.state.database` rather
than the module-level `SessionLocal`, so the module-level engine is off the
request path entirely. `get_repository_dep` composes the `Database` and the
request-scoped session. The `lifespan` handler calls `database.create_all()` once,
wrapped so a database that is reachable but not writable does not stop the
read-only endpoints from starting.

### Notable corrections to the Phase 0 schema sketch

The sketch predated the Phase 1-6 schemas and described fields the pipeline does
not have. Rather than building a second version of every model, the schema follows
the models. The full diff is tabulated in `docs/DATABASE_SCHEMA.md`; the
substantive ones:

- **`investigation_id` is not unique.** It is a digest of the submitted content,
  so a unique constraint would make re-running content an error and would
  destroy the evidence that content was checked twice. `id` is the primary key;
  the digest is preserved in `public_id` so no Phase 8 client is broken (D-040).
- **`red_flags` is not unique on `(investigation_id, code)`.** Phase 1 emits one
  flag per span, each with its own `rf_` id, and Phase 6 cites those ids
  individually. The key is `(investigation_id, code, span_start, span_end)`.
- **`sources` is keyed on `(source_id, result_id)`, not `source_id`.** Phase 5
  derives `ev_` ids from both; keying on the document alone would force one
  retrieval to borrow another's `result_id`, and evidence citing it would then
  report provenance that was never fetched.
- **No `caveat` column on `risk_assessments`.** The caveat is
  `SCORE_NOT_A_PROBABILITY`, appended to `warnings` by `RiskAssessment` itself.
  Rebuilding the assessment through the model reproduces it, which is stronger
  than storing a copy free to drift (D-044).
- **No `users` and no `reports` tables.** `reports` is Phase 10; an empty
  placeholder records a decision nobody made (D-043).
- **Status values are `COMPLETED | PARTIAL | FAILED`**, not the sketch's
  `PENDING | PROCESSING | COMPLETED | FAILED`. The graph is synchronous, so there
  is no queued or running state, and `PARTIAL` is what the sketch lacked.

### Bugs found and fixed during the phase

1. **`relationship` shadowed the `relationship()` function.** The evidence table
   has a column named `relationship`, which in a declarative class body hides the
   SQLAlchemy function for the rest of the class. Symptom:
   `TypeError: 'MappedColumn' object is not callable`. Fixed by naming the Python
   attribute `relationship_` and mapping it to the database column `relationship`,
   the same pattern already used for `metadata`.
2. **Duplicate index name.** An explicit `Index("ix_evidence_claim_id", ...)`
   collided with the `index=True` on `claim_id`. The composite was renamed to
   `ix_evidence_investigation_claim`, and a redundant
   `ix_investigations_created_desc` (identical to the `created_at` index) was
   removed.
3. **Evidence items had a null `investigation_id`.** An item reaches the
   `investigations` table through its response, and SQLAlchemy populates a
   foreign key only from the relationship that references it. The parent is now
   passed explicitly.

### Test results

```
cd backend && python -m pytest
2085 passed, 4 deselected
```

Baseline before Phase 9 was `1997 passed, 4 deselected`. **88 tests added**, none
modified or removed.

| File | Tests | Covers |
| --- | --- | --- |
| `tests/db/test_round_trip.py` | 17 | State ⇄ rows fidelity, per-collection round trips, the caveat, the risk trace, `red_flag_id_for` recomputability |
| `tests/db/test_history.py` | 21 | Listing, paging stability, the shared-id case, deletion and cascade, timestamp round trips |
| `tests/db/test_schema.py` | 29 | Table set, uniqueness rules, cascade, absence of secret-bearing columns, PostgreSQL DDL compilation |
| `tests/api/test_persistence_endpoints.py` | 21 | Store-on-write, retrieval equality, the `404`, history, and the Phase 8 contract holding |

Two fixture contexts back the round-trip tests. `real_context` runs the genuine
Phase 1-6 services offline. `rich_context` replaces only extraction and red-flag
detection, because the deterministic fallback emits **no entities** and its
guarantee claims are not externally verifiable — a suite built only on the
default context would have tested `entities` and `claim_entity_links` against
empty tables and reported full coverage. Verification and evidence stay real in
both, so the search recorder populates and the evidence node has something real
to assemble.

### Known limitations carried forward

- **No authentication, therefore no ownership check.** Anyone holding an id can
  read that investigation. Acceptable only because there is nothing to
  authenticate against; it needs revisiting before there is.
- **No migrations.** `create_all()` creates missing tables and will not add a
  column to an existing one. Fine while the schema is still changing and the
  database is disposable; Alembic is the right tool the first time a deployed
  database must be migrated in place.
- **No live PostgreSQL run.** DDL is verified by compiling every table and index
  against the PostgreSQL dialect, and `UtcDateTime` handles the offset asymmetry,
  but nothing has connected to a real PostgreSQL server.
- **No `DELETE` endpoint**, though the repository supports it.
- **`limit`/`offset` paging only.** Fine at this scale.

### Carried into Phase 10

`docs/DATABASE_SCHEMA.md` has been rewritten to describe the schema as
implemented, with the differences from the Phase 0 sketch tabulated rather than
quietly dropped. The risk trace Phase 10 will need — factor → claim → red flag →
evidence → source — is stored and round-trips, so a report is a read rather than a
reconstruction. Whether reports are a stored snapshot, a rendered artefact, or
both is still an open decision; no `reports` table was created.


## 2026-10-02 — Phase 10 — Testing & Quality Hardening

**Scope.** Coverage, isolation and contract proof. No new product behaviour. The
architecture in `ARCHITECTURE.md` is unchanged; the only production-code changes were
two defect fixes below.

**Phase naming.** Two documents disagreed about what Phase 10 is.
`IMPLEMENTATION_PLAN.md` has always called it "Backend Test Suite";
`CURRENT_STATE.md` called it "Report generation", which is `AI_PIPELINE.md` Stage 11
— a pipeline stage, on a different numbering axis. Phase 10 is **Testing & Quality
Hardening**. Report generation is not a numbered project phase, is not started, and
was deliberately not renumbered into one: giving it a number is the kind of premature
decision `DATABASE_SCHEMA.md` avoided when it declined to create the `reports` table.

**Tests: 2085 → 2510 passed, 4 deselected, 0 failed.** 425 added. Two production
defects found and fixed; one guard that was never installed; two more bugs in the
Phase 10 additions themselves.

### Defects found and fixed in production code

**1. The 422 handler crashed on form-encoded bodies.** It assumed Pydantic's `input`
value was JSON-serialisable. For a form body it is raw `bytes`, so serialising it
raised `TypeError` *inside* the error handler, which fell through to a generic
`500 INTERNAL_ERROR`. A clean `422` became an internal error, and it did so with a
submitted credential in the payload. Fixed by `_validation_detail()` in
`app/main.py`, which copies only `type`, `loc` and `msg` — excluding submitted
values by construction rather than by filtering. Pinned by
`test_a_credential_in_form_data_is_not_reflected`.

**2. No handler for `StarletteHTTPException`.** A `404` on an unknown path and a
`405` on a wrong method bypassed the error envelope entirely and returned FastAPI's
default body. Added a handler mapping `404 → ROUTE_NOT_FOUND` and
`405 → METHOD_NOT_ALLOWED` in the documented envelope.

### A guard that was never installed

`tests/network_guard.py` defined an `autouse` fixture and `tests/conftest.py`
imported the module. **That registered nothing.** An `autouse` fixture is only
collected from a file pytest treats as a conftest or a plugin, so the guard existed,
was importable, and had never once blocked a connection. The suite was green either
way, and nothing in 2085 tests noticed — a test that reaches the network usually
still passes, which is what made the absence invisible.

The fixture now lives in `tests/conftest.py`. `tests/test_network_guard.py` attempts
a connection from inside an ordinary test and requires it to be blocked, which is the
only kind of test that can catch this class of mistake. Two further bugs surfaced
while writing it: `allow_network(host)` did not work as documented for DNS (a
wildcard port never matched the concrete port `getaddrinfo` is handed), and the
loopback exemption it needed for `TestClient` was briefly written as a `127.` prefix
check, which would have allowed the registrable domain `127.0.0.1.example.invalid`.
Both are now pinned from both sides.

### What was added

- **API input contract** — 62 tests. Valid multilingual, Unicode, multiline, CRLF and
  boundary-length input; missing, null, wrong-type, oversized, unknown-field, list,
  object, string and numeric bodies; malformed JSON and form data; error-envelope
  discipline; 404/405 envelopes; credential non-reflection.
- **OpenAPI contract** — 46 tests. Endpoint inventory, schema bounds, enums, paging,
  error documentation, OpenAPI 3.1, dangling references, and the absence of internal
  ORM, repository and graph schemas from the public surface.
- **Graph wiring** — 34 AST and runtime tests. The production route calls
  `run_investigation` and `serialize_investigation`; it does not bypass the graph or
  reach for a hidden engine. Stage execution counts, input immutability, context
  reuse, stage order, response shape, deterministic id derivation, frozen
  dependencies and recorder sharing.
- **Status semantics** — 36 tests. `COMPLETED` / `PARTIAL` / `FAILED` and stage-level
  `SKIPPED`, derived from the stage timeline and never from the warning list
  (D-037). A search that ran and found nothing is a real attempt, not
  `SEARCH_UNAVAILABLE`; an unavailable provider requires an injected one.
- **Risk safety** — 34 tests, plus the Phase 6 machinery moved to
  `tests/vocabulary.py` so one matcher defines a violation. The vocabulary is now
  checked over the adapter, the JSON response, the database, the `GET` response, the
  history entry, error bodies and the OpenAPI document — not only the engine. The
  `SCORE_NOT_A_PROBABILITY` caveat is asserted live, stored, reloaded and in history.
- **Secret leakage** — 33 tests. A DSN, a provider key, a `GROQ_API_KEY`, a DSN
  quoted inside a provider error, and credential-shaped values in six request
  positions. Nothing reaches a response.
- **Failure injection** — 58 tests across every stage, plus the semantic-
  reproducibility property that a given failure produces the same result every time.
- **Network guard** — 24 tests, described above.
- **Determinism** — 68 tests. The content-derived id, and semantic reproducibility
  across the graph, storage and HTTP. Records that the fixed test clock covers the
  graph but not the services, which is precisely why `semantic_view` exists.
- **Retrieval contract** — 14 tests. Semantic equivalence field by field, the
  timestamp contract, and the limit of it: two runs of identical content share an id
  and are not one retrievable body.
- **Query cost** — 16 tests. Flatness in the data rather than a magic number, at both
  the repository and the HTTP boundary, plus the check that a list row carries no
  payload.

### Known limitations recorded, not fixed

Each of these would mean redesigning something Phase 10 was explicitly not scoped to
redesign. They are in `CURRENT_STATE.md` and each is pinned by a test.

- `GET /api/investigations/limits` is typed `dict[str, object]`, so OpenAPI shows an
  inline `additionalProperties: true`. The response is correct; only the schema is
  loose. Fixing it means adding a response model for a schema generator's benefit.
- Graph nodes do not validate every malformed dependency return, so a broken contract
  can surface as `AttributeError` rather than the stage's typed failure code. The
  safety-relevant part — an error is recorded and the run does not score — is
  covered; the specific error code is not, and fixing it is a Phase 7 change.
- `psycopg2` connects through libpq, which never enters Python's `socket` module, so
  the guard cannot see a database connection. This is why the poison DSN points at
  loopback port 1: refused in milliseconds, and no test points a database at a
  routable address.
- `/api/health` reports the *names* of credential environment variables and is
  unauthenticated. No value leaks and the `configured` boolean is unaffected, but the
  wording is reconnaissance on a public deployment. Recorded rather than changed,
  since it is Phase 8 behaviour and useful to an operator.

### Documentation corrections

- `CURRENT_STATE.md` said a retrieved investigation is **byte-identical** to the live
  one. The guarantee is **semantic equivalence**. Byte equality does hold today for
  `POST`-then-`GET`, because `started_at` is stored rather than regenerated — but the
  contract does not depend on it, and two runs of identical content are *not*
  byte-identical to each other while sharing an id. Corrected, with both the exact
  and the semantic comparison asserted so neither claim is doing unstated work.
- The claim that `completed_at` is populated was corrected. No node has ever written
  it: the key is absent from the state and the adapter renders `null` (D-049).

### Not done, deliberately

No coverage percentage: `pytest-cov` is not installed and was not added for one
number. `ruff`, `mypy` and `hypothesis` likewise absent and not added. No frontend,
authentication, OCR, report generation, URL ingestion or new product features. The
historical credential in Git history was not rewritten and not rotated — that is an
operational action, and history rewriting is out of scope. The current tree is clean:
`.env` is gitignored, every settings factory passes `_env_file=None`, and no `.env`
value is read, printed or asserted on.

---

## Phase 11 — React Frontend

**Date:** 2026-10-02
**Phase:** 11 — React frontend
**Commit:** `feat: implement React frontend`

### What was implemented

A new `frontend/` application: Vite + React 18 + TypeScript (strict), Tailwind CSS v4,
shadcn/ui-style primitives, Lucide React, Recharts, react-router-dom. Five routes plus a
not-found page: `/`, `/dashboard`, `/investigate`, `/investigation/:id`, `/history`.

- `src/types/api.ts` — a TypeScript mirror of the backend contract. Every union and field
  was read from the **running** FastAPI app (`app.openapi()` plus the enums in
  `app/schemas/*`), not from documentation prose. No `any` anywhere in `src/`; ESLint runs
  `@typescript-eslint/no-explicit-any` as an error.
- `src/services/api-client.ts` — the only module that calls `fetch`. Owns the base URL, the
  documented `{ "error": { code, message, detail } }` envelope, request timeouts, abort
  handling, and a typed `ApiError` whose `kind` distinguishes `http`, `network`, `timeout`
  and `malformed`. Components never issue requests themselves.
- Design system — a "financial security command center": a dark, high-signal palette, a
  single cyan instrument accent, and a narrow set of semantic tones reserved for risk
  states. No decorative gradients, glassmorphism, fake market charts or large decorative
  animation. Colour never carries meaning alone; every status also renders text.
- Report page — investigation header, risk assessment, red flags ("why was this flagged?"),
  claims with verification status, the claim → evidence → source panels, entities, the
  eight-stage timeline, limitations and recorded errors, and a standing disclaimer.
- `frontend/.env.example` with `VITE_API_BASE_URL` only. `VITE_API_BASE_URL` is the single
  environment variable the app reads; a development fallback of `http://127.0.0.1:8000` is
  used when it is absent.

### Contract decisions

- **Text is the only implemented input.** `GET /api/investigations/limits` reports
  `supported_input_types: ["TEXT"]`, and the OpenAPI document exposes **no** `/url` or
  `/upload` endpoint. The URL, Screenshot and PDF surfaces exist so the roadmap is visible,
  but they are disabled, removed from the tab order, labelled with the phase that will
  implement them, and **no request is ever sent for them**.
- **Input-mode availability is read from the API**, not hardcoded, so the UI cannot claim a
  mode works when the backend stops accepting it.
- **No risk distribution chart on the dashboard.** `InvestigationSummaryResponse` carries no
  risk level, so there is no real distribution to plot. Both the dashboard and the history
  page state this in the interface instead of approximating it. Recharts is used in exactly
  one place, where real data exists: risk contribution by severity, from
  `risk_assessment.factors[].contribution`.
- **History paging uses the API contract.** `limit` is constrained to 1–100 and `offset` to
  ≥ 0, matching the backend; no client-side pagination was invented.
- **No backend change was made or needed.** Where the API fell short of the plan, the
  frontend adapted. No contract was silently altered.

### Product invariants implemented

- An `UNVERIFIED` or `INSUFFICIENT_EVIDENCE` claim is never labelled fraudulent; it is
  rendered with plain wording and the backend's own `reason` text.
- Absence of evidence is never converted into a contradiction; the empty state says so
  explicitly.
- The risk caveat is fixed copy — "Risk score is a transparent heuristic indicator and is not
  a probability of fraud or financial loss." — shown verbatim and never reworded into a
  stronger claim.
- Limitations and recorded stage failures render above the findings rather than behind a
  disclosure.
- Evidence links open with `rel="noopener noreferrer"` and `referrerPolicy="no-referrer"`,
  and a non-`http(s)` URL is rendered as inert text rather than as a link.

### Accessibility

Semantic landmarks, a skip-to-content link as the first focusable element, a visible focus
ring on every interactive element, `aria-label` on all icon-only buttons, labelled form
controls with `aria-describedby`, `role="status"` live regions for submission and paging
state, `role="meter"` for the risk score, and full `prefers-reduced-motion` support.

### Duplicate-submission guard

`POST /api/investigations/text` runs the full pipeline synchronously and can take around a
minute. The submit button is disabled while in flight, and the hook additionally holds an
in-flight ref so a keyboard repeat or a double click that lands before React re-renders
cannot start a second request.

### Verification

Executed against the **running** backend rather than mocks:

| Check | Result |
| --- | --- |
| `npm run typecheck` | pass, no errors |
| `npm run lint` | pass, 0 errors / 0 warnings |
| `npm run build` | pass, 2233 modules, chunks split app / react / charts |
| `npm run verify:api` | 48 passed, 0 failed |
| `RUN_LIVE=1 npm run verify:flow` | 17 passed, 0 failed |
| `node scripts/verify-render.mjs` | 26 passed, 0 failed |
| All five routes on the dev server | 200, every module compiles |
| CORS preflight from `localhost:5173` | `access-control-allow-origin: http://localhost:5173` |

`verify:api` asserts every endpoint plus the failure paths: `INPUT_EMPTY`, over-length text,
`INVESTIGATION_NOT_FOUND`, an out-of-range `limit`, and a simulated unreachable backend.
`verify:flow` creates a real investigation through the same client function the Investigate
page calls, reads it back, and confirms it appears in the history list with matching counts.
`verify-render.mjs` server-renders the report components against a captured real payload and
asserts both the populated and every empty state.

### Environment issue found and worked around

Node was upgraded from v20.20.2 to v22.23.2 partway through the session, which broke the
PowerShell `npm.ps1` shim (`Cannot find module '@npmcli/config'`). `npm.cmd` was used for the
remainder of the work. Separately, `tailwindcss@4.0.0` with `@tailwindcss/vite@4.0.0` fails
to build on Node 22 with `Cannot convert undefined or null to object`, even for a file
containing only `@import "tailwindcss";`. Both packages were pinned up to `4.3.3`, which
builds cleanly. This is a toolchain fact worth recording: the 4.0.0 native binding does not
work on Node 22.

### Not done, deliberately

- **Phases 12–14 are not implemented.** URL, screenshot/OCR and PDF surfaces are present as
  disabled, phase-labelled UI only. Their backend processing does not exist and the frontend
  does not pretend otherwise.
- **No authentication, portfolio, trading, recommendation or payment features**, per scope.
- **No browser-driven verification.** No automated browser was available in this
  environment, so visual layout at each breakpoint, mouse interaction and HMR were not
  exercised. Rendering correctness, the API contract and route serving were verified as
  described above.
- **No Redux or a server-state library.** Plain React state and hooks; the project has no
  demonstrated need for more.
- **No backend file was modified.** `git diff` for this phase touches `frontend/` and
  `docs/` only.
