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

## Phase 3 — External Services `[ ]`

- [ ] `LLMService` (Groq + Null provider)
- [ ] `SearchService` (SerpAPI + Null provider)
- [ ] `EmbeddingService` (sentence-transformers, optional at runtime)
- [ ] `VectorStore` (NumPy cosine similarity default)
- [ ] `OCRService` (pytesseract, `TESSERACT_CMD` + Windows fallback)
- [ ] `PDFService` (PyMuPDF)
- [ ] Graceful degradation on every service (never crash an investigation)
- [ ] Tests

## Phase 4 — Verification Agent `[ ]`

- [ ] Authoritative source registry / tiering (SEBI, NSE, BSE, MCA, govt)
- [ ] Targeted query construction per claim type
- [ ] Claim ↔ evidence comparison
- [ ] Controlled statuses: `VERIFIED` / `UNVERIFIED` / `CONTRADICTED` / `INSUFFICIENT_EVIDENCE` / `NOT_APPLICABLE`
- [ ] Hard rule: absence ⇒ `UNVERIFIED`, never `CONTRADICTED`
- [ ] Guard test asserting no accusation language is ever produced
- [ ] Tests

## Phase 5 — Evidence Engine `[ ]`

- [ ] Evidence item model with `relationship` ∈ `SUPPORTS` / `CONTRADICTS` / `CONTEXT`
- [ ] Claim → evidence → source wiring
- [ ] Source credibility ranking and tiering
- [ ] No fabricated sources, ever (hard invariant + test)
- [ ] Tests

## Phase 6 — Risk Engine `[ ]`

- [ ] Weighted, transparent indicator accumulation
- [ ] Configurable weights
- [ ] Bands: `LOW` / `MEDIUM` / `HIGH` / `CRITICAL`
- [ ] Per-factor contribution breakdown for explainability
- [ ] No probabilistic language in output
- [ ] Tests

## Phase 7 — LangGraph Orchestration `[ ]`

- [ ] `InvestigationState` typed state
- [ ] Nodes: input_processor → extraction → planner → (red_flag ∥ entity_verify) → claim_verify → evidence → risk → report
- [ ] Timeline step records emitted for the UI
- [ ] `InvestigationService` façade
- [ ] Tests

## Phase 8 — FastAPI Investigation APIs `[ ]`

- [ ] `POST /api/investigations`
- [ ] `POST /api/investigations/text`
- [ ] `POST /api/investigations/url`
- [ ] `POST /api/investigations/upload`
- [ ] `GET /api/investigations`
- [ ] `GET /api/investigations/{id}`
- [ ] API schemas kept separate from DB models
- [ ] Structured error responses
- [ ] Tests

## Phase 9 — Database Persistence `[ ]`

- [ ] SQLAlchemy models: `users`, `investigations`, `claims`, `entities`, `red_flags`, `sources`, `evidence`, `reports`
- [ ] Normalized relationships + indexes
- [ ] Investigation history queries
- [ ] Tests

## Phase 10 — Backend Test Suite `[ ]`

- [ ] Test 1 — suspicious investment message → HIGH indicators
- [ ] Test 2 — legitimate-looking text → not auto-flagged as fraud
- [ ] Test 3 — unverified adviser → `UNVERIFIED`
- [ ] Test 4 — screenshot OCR continues pipeline
- [ ] Test 5 — URL analysis
- [ ] Test 6 — no external evidence → `INSUFFICIENT_EVIDENCE`
- [ ] Test 7 — search unavailable → graceful degradation
- [ ] Integration tests across API + DB + pipeline

## Phase 11 — React Frontend `[ ]`

- [ ] Vite + React + TS + Tailwind + shadcn/ui scaffold
- [ ] `/` landing
- [ ] `/dashboard`
- [ ] `/investigate` (Text | URL | Screenshot | PDF)
- [ ] `/investigation/:id` result page
- [ ] `/history`
- [ ] API client layer
- [ ] Financial-security command-center design system
- [ ] Loading / error / empty states

## Phase 12 — URL Analysis `[ ]`

- [ ] Domain parsing + safe fetch (SSRF guards, scheme/size limits)
- [ ] Page text extraction
- [ ] Registration claims / payment methods / download links extraction
- [ ] Structured website analysis object
- [ ] Never label a domain malicious without evidence

## Phase 13 — Screenshot / OCR `[ ]`

- [ ] Image upload → OCR → text → standard pipeline
- [ ] OCR unavailable → `OCR_UNAVAILABLE`, pipeline continues
- [ ] File size / type validation

## Phase 14 — PDF Analysis `[ ]`

- [ ] PDF upload → PyMuPDF text extraction → standard pipeline
- [ ] Scanned-PDF (no text layer) handling
- [ ] Extraction failure → `PDF_EXTRACTION_FAILED`

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
