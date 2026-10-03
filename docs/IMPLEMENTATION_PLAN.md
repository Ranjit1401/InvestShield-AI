# IMPLEMENTATION_PLAN — InvestShield AI

Status legend:

```
[ ] Not started
[~] In progress
[x] Completed
[!] Blocked
```

Phases are executed **in order**. A phase may not be marked complete while its
tests fail, unless the failure is documented as an accepted blocker in
`DEVELOPMENT_LOG.md`.

---

## Phase 0 — Project foundation `[x]`

- [x] Initialize Git repository (`main` branch)
- [x] Create monorepo directory structure
- [x] Create persistent documentation set (`docs/`)
- [x] Root files: `.gitignore`, `.env.example`, `README.md`, `docker-compose.yml`
- [x] Backend package skeleton + `__init__.py` files
- [x] `requirements.txt` (Python 3.14 compatible)
- [x] SQLAlchemy base + session/engine configuration
- [x] Pydantic-settings configuration (`.env` driven)
- [x] Structured logging setup
- [x] FastAPI application factory + CORS + router registration
- [x] `GET /api/health`
- [x] Initial tests (config, health, db engine, app import)
- [x] Verify server boots with uvicorn
- [x] First git commit

## Phase 1 — Red Flag Engine `[x]`

- [x] `app/schemas/red_flags.py` — `Severity`, `RedFlagCode`, `EvidenceSpan` (now
      re-exported from `app/schemas/common.py`), `RedFlag`
- [x] `app/services/red_flag_rules.py` — centralised rule catalogue with all 15 rules
- [x] Detector types: `RegexDetector`, `PercentThresholdDetector`,
      `MultiplierDetector`, `CurrencyGrowthDetector`, `ShortPeriodProfitDetector`,
      `ContextKeywordDetector`
- [x] `app/services/red_flag_engine.py` — `detect()`, dedupe, deterministic sort
- [x] Configurable weights + thresholds in `app/core/config.py` and `.env.example`
- [x] Exact evidence spans sliced from the original input
- [x] Negation handling (disclaimers are not claims)
- [x] False-positive guards (context requirements, historical-figure exclusions)
- [x] 105 new tests: per-rule positives, per-rule negatives, edge cases, spans,
      dedupe, sorting, configurability, determinism

## Phase 2 — Claim / Entity Extraction `[x]`

- [x] `app/schemas/common.py` — shared `EvidenceSpan` + `make_span()` /
      `span_slice_matches()`, promoted out of `red_flags.py` so Phase 1 and
      Phase 2 cite evidence the same way
- [x] `app/schemas/claims.py` — `ClaimType` (14 members),
      `VERIFIABLE_CLAIM_TYPES`, `PROMISE_CLAIM_TYPES`, frozen `Claim`
- [x] `app/schemas/entities.py` — `EntityType` (18 members),
      `IDENTITY_ENTITY_TYPES`, `PAYMENT_ENTITY_TYPES`, `REGULATOR_EXPANSIONS`,
      `normalize_entity_name()`, frozen `Entity`
- [x] `app/schemas/extraction.py` — `ExtractionMode`, `RelationshipType`,
      `ClaimEntityLink`, frozen `ExtractionResult` (validates that every
      relationship id resolves; has **no** verification-verdict field)
- [x] `app/services/text_normalization.py` — `NormalizedText` with a reversible
      original↔normalised offset map; NFC, control/zero-width removal, horizontal
      whitespace collapsing, newlines preserved
- [x] `app/services/llm_service.py` — `LLMService` gateway, `LLMProvider`
      protocol, `GroqProvider` (constrained JSON-schema output),
      `NullProvider`, typed error codes, secrets never logged
- [x] `app/prompts/extraction.py` — `EXTRACTION_PROMPT_VERSION = "extraction-v1"`,
      multilingual + no-hallucination instructions, input truncation notice
- [x] `app/services/claim_extractor.py` — deterministic sentence/clause
      segmentation and typed claim signals; the demo input yields 6 atomic claims
- [x] `app/services/entity_extractor.py` — deterministic URLs/domains, emails,
      UPI, IFSC, phones, registration numbers, social handles, regulators,
      platforms, instruments, companies and persons
- [x] `app/services/extraction_service.py` — **one** structured LLM call per input,
      merged with deterministic extraction, hallucination filter, span alignment
      to the original text, claim ↔ entity linking, explicit mode + warnings
- [x] Claim typing: `GUARANTEE_CLAIM`, `RETURN_PROMISE`, `PROFIT_PROMISE`,
      `PERFORMANCE_CLAIM`, `CREDENTIAL_CLAIM`, `REGULATORY_STATUS`,
      `COMPANY_CLAIM`, `PRODUCT_CLAIM`, `OWNERSHIP_CLAIM`, `AFFILIATION_CLAIM`,
      `WITHDRAWAL_CLAIM`, `PAYMENT_INSTRUCTION`, `INVESTMENT_OPPORTUNITY`, `OTHER`
- [x] Entity typing: `PERSON`, `COMPANY`, `ORGANIZATION`, `REGULATOR`, `BROKER`,
      `INVESTMENT_ADVISER`, `PLATFORM`, `WEBSITE`, `DOMAIN`, `PRODUCT`,
      `FINANCIAL_INSTRUMENT`, `LOCATION`, `SOCIAL_HANDLE`,
      `REGISTRATION_NUMBER`, `BANK_ACCOUNT`, `UPI_ID`, `PHONE_NUMBER`, `EMAIL`,
      `OTHER`
- [x] Monetary amounts and percentages recorded on the claim, not as entities
- [x] 72 new tests: schema invariants, offset-map round-tripping, deterministic
      fallback, hallucination rejection, partial/partial-output mode reporting,
      multilingual (en/hi/mr/Hinglish), exact original-text spans

## Phase 3 — External Services `[~]`

Search infrastructure complete. OCR/PDF/embeddings/vector-store are deferred:
they are not required by the verification design and would add dependencies with
no consumer until Phase 8+.

- [x] `app/schemas/search.py` — `SourceType`, `SearchStatus`, `SearchResult`,
      `SearchResponse`, `SOURCE_PRIORITY`, typed error codes
- [x] Three states kept distinct: `OK` (zero results is a success), `UNAVAILABLE`
      (never asked), `ERROR` (asked and failed)
- [x] `app/services/search/base.py` — `SearchProvider` contract,
      `NullSearchProvider`, `results_or_empty()`
- [x] `app/services/search/serpapi_provider.py` — `SerpAPIProvider`; the only
      module that speaks SerpAPI's wire format
- [x] `app/services/search/query.py` — deterministic query normalization, no LLM
- [x] `app/services/search/domain.py` — `extract_domain()`, `canonicalize_url()`
- [x] `app/services/search/source_classifier.py` — hostname-identity classification
- [x] `app/services/search/search_service.py` — `SearchService`,
      `build_search_service()`
- [x] Canonical-URL deduplication; earliest position preserved
- [x] Result limits with a hard ceiling (`HARD_MAX_RESULTS = 50`)
- [x] No-key behaviour: explicit `UNAVAILABLE`, no request, no fabricated results
- [x] Provider failures mapped to stable codes; never converted to "no results"
- [x] Secrets never logged, returned, or embedded in warnings
- [x] 260 tests, fully offline; opt-in `integration` marker for a live check
- [x] `OCRService` (Phase 13 wiring) — implemented
- [x] `PDFService` (Phase 14 wiring) — implemented
- [ ] `EmbeddingService` / `VectorStore` — deferred to Phase 8+

**Intentionally not built:** claim verification, entity verification, evidence
scoring, risk scoring, LangGraph, public API, database persistence for search
results. Phase 3 answers only *"can we retrieve and normalize external
information?"* (D-017).

## Phase 4 — Verification Agent `[x]`

- [x] `app/schemas/verification.py` — `VerificationStatus`, `SourceTier`,
      `IdentityMatch`, reason codes, frozen `VerificationResult` /
      `VerificationResponse` with self-recomputing counts
- [x] `app/services/verification/authority_registry.py` — SEBI, RBI, IRDAI,
      NFRA, SFIO, MCA, NSE, BSE; hostname-identity lookup and per-claim-type
      relevance
- [x] `app/services/verification/target.py` — `VerificationTarget`,
      claim → entity → authority resolution
- [x] `app/services/verification/query_builder.py` — deterministic,
      template-driven queries, `MAX_QUERIES_PER_CLAIM = 5`
- [x] `app/services/verification/comparator.py` — identity, authority and
      relevance gates; support/contradiction cue detection; stable
      `src_`/`res_` ids
- [x] `app/services/verification/decision_engine.py` — pure decision table and
      fixed explanation templates
- [x] `app/services/verification/verification_service.py` — `VerificationService`,
      `build_verification_service()`
- [x] Controlled statuses: `VERIFIED` / `UNVERIFIED` / `CONTRADICTED` /
      `INSUFFICIENT_EVIDENCE` / `NOT_APPLICABLE`
- [x] Hard rule: absence ⇒ `UNVERIFIED` or `INSUFFICIENT_EVIDENCE`, never
      `CONTRADICTED` (D-021)
- [x] Guard test asserting no accusation language is ever produced (D-022)
- [x] `app/scripts/manual_verification.py` — runnable offline + live smoke test
- [x] 313 tests, fully offline; every `SearchProvider` path faked

**Intentionally not built:** the Phase 5 `Evidence` model (Phase 4 preserves
`matched_result_ids` as plain strings instead), evidence strength or ranking,
risk scoring, entity *legitimacy* judgements (DNS/WHOIS), and any HTTP surface.
Verification decides claim status; nothing here renders a report or a score
(D-017, D-020). Phase 5 later turns those preserved ids into formal evidence
objects.

## Phase 5 — Evidence Engine `[x]`

- [x] `app/schemas/evidence.py` — `EvidenceType` (5 members), `EvidenceRelation`
      (5 members), `EvidenceRelevance` (3 members), frozen `EvidenceSource` /
      `EvidenceItem` / `EvidenceResponse` / `EvidenceBundleResponse` with
      self-recomputing counts and provenance enforced at construction
- [x] `app/services/evidence/source_normalizer.py` — Phase 3 `SearchResult` →
      `EvidenceSource`, reusing `canonicalize_url()`, `extract_domain()` and
      Phase 4 `source_id_for()` / `result_id_for()` / `resolve_authority()`
- [x] `app/services/evidence/relationship.py` — the layer's **only** judgement:
      relationship, relevance and record type derived from Phase 4's
      `AssessedSource`; never a re-read of the snippet
- [x] `app/services/evidence/evidence_builder.py` — verbatim excerpts with
      `excerpt_origin`, stable `ev_` ids, `group_by_query()` query provenance
- [x] `app/services/evidence/evidence_dedupe.py` — first-seen-wins de-duplication
      and deterministic ordering on Phase 4 `TIER_PRIORITY`, relevance,
      relationship, provider position and stable id
- [x] `app/services/evidence/evidence_service.py` — `EvidenceService`,
      `build_evidence_service()`, per-claim and batch assembly, no-evidence and
      coverage warnings
- [x] Claim → evidence → source wiring
- [x] Source credibility ranking and tiering, **reused** from Phase 3/4 rather
      than reimplemented (D-012, D-018, D-023)
- [x] No fabricated sources, ever (hard invariant + tests): excerpts are verbatim
      `snippet` or `title`, `NOT_APPLICABLE` yields nothing, and a supplied
      result Phase 4 never saw is dropped with a warning
- [x] Context items deliberately retained, never suppressed
- [x] `app/scripts/manual_evidence.py` — runnable offline + live smoke test
- [x] 368 tests, fully offline; every `SearchProvider` path faked

**Intentionally not built:** any numeric "evidence strength" score, risk scoring
or banding, entity *legitimacy* judgements, page fetching or HTML parsing,
persistence, and any HTTP surface. Evidence explains a verification result; it
does not judge the investment and cannot contradict Phase 4 (D-020, D-021,
D-023).

## Phase 6 — Risk Engine `[x]`

- [x] Weighted, transparent indicator accumulation
- [x] Configurable weights
- [x] Bands: `LOW` / `MEDIUM` / `HIGH` / `CRITICAL`
- [x] Per-factor contribution breakdown for explainability
- [x] No probabilistic language in output
- [x] Tests

Delivered as `RiskService` over `RiskFactor` / `RiskWeights` / `RiskThresholds` /
`RiskAssessment`. Four settings added (`risk_weight_contradicted_claim`,
`risk_weight_unverified_claim`, `risk_weight_insufficient_evidence`,
`risk_score_ceiling`); the fifteen red-flag weights are Phase 1's own, read
through `RULES_BY_CODE` so there is one weight table. Bands are
`0–20 / 21–50 / 51–90 / 91–100`, inclusive and validated as strictly increasing.

Beyond the checklist, three things the plan did not anticipate:

- **Cross-stage de-duplication.** One behaviour noticed by four stages scores
  once. The extra view is kept at `contribution = 0` with an `absorbed_into`
  pointer, so the breakdown stays complete without inflating the severity
  (D-026).
- **The completed-search gate.** `INSUFFICIENT_EVIDENCE` weighs `0` and an
  `UNVERIFIED` result produces a factor only when its reason code says a search
  actually completed — so a tool that cannot reach the internet cannot report its
  own blindness as somebody's risk (D-027).
- **A negation-aware vocabulary ban.** Enforced by test over every field and
  warning, across the whole output space, with denials permitted so the
  disclaimers survive (D-029).

## Phase 7 — LangGraph Orchestration `[x]`

- [x] `InvestigationState` typed state
- [x] Nodes: input → extraction → red_flags → verification → evidence → risk
- [x] Timeline step records emitted for the UI
- [~] `InvestigationService` façade — **not built**; `app/graph/` owns
  orchestration instead. See `ARCHITECTURE.md` §2.2.
- [x] Tests (291)

## Phase 8 — FastAPI Investigation APIs `[x]`

- [x] `POST /api/investigations` — typed entry point, `TEXT` only
- [x] `POST /api/investigations/text`
- [x] `GET /api/investigations/limits`
- [x] `GET /api/health` — database probe now follows the app's own settings
- [~] `POST /api/investigations/url` — **deferred to Phase 12**; `URL` is
  recognised and refused with `422`
- [~] `POST /api/investigations/upload` — **deferred to Phase 12**; `IMAGE` and
  `PDF` are recognised and refused with `422`
- [~] `GET /api/investigations` — **deferred to Phase 9**; needs persistence
- [~] `GET /api/investigations/{id}` — **deferred to Phase 9**; needs persistence
- [x] API schemas kept separate from DB models (`app/schemas/api.py`)
- [x] Structured error responses — one envelope, 422 for caller faults, 500 for
  contract violations, 200 + `PARTIAL` for degradation
- [x] Tests (114)

## Phase 9 — Database Persistence `[ ]`

- [ ] SQLAlchemy models: `users`, `investigations`, `claims`, `entities`, `red_flags`, `sources`, `evidence`, `reports`
- [ ] Normalized relationships + indexes
- [ ] Investigation history queries
- [ ] Tests

## Phase 10 — Backend Test Suite `[x]` — Testing & Quality Hardening

> **Naming.** This phase was carried out as **Testing & Quality Hardening**, which
> is what the list below describes. `CURRENT_STATE.md` had labelled the same number
> "Report generation"; that label came from `AI_PIPELINE.md`'s **Stage 11**, a
> pipeline stage on a different numbering axis, and it has been corrected. Report
> generation is not a numbered project phase and is not started.
>
> The seven scenarios below were written as a wish list when the pipeline did not
> exist. Phases 1–9 built the pipeline, so most of them are now covered — but not
> all seven, and the two that are not are the honest gap:
>
> - **Test 4 (screenshot OCR) and Test 5 (URL analysis) cannot pass.** OCR and URL
>   ingestion are Phases 13 and 12, and `URL`/`IMAGE`/`PDF` are recognised and
>   refused with `422`. Leaving the boxes ticked would claim coverage of code that
>   does not exist, so they stay open and are re-labelled to say so.

- [x] Test 1 — suspicious investment message → HIGH indicators
- [x] Test 2 — legitimate-looking text → not auto-flagged as fraud
- [x] Test 3 — unverified adviser → `UNVERIFIED`
- [ ] Test 4 — screenshot OCR continues pipeline — **blocked: OCR is Phase 13**
- [x] Test 5 — URL analysis — delivered in Phase 12
  (`tests/graph/test_graph_url_execution.py`)
- [x] Test 6 — no external evidence → `INSUFFICIENT_EVIDENCE`
- [x] Test 7 — search unavailable → graceful degradation
- [x] Integration tests across API + DB + pipeline

**What Phase 10 actually delivered**, beyond the list above: a network guard that
makes the default suite genuinely offline; API input, error-envelope and OpenAPI
contract coverage; a per-stage failure-injection matrix; secret-leakage tests that
plant a DSN, a provider key and a credential-shaped submission and prove none reach
a response; retrieval-contract, query-cost and determinism coverage; and two
production-code defect fixes (the form-encoded 422 handler, and a missing
`StarletteHTTPException` handler). See `CURRENT_STATE.md` and the Phase 10 entry in
`DEVELOPMENT_LOG.md`.

## Phase 11 — React Frontend `[x]`

**Goal:** an investor-safety investigation frontend over the existing API. No backend
change and no API contract change.

- [x] Vite + React + TS + Tailwind v4 + shadcn/ui scaffold
- [x] `/` landing
- [x] `/dashboard`
- [x] `/investigate` (Text | URL | Screenshot | PDF)
- [x] `/investigation/:id` result page
- [x] `/history`
- [x] API client layer
- [x] Financial-security command-center design system
- [x] Loading / error / empty states

**Status:** complete. Implemented in `frontend/`. `npm run typecheck`, `npm run lint` and
`npm run build` pass, and the client was verified against the running backend (48 API
assertions, 17 live end-to-end text-flow assertions, 26 render assertions).

> **The Screenshot and PDF input surfaces were present as UI but not
> operational at this phase.** Their backend processing was deferred to
> Phases 13–14, both of which are now complete: `POST /api/investigations/image`
> and `POST /api/investigations/pdf` are implemented, and all four input
> modes are operational. (Historical note: at Phase 11 the OpenAPI document
> exposed no `/image` or `/pdf` endpoint and `GET /api/investigations/limits`
> reported `supported_input_types: ["TEXT"]`, so those modes were rendered
> disabled and phase-labelled.)

## Phase 12 — URL Analysis `[x]`

**Goal:** accept a submitted URL, fetch the page safely, and run the
existing pipeline over its visible text.

- [x] Domain parsing + safe fetch (SSRF guards, scheme/size limits)
- [x] Page text extraction
- [x] Registration claims / payment methods / download links extraction
- [x] Structured website analysis object
- [x] Never label a domain malicious without evidence

**Status:** complete. Implemented in
`backend/app/services/url_guards.py`, `url_fetch.py`,
`website_extractor.py`, `backend/app/schemas/url.py`, the URL input
path in `backend/app/graph/nodes.py`, `POST /api/investigations/url`,
a `source_metadata` persistence column, and the frontend's URL input
mode. Full suite: **2796 passed, 4 deselected, 0 failed**;
`npm run verify:api` 57 passed.

**What was built:** an allowlist-only SSRF policy (private, loopback,
link-local and metadata addresses refused — including obfuscated IPv4
literals and names that resolve inward), a stdlib `http.client`
fetcher with pinned connections, per-hop redirect re-validation and
byte/redirect/timeout budgets, a stdlib visible-text extractor, and a
fault taxonomy that answers `422` for caller faults, `502` for site
faults and `503` for a disabled fetch capability, with no socket, TLS
or host detail in any message.

**Deliberately deferred:** JavaScript-rendered pages (a headless
browser is the eventual remedy; `PAGE_TEXT_NOT_RETRIEVED` is recorded
instead of faking text), and any verdict on a domain itself — the
pipeline analyses *claims in the page*, never "is this domain a scam"
(D-006, D-020).

> **The Screenshot and PDF input surfaces remained UI-only at this
> phase.** Their backend processing was Phases 13–14, both now complete;
> the frontend now offers working Screenshot and PDF modes, and
> `GET /api/investigations/limits` reports
> `supported_input_types: ["TEXT", "URL", "IMAGE", "PDF"]`.

## Phase 13 — Screenshot / OCR `[x]`

- [x] Image upload → OCR → text → standard pipeline
- [x] OCR unavailable → `OCR_UNAVAILABLE`, pipeline continues
- [x] File size / type validation

**Status:** complete. `POST /api/investigations/image` accepts a
`multipart/form-data` upload, validates it (size, declared type, and what
the bytes actually decode to), reads it with Tesseract OCR, and runs the
existing pipeline over the recovered text. The graph gained an IMAGE input
path, persistence gained an `image_metadata` column, and the frontend gained
a working Screenshot mode. (This section was left unchecked by the Phase 13
commit and is corrected here.)

## Phase 14 — PDF Analysis `[x]`

- [x] PDF upload → PyMuPDF text extraction → standard pipeline
- [x] Scanned-PDF (no text layer) handling
- [x] Extraction failure → `PDF_EXTRACTION_FAILED`
- [x] File size / type validation
- [x] Page limit (`pdf_max_pages`) and character budget (`pdf_extraction_max_chars`)
- [x] `pdf_source` provenance on every response

**Status:** complete. Implemented in
`backend/app/services/pdf_guards.py`, `pdf_service.py`,
`backend/app/schemas/pdf.py`, the PDF input path in
`backend/app/graph/nodes.py`, `POST /api/investigations/pdf`,
a `pdf_metadata` persistence column, and the frontend's PDF input
mode. Full suite: **2966 passed, 4 deselected, 0 failed**;
frontend `typecheck` / `lint` / `build` all pass.

**What was built:** an allowlist-only upload policy (byte budget,
declared media type — `application/pdf` only — with the parsed
document's `is_pdf` as the authority), a lazy `pymupdf` extraction
that reads the first `pdf_max_pages` (100) pages under a
`pdf_extraction_max_chars` (20,000) character budget, and a fault
taxonomy that answers `422` for caller faults (oversized, wrong type,
unparseable, empty), `503` for a disabled PDF capability
(`PDF_INPUT_UNAVAILABLE`), and `200 PARTIAL` with a recorded
limitation for every degradation the run survives — `PDF_UNAVAILABLE`
(no library), `PDF_TEXT_NOT_RETRIEVED` (a parseable PDF with no
readable text, e.g. a scan), `PDF_EXTRACTION_FAILED` (the engine ran
and failed), `PDF_CONTENT_TRUNCATED` (over-budget text) and
`PDF_PAGE_LIMIT_REACHED` (later pages skipped). No text is ever
fabricated, and no message carries a path or engine detail.

## Phase 15 — Multilingual Reports `[ ]`

- [ ] en / hi / mr translation layer
- [ ] Translation applied at report/presentation layer only
- [ ] Investigation stays language-independent
- [ ] Tests

## Phase 16 — Full Integration `[ ]`

- [ ] End-to-end run of all input types
- [ ] Error-handling sweep (LLM, search, OCR, PDF, DB failures)
- [ ] Performance check on realistic inputs

## Phase 17 — Demo / Hackathon Polish `[ ]`

- [ ] Investigation timeline polish
- [ ] Evidence cards + claim/evidence graph visualization
- [ ] "Why was this flagged?" experience
- [ ] Demo flow per README
- [ ] Documentation final pass

---

## Phase 7 — LangGraph Orchestration — COMPLETE

**Goal:** connect Phases 1-6 into one deterministic, testable investigation
graph, without moving any business logic into it.

**Status:** complete. 291 tests added. No Phase 1-6 file was modified.

**What was built**

```
backend/app/graph/
├── __init__.py             public surface
├── state.py                InvestigationState, stage/timeline vocabulary
├── context.py              GraphDependencies, GraphContext, RecordingSearchService
├── nodes.py                the six stage nodes + fixed message tables
├── edges.py                route_on_recorded_error
└── investigation_graph.py  build_investigation_graph, run_investigation
backend/tests/graph/
├── graph_factories.py      recording fakes + real-service offline builders
├── test_graph_construction.py
├── test_graph_state.py
├── test_graph_recorder.py
├── test_graph_nodes.py
├── test_graph_execution.py
├── test_graph_safety.py
└── test_graph_package.py
backend/app/scripts/manual_graph.py
```

**Decisions recorded:** D-030 … D-035.

**The two findings worth carrying forward**

1. *A failed stage originally continued anyway.* Guarding only the input node
   meant an extraction failure still produced a full `risk_assessment`, and a
   failed red-flag pass reported "no patterns found" — understating risk while
   looking complete. One predicate now guards every stage (D-032).
2. *Phase 4 discards the search responses Phase 5 needs.* Solved by recording
   them at the provider boundary rather than re-running searches, which would
   have duplicated every call and risked presenting evidence that the
   verification never saw (D-031).

**Not done, deliberately:** no FastAPI route (Phase 8), no persistence (Phase 9),
no report (Phase 10), no OCR/PDF/image ingestion, no parallel execution. Only
`TEXT` input is analysed; the other three declared types are refused with a typed
reason (D-035).

---

## Phase 8 — FastAPI Investigation APIs — COMPLETE

**Goal:** expose the graph over HTTP, and fix the health endpoint's database
boundary so the suite could go fully green.

**Status:** complete. 114 tests added, one pre-existing failure fixed. Suite:
`1997 passed, 4 deselected`.

**What was built**

```
backend/app/schemas/api.py              request/response contracts, extra="forbid"
backend/app/api/adapters.py             InvestigationState -> InvestigationResponse
backend/app/api/errors.py               typed failures -> 422 / 500
backend/app/api/routes/investigations.py  POST /investigations, /investigations/text,
                                          GET /investigations/limits
backend/tests/api/                      114 tests across five modules
```

`app/api/routes/health.py`, `app/api/deps.py`, `app/main.py` and
`app/services/capabilities.py` were also touched — see below.

**Decisions recorded:** D-036 … D-039.

**The three findings worth carrying forward**

1. *The health endpoint reported on the machine, not the app.* It probed the
   module-level engine in `app/db/session.py`, built from `get_settings()` at
   import time. A test passing its own `Settings` still probed the developer's
   real database, and an unreachable ambient `DATABASE_URL` made a healthy app
   report `degraded`. `create_app` now builds one `Database` and one
   `GraphContext` from its own settings and parks both on `app.state`; the
   dependencies read them back and `lifespan` disposes the engine.
2. *`probe_database` leaked connection details.* It returned `f"{type(exc).__name__}:
   {exc}"`, and SQLAlchemy plus most drivers put the DSN — credentials included —
   in exactly that string. It now returns the exception type only.
3. *"Any warning means a degraded run" would have been wrong.*
   `NO_RED_FLAGS_DETECTED` is recorded on any clean content, so a status derived
   from the warning list would badge most benign investigations as partial. The
   status is read from the stage timeline, where the graph had already made the
   distinction (D-037).

**Not done, deliberately:** no persistence and no `GET /api/investigations/{id}`
(Phase 9); no URL, image or PDF endpoints (Phase 12) — those kinds are refused
with `422` naming what does work; no `summary`, `why_flagged` or
`safety_guidance` (Phase 10), because each would be a second, unvalidated
restatement of a judgment the pipeline already made (D-039); no translation.
