# ARCHITECTURE — InvestShield AI

This document describes the system structure. Update it whenever architecture
changes, and cross-reference `DECISIONS.md`.

---

## 1. Repository Layout (monorepo)

```
investshield-ai/
├── frontend/                 # React + Vite + TS (Phase 11)
│   └── src/
│       ├── components/
│       ├── pages/
│       ├── services/         # typed API client
│       ├── hooks/
│       ├── types/
│       └── utils/
├── backend/                  # FastAPI service (authoritative backend)
│   ├── app/
│   │   ├── api/
│   │   │   ├── deps.py
│   │   │   └── routes/       # HTTP endpoints only
│   │   ├── core/             # config, logging, security helpers
│   │   ├── models/           # SQLAlchemy ORM models
│   │   ├── schemas/          # Pydantic contracts (common, red_flags, claims,
│   │   │                     #   entities, extraction, search, verification)
│   │   ├── prompts/          # versioned LLM prompts
│   │   ├── scripts/          # runnable developer scripts (manual smoke tests)
│   │   ├── services/         # external + domain services
│   │   │   ├── search/       # search provider abstraction + SerpAPI provider
│   │   │   └── verification/ # registry, target, queries, comparator, decisions
│   │   ├── agents/           # 5 logical AI components
│   │   ├── graph/            # LangGraph state + nodes + builder
│   │   ├── tools/            # small deterministic utilities
│   │   ├── db/               # engine, session, base
│   │   └── main.py           # app factory
│   ├── tests/
│   ├── requirements.txt
│   └── .env.example
├── data/                     # local SQLite file lives here
├── docs/                     # persistent project memory
├── .env
├── .env.example
├── .gitignore
├── README.md
└── docker-compose.yml
```

Layering rule (top may call down, never up):

```
API  →  Service layer  →  Investigation engine (LangGraph)  →  Agents/Tools  →  DB/Search/LLM
```

FastAPI route handlers must not contain business logic.

---

## 2. Backend Architecture

### 2.1 API layer (`app/api/`)

- Thin routers, one per resource (`health`, `investigations`).
- Dependency injection via `app/api/deps.py` (DB session, settings, services).
- Errors converted to structured JSON responses by global exception handlers.
- Never returns secrets; never leaks internal stack traces.

### 2.2 Service layer (`app/services/`)

Single-responsibility services, each independently constructible and testable:

| Service | Responsibility | Degradation code |
| --- | --- | --- |
| `LLMService` | `generate()`, `structured_generate()`; provider swap | `LLM_SERVICE_ERROR` |
| `SearchService` | Web search, result normalization, tiering | `SEARCH_SERVICE_ERROR` |
| `EmbeddingService` | Sentence embeddings, cosine similarity | `EMBEDDING_SERVICE_ERROR` |
| `VectorStore` | Store + nearest-neighbour retrieval | in-memory, no failure mode |
| `OCRService` | Image → text (pytesseract) | `OCR_UNAVAILABLE` |
| `PDFService` | PDF → text (PyMuPDF) | `PDF_EXTRACTION_FAILED` |
| `RedFlagEngine` | Deterministic red-flag detection | none (pure) |
| `normalize_text` | Reversible normalisation + offset map | none (pure) |
| `ClaimExtractor` | Deterministic atomic-claim splitting | none (pure) |
| `EntityExtractor` | Deterministic entity/identifier extraction | none (pure) |
| `ExtractionService` | One structured LLM call + deterministic merge | `ExtractionMode.FALLBACK` |
| `SearchService` | Query normalization, provider selection, source classification | `SearchStatus.UNAVAILABLE` / `ERROR` |
| `SearchProvider` | Transport contract for web search | `SEARCH_*` codes |
| `VerificationService` | Verifies claims against authoritative sources | `INSUFFICIENT_EVIDENCE` / `SEARCH_UNAVAILABLE` |
| `EvidenceService` | Assembles traceable, verbatim-sourced evidence bundles | none (inherits Phase 3/4 codes) |
| `RiskEngine` | Weighted risk accumulation | none (pure) |
| `InvestigationService` | Façade orchestrating the pipeline | aggregates |

Bold names are implemented; the rest arrive in later phases.

### 2.3 Search infrastructure (Phase 3, complete)

```
Claim / Entity
      ↓
SearchService            ← policy: normalize, validate, classify, dedupe, limit
      ↓
SearchProvider           ← abstraction (ABC)
      ↓
SerpAPIProvider          ← transport: the only SerpAPI-shaped module
      ↓
SearchResult (normalized)
```

```
app/schemas/search.py              SourceType, SearchStatus, SearchResult,
                                   SearchResponse, SOURCE_PRIORITY, error codes
app/services/search/base.py        SearchProvider (ABC), NullSearchProvider
app/services/search/query.py       normalize_query(), is_meaningful_query()
app/services/search/domain.py      extract_domain(), canonicalize_url()
app/services/search/source_classifier.py   classify_domain(), classify_url()
app/services/search/serpapi_provider.py    SerpAPIProvider, clean_text()
app/services/search/search_service.py      SearchService, build_search_service()
```

Contracts:

- **Consumers depend on `SearchService`, never on SerpAPI.** Adding Bing, Brave,
  DuckDuckGo or a recorded fixture means implementing `search()` and registering a
  name; no consumer changes (D-004 analogue).
- **Transport in the provider, policy in the service.** The provider converts its
  wire format into `SearchResult` and nothing more; the service owns query rules,
  result limits, classification and de-duplication, because those are
  provider-independent.
- **Three states stay distinct.** `OK` (including zero results), `UNAVAILABLE`
  (never asked), `ERROR` (asked, failed). The `SearchResponse` validator refuses
  to represent "failure" as "empty success" (D-017).
- **No interpretation.** There is no verdict, score or evidence-strength field.
  `SOURCE_PRIORITY` ranks *source categories* for later stages; it never decides
  whether a claim is true (D-006, D-017).
- **Authority by hostname identity.** Exact registry and label-boundary suffix
  matching only. `fake-sebi-example.com` is `GENERAL_WEB` (D-018).
- **Deterministic query cleanup.** Whitespace/control/invisible characters only;
  identifiers, names and URLs pass through untouched; no LLM (D-019).
- **Bounded requests.** `max_results` is clamped to
  `[1, min(SEARCH_MAX_RESULTS, HARD_MAX_RESULTS=50)]`.
- **De-duplication by canonical URL only.** Similar titles are never merged;
  merging them would silently discard sources from a later evidence review.
- **Secrets stay in the provider.** The key is sent as a request parameter and
  never logged, returned, or placed in a warning. Logged fields are limited to
  provider name, HTTP status, error type and duration.
- **Deterministic URL handling.** `file:`, `javascript:` and `data:` result URLs
  are rejected at the schema boundary, so a hostile result cannot introduce an
  unusable scheme into a later fetch stage.

### 2.3a Verification layer (Phase 4, complete)

```
Claim + Entity
      ↓
build_target()             → VerificationTarget(claim, parties, identifiers,
                             relevant authorities, queries)     ← pure, no network
      ↓
build_queries()            → ≤ 5 targeted queries               ← pure
      ↓
VerificationService ──► SearchService.search() × N              ← the only network call
      ↓
decide_verification()      → VerificationResult                 ← pure
      │   └─ assess_results() → identity / authority / relevance gates
```

```
app/schemas/verification.py                 VerificationStatus, SourceTier,
                                            IdentityMatch, reason codes,
                                            VerificationResult/Response
app/services/verification/authority_registry.py   8 official bodies, relevance
app/services/verification/target.py              VerificationTarget, build_target()
app/services/verification/query_builder.py       build_queries(), MAX_QUERIES_PER_CLAIM
app/services/verification/comparator.py          assess_results(), match_identity(),
                                                detect_stance(), src_/res_ ids
app/services/verification/decision_engine.py     decide_verification(),
                                                EXPLANATION_TEMPLATES
app/services/verification/verification_service.py VerificationService
```

Contracts:

- **Search goes through Phase 3 only.** `VerificationService` calls
  `SearchService.search()`; no Phase 4 module imports an HTTP client or names a
  provider, and a package test enforces both.
- **Everything except the search loop is pure.** Target, queries, comparison and
  decision are functions of their inputs, so all twelve decision rows are tested
  without a key, a network or a provider.
- **Three gates before any status moves.** *Identity* (token-boundary match on
  the exact entity; a longer name is `AMBIGUOUS`, never a match), *authority*
  (registry member, tier 1–3) and *relevance* (that authority's records can
  speak to this claim family). All three must pass (D-021).
- **Absence is never contradiction.** `CONTRADICTED` has exactly one route: a
  direct conflict cue in an identity-matched, claim-relevant authoritative
  record. Unavailable search, failed search, zero results, identity ambiguity
  and identity absence all resolve to `UNVERIFIED` or `INSUFFICIENT_EVIDENCE`.
- **No verdict vocabulary exists.** Five statuses; no `SCAM`, `FRAUD`, `SAFE`
  or `DANGEROUS` member and no field that would express one (D-020).
- **`confidence` is confidence in the status**, adjusted by the tier of the
  source relied on. It is not a probability of fraud, loss, or truth.
- **Explanation wording is templated.** One fixed string per reason code; no
  generated prose, guarded by a word-boundary scan for verdict terms (D-022).
- **Stable, derived ids.** `src_` / `res_` + `sha256(canonical_url)[:12]`, so
  the same document keeps the same id across claims and runs. Phase 5 attaches an
  evidence object to these rather than recomputing them.
- **Bounded, targeted queries.** Site-restricted queries against registered hosts
  are built first so they always survive `MAX_QUERIES_PER_CLAIM = 5`;
  identifiers, URLs, amounts and percentages are emitted verbatim (D-019).
- **Verification reads titles and snippets only.** No page is fetched. This is a
  coverage limit, not a safety one: it can miss a contradiction, never invent a
  source.

### 2.3b Evidence layer (Phase 5, complete)

```
Claim + Entity
VerificationResult ─┐
Phase 3 results ────┼─► EvidenceService.build_claim()
Phase 3 responses ──┘        │
                             ▼
                    EvidenceResponse
                    (evidence + sources + counts + warnings)
```

```
app/schemas/evidence.py                       EvidenceType, EvidenceRelation,
                                              EvidenceRelevance,
                                              EvidenceSource/Item/Response/Bundle
app/services/evidence/source_normalizer.py    normalize_source(), canonical_url_for(),
                                              evidence_type_for_tier()
app/services/evidence/relationship.py         relationship_for(), relevance_for(),
                                              is_probative(), evidence_type_for()
app/services/evidence/evidence_builder.py     build_evidence_item(), excerpt_for(),
                                              evidence_id_for(), group_by_query()
app/services/evidence/evidence_dedupe.py      dedupe_evidence(), order_evidence(),
                                              distinct_sources()
app/services/evidence/evidence_service.py     EvidenceService, build_evidence_service()
```

Contracts:

- **No network, no provider, no LLM.** Phase 5 issues no search, imports no HTTP
  client and names no provider; a package test enforces all three. Its only
  inputs are objects an earlier phase produced.
- **One judgement, one place.** `relationship.py` is the only module that decides
  what a document *means*, and its only input is Phase 4's `AssessedSource`. No
  Phase 5 module calls `detect_stance()`.
- **Evidence may narrow Phase 4, never widen it.** A supporting cue in a document
  that failed the identity or authority gate — or in a claim whose reason code
  relies on no source — is `CONTEXT`/`MENTIONS`, never `SUPPORTS` (D-024).
- **Excerpts are verbatim.** `snippet`, else `title`; `excerpt_origin` records
  which. Title and snippet are never merged, summarised or paraphrased, and
  `ALLOWED_EXCERPT_ORIGINS` rejects anything else at construction time (D-023).
- **No evidence without a retrieved document.** An empty tuple is the correct
  answer for unavailable search, failed search, zero results and
  `NOT_APPLICABLE`. There is no placeholder item and no synthesised document.
- **Only Phase 4's documents may be cited.** A supplied result whose `src_`/`res_`
  id is absent from the claim's `VerificationResult` is dropped with a warning
  naming how many.
- **Three independent axes.** `evidence_type` (from the authority tier),
  `relationship` (from Phase 4's stance) and `relevance` (closeness, never a
  number). A single taxonomy encoding all three was rejected: two copies of one
  fact eventually disagree (D-023).
- **Reused priority, no second ranking.** Ordering is Phase 4 `TIER_PRIORITY`,
  then relevance, relationship, provider position and the stable `ev_` id. No
  credibility score and no numeric "evidence strength" is defined (D-007, D-012).
- **Derived, never minted.** `ev_` ids are `sha256` over claim, source, result,
  excerpt origin, relationship and excerpt — no counter, no clock, no salt — so
  rebuilding the same evidence yields the same id.
- **Neutral context is kept.** `MENTIONS`, `CONTEXT` and `IDENTITY_REFERENCE`
  items are shown, never suppressed; hiding them would hide the searches that
  found nothing (D-006).
- **A status is copied, not recomputed.** Where the status and the showable
  evidence diverge, the bundle keeps Phase 4's verdict and warns about the gap.

### 2.4 Extraction pipeline (Phase 2, complete)

```
ExtractionService.extract(raw_text) -> ExtractionResult
    │
    ├─ normalize_text(raw)                -> NormalizedText (original + index_map)
    ├─ extract_claims(normalized)         -> [RawClaim]        (deterministic)
    ├─ extract_entities(normalized)       -> [RawEntity]       (deterministic)
    ├─ LLMService.structured_generate()   -> one request, claims + entities together
    │     └─ align every item to normalised offsets, then to original offsets
    ├─ merge: deterministic + LLM, de-duplicated, hallucinated items dropped
    ├─ link: claim ↔ entity (MENTIONS / SUBJECT)
    └─ ExtractionResult(mode, claims, entities, relationships, warnings)
```

Contracts:

- **One structured call per input.** Claims and entities are requested together.
  A per-sentence loop would multiply latency and cost and make partial failure
  likely.
- **Deterministic first, always.** Both pure extractors run unconditionally. They
  are the guaranteed-available half; the LLM is the enhancement.
- **Spans index the original text.** Normalisation is reversible, and every
  final span is re-sliced from `result.source_text` via `make_span()`, which
  raises if the slice and the recorded text disagree. `span_slice_matches()` is
  asserted across the whole result in the tests.
- **No hallucination.** Model output is never trusted as text. Each item must be
  located in the input; anything that cannot be is dropped with a warning naming
  the fact.
- **Honest mode.** `LLM` = model contributed fully; `PARTIAL` = some model output
  discarded or nothing survived; `FALLBACK` = model unavailable or failed.
- **No verification.** `ExtractionResult` deliberately has no verdict field
  (D-006, D-014). Verification is Stage 5/6.

### 2.5 Red-flag engine (Phase 1, complete)

All scam-pattern knowledge lives in **one** module: `app/services/red_flag_rules.py`.
No other file may contain an investment-scam regex.

```
red_flag_rules.py
    RedFlagRule(code, name, description, severity, default_weight,
                weight_attr, detectors, verification_note)
    ALL_RULES / RULES_BY_CODE / get_rule()
    Detector types: RegexDetector, PercentThresholdDetector,
                    MultiplierDetector, CurrencyGrowthDetector,
                    ShortPeriodProfitDetector, ContextKeywordDetector

red_flag_engine.py
    RedFlagEngine.detect(text) -> list[RedFlag]   # dedup + sort
    RedFlagEngine.detect_codes(text) -> set[RedFlagCode]
    RedFlagEngine.weight_for(code) -> int          # settings-driven
```

Contracts:

- **Detection only.** A rule detects *language*. It never decides whether a
  claim is true. `FAKE_REGULATORY_CLAIM` and `UNVERIFIED_ADVISER` carry an
  explicit `verification_note` so the report says "requires verification".
- **Exact spans.** `evidence_span.start/end` index the *original* input;
  `matched_text` is `text[start:end]`, never reconstructed.
- **Deduplication.** One finding per `RedFlagCode`. The primary span is chosen
  by `(detector priority, longest span, earliest offset)`; the rest go to
  `additional_spans`.
- **Sorting.** `(severity desc, weight desc, code asc)` — never dict order.
- **False-positive discipline.** Negation cues (checked before *and* inside a
  match) turn disclaimers into non-matches; sentence-level exclusions suppress
  requirement statements; `ContextKeywordDetector` requires investment context
  in the same sentence; numeric rules ignore historical/reported figures.
- **Configurability.** Weights and thresholds come from `Settings`. Severity is
  fixed per rule (intrinsic seriousness) and is deliberately not tunable.
- **Sentences.** `[.!?।]`, an ellipsis, or a **blank line** ends one. A single
  newline is soft, because promotional posts are line-broken.

### 2.6 Agents (`app/agents/`)

Five logical components only:

1. **`ClaimEntityAgent`** — extract claims, entities, URLs, registration
   numbers, financial promises.
2. **`ScamIntelligenceAgent`** — wrap `RedFlagEngine` plus heuristic reasoning
   over the extracted content.
3. **`VerificationAgent`** — search authoritative sources; assign verification
   statuses. Phase 4 ships the deterministic decision layer behind this; the
   agent wrapper arrives with Phase 7 orchestration.
4. **`EvidenceAgent`** — collect, rank, and link evidence to claims.
5. **`ReportAgent`** — compose the structured investor-safety report and
   optional translation.

Deterministic logic stays in `services/`. Not everything is an agent.

---

## 3. AI / LangGraph Architecture

### State

`app/graph/state.py` defines `InvestigationState`:

```python
class InvestigationState(TypedDict, total=False):
    # input
    input_type: str          # TEXT | URL | IMAGE | PDF
    raw_input: str
    extracted_text: str
    # extraction
    claims: list[Claim]
    entities: list[Entity]
    urls: list[str]
    registration_numbers: list[str]
    # detection
    red_flags: list[RedFlag]
    # verification
    search_queries: list[str]
    search_results: list[SearchResult]
    verification_results: list[VerificationResult]
    evidence: list[Evidence]
    # risk
    risk_factors: list[RiskFactor]
    risk_level: str
    # output
    report: Report | None
    # meta
    limitations: list[str]
    timeline: list[TimelineStep]
    errors: list[ServiceError]
```

### Graph

```
START
  ↓
input_processor            normalize TEXT/URL/IMAGE/PDF → extracted_text
  ↓
claim_entity_extraction    ClaimEntityAgent
  ↓
planner                    decide which claims need verification
  ↓
 ┌────────────────────────┴────────────────────────┐
 ▼                                                 ▼
red_flag_analysis                          entity_verification
 │                                                 │
 └────────────────────────┬────────────────────────┘
                          ↓
                 claim_verification            VerificationAgent
                          ↓
                 evidence_collection           EvidenceAgent
                          ↓
                  risk_calculation             RiskEngine
                          ↓
                  report_generation            ReportAgent
                          ↓
                         END
```

`red_flag_analysis` and `entity_verification` may run in parallel — they are
independent and both are I/O bound.

Each node appends a `TimelineStep` (id, label, status, timestamp, detail) so the
UI can render the §27 investigation timeline without a separate event bus.

---

## 4. Data Flow

```
User input
  → InputProcessor (normalize + extract text)
  → ClaimEntityAgent (claims, entities, urls, reg numbers)
  → RedFlagEngine (deterministic indicators)
  → Planner (select verifiable claims)
  → VerificationAgent (search → evidence → status)
  → EvidenceAgent (rank + link claim→evidence→source)
  → RiskEngine (weighted, explainable)
  → ReportAgent (structured report + guidance + limitations)
  → persist to DB
  → API response → React result page
```

---

## 5. Verification Architecture

```
CLAIM
  ↓  build targeted queries (e.g. site:sebi.gov.in <name> <reg no>)
SEARCH AUTHORITATIVE SOURCES
  ↓
COLLECT EVIDENCE (Source objects with tier + credibility)
  ↓
COMPARE claim text against retrieved evidence
  ↓
ASSIGN STATUS ∈ {VERIFIED, UNVERIFIED, CONTRADICTED, INSUFFICIENT_EVIDENCE, NOT_APPLICABLE}
```

### Source hierarchy

| Tier | Meaning | Examples |
| --- | --- | --- |
| 1 | Official / authoritative | SEBI, NSE, BSE, MCA, .gov.in |
| 2 | Official entity sources | Company's own site, regulatory disclosures |
| 3 | Trusted secondary | Established news, financial media |
| 4 | General web | Supporting context only |
| — | User-provided | The submitted content itself |

Credibility enum: `OFFICIAL`, `TRUSTED`, `GENERAL_WEB`, `USER_PROVIDED`.

**Tier 4 results never establish verification on their own.**

---

## 6. Evidence System

Implemented in Phase 5. The conceptual model is unchanged; the detail is now
fixed.

```
CLAIM ──► EvidenceResponse ──► EvidenceItem ──► EvidenceSource
              │                    │                 │
              │                    │                 └─ url, canonical_url, domain,
              │                    │                    title, source_type,
              │                    │                    source_tier, retrieved_at
              │                    └─ excerpt (verbatim), excerpt_origin,
              │                       relationship, relevance, evidence_type,
              │                       matched_cue, provider_query
              └─ verification_status + reason_code (copied from Phase 4),
                 counts, warnings
```

```
EvidenceType       REGULATORY_RECORD | GOVERNMENT_RECORD | EXCHANGE_RECORD |
                   OFFICIAL_ENTITY_SOURCE | SEARCH_RESULT
EvidenceRelation   SUPPORTS | CONTRADICTS | IDENTITY_REFERENCE | CONTEXT | MENTIONS
EvidenceRelevance  HIGH | MEDIUM | LOW
```

Evidence is always traceable: `claim -> evidence -> result -> source`, printed by
`EvidenceItem.trace_path`. Items are only ever created from a real Phase 3 search
result; synthetic sources are forbidden structurally (D-023) and guarded by tests.

Notes carried forward:

- **`excerpt` is verbatim.** `snippet`, else `title`. Never merged, summarised or
  paraphrased. `excerpt_origin` names the field so a reader can check the quote
  against the page.
- **`relationship` is derived, never re-derived.** It is a function of Phase 4's
  `AssessedSource` and the claim's reason code, and Phase 5 may only narrow it
  (D-024).
- **An empty evidence set is a valid answer.** Unavailable search, failed search,
  zero results and `NOT_APPLICABLE` all produce no items plus a factual warning.
- **Relevance is a label, not a number.** The `0..1` score sketched before Phase 5
  is deliberately not implemented: it would become an undeclared risk input
  (D-007). Phase 6 consumes `proof_count` and `source_count` as *counts* and shows
  the items behind them.

---

## 7. Risk Engine

```
score = Σ (weight of each distinct detected indicator)
level = band(score)  →  LOW | MEDIUM | HIGH | CRITICAL
```

Default heuristic weights (configurable):

| Indicator | Weight |
| --- | --- |
| Guaranteed return | +20 |
| Urgency / pressure | +15 |
| Fake regulatory claim | +25 |
| Unverified entity | +20 |
| Suspicious payment | +15 |
| Suspicious URL | +10 |

Each contribution is returned as a `RiskFactor(code, label, weight, reason)` so
the report can show exactly why the level was chosen. Weights are heuristics and
are labelled as such — never as probabilities.

---

## 8. Database Architecture

SQLAlchemy 2.x ORM. Eight tables, fully normalized:

```
users
investigations ──┬── claims ──── evidence ──── sources
                 ├── entities
                 ├── red_flags
                 └── reports
```

`investigations.user_id` is nullable (no auth). See `DATABASE_SCHEMA.md`.

---

## 9. Frontend Architecture

- Vite + React 18 + TypeScript strict mode.
- Tailwind CSS design tokens + shadcn/ui primitives.
- `react-router-dom` for the five routes.
- Typed API client in `src/services/` generated to mirror `API_SPEC.md`.
- Recharts for risk distribution on the dashboard.
- Design language: dark financial-security "command center" — dense evidence
  panels, explicit risk badges, timeline strip. Deliberately **not** a chat UI.

---

## 10. Error Handling Strategy

Every external dependency is wrapped. Failures are recorded as typed
`ServiceError` objects and surfaced in the report's Limitations section rather
than crashing:

```
Groq unavailable     → LLM_SERVICE_ERROR
SerpAPI unavailable  → SEARCH_SERVICE_ERROR
OCR unavailable      → OCR_UNAVAILABLE
PDF extraction fails → PDF_EXTRACTION_FAILED
Invalid URL          → 422 with a clear message
Malformed input      → 422, investigation never created
```

An investigation always produces a report, even if every external service is
down — in that case the report explicitly states that external verification
could not be completed.

---

## 11. Security Architecture

- Secrets only in `.env`, never in Git, never returned by the API.
- Uploaded files: type allow-list, size cap, stored in memory or a temp dir, and
  **never executed**.
- URL fetching (Phase 12): HTTPS-only, private/loopback IP blocked (SSRF guard),
  redirect limit, response size cap, timeout.
- Extracted text is treated as untrusted data and is never passed to a shell,
  an eval, or a templating engine.
- We never download or execute APKs or arbitrary binaries — `.apk` content is
  only ever *described* as a red flag.
