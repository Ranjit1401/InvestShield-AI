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
│   │   │   ├── deps.py         # settings, Database, GraphContext dependencies
│   │   │   ├── adapters.py     # InvestigationState -> API response
│   │   │   ├── errors.py       # typed failures -> 422 / 500
│   │   │   └── routes/         # HTTP endpoints only
│   │   ├── core/             # config, logging, security helpers
│   │   ├── models/           # SQLAlchemy ORM models
│   │   ├── schemas/          # Pydantic contracts (common, red_flags, claims,
│   │   │                     #   entities, extraction, search, verification,
│   │   │                     #   evidence, risk, api)
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

### 2.1 API layer (`app/api/`, Phase 8 complete)

- Thin routers, one per resource (`health`, `investigations`).
- Dependency injection via `app/api/deps.py`: `get_settings_dep`,
  `get_database_dep`, `get_graph_context_dep`, `get_db`.
- Handlers do four things and nothing else: validate, call `run_investigation`,
  map recorded graph errors, serialize. No threshold, score or verdict.
- Errors converted to structured JSON responses by global exception handlers.

```
app/api/
├── deps.py          settings, Database and GraphContext dependencies
├── adapters.py      InvestigationState -> InvestigationResponse
├── errors.py        ApiError taxonomy; 422 vs 500 mapping
└── routes/
    ├── health.py            GET  /api/health
    └── investigations.py    POST /api/investigations
                             POST /api/investigations/text
                             GET  /api/investigations/limits
```

**Everything the app is built from lives on `app.state`.** `create_app` builds one
`Database` and one `GraphContext` from the settings it was given, parks both on
`app.state`, and disposes the database in the `lifespan` handler. The dependencies
read them back. This is why the health endpoint describes the app under test
rather than whatever `DATABASE_URL` happened to be set to when
`app/db/session.py` was first imported — the bug that held the suite red until
Phase 8. Building the graph context per request instead would construct six
services and a connection pool on every call.

**The adapter interprets; it never computes.** `serialize_investigation` derives
exactly two things — the top-level `status` and the deduplicated `limitations`
codes. Every judgment belongs to the phase that made it (D-039). Domain objects
are embedded rather than re-projected, so there is one definition of "a claim"
rather than two that can drift (D-038).

| Concern | Where |
| --- | --- |
| Request/response contracts | `app/schemas/api.py`, `extra="forbid"` throughout |
| State to response | `app/api/adapters.py` |
| Run status | `investigation_status()`, from `TimelineStatus` (D-037) |
| Limitation codes | `dedupe_codes()`, first-seen order |
| Failure taxonomy | `app/api/errors.py`; `ApiError` handler in `app/main.py` |
| Offline testing | `app.dependency_overrides[get_graph_context_dep]` |

**A degraded run is a `200`.** When search, the LLM or extraction is unavailable,
the request succeeds and returns `status: PARTIAL` with the limitation listed.
The status code is chosen by whose fault the failure is: the caller's mistake is
`422`, our contract violation is `500`, and an external provider being down is
neither — it is a `200` (D-009).

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
| `RiskService` | Deterministic, explainable, de-duplicated risk assessment | none (inherits Phase 1/4 codes) |
| `InvestigationService` | Façade orchestrating the pipeline | aggregates |

**Implemented:** `LLMService`, `SearchService`, `SearchProvider`, `RedFlagEngine`,
`normalize_text`, `ClaimExtractor`, `EntityExtractor`, `ExtractionService`,
`VerificationService`, `EvidenceService`, `RiskService`.

**Deferred:** `EmbeddingService`, `VectorStore`, `OCRService` and `PDFService`
belong to image and PDF ingestion (Phase 12); embeddings are off by default and
Phase 5 falls back to lexical similarity.

**Superseded:** `InvestigationService` is the façade originally sketched to
orchestrate the pipeline. Phases 2-6 exist and Phases 4 and 5 do not line up
cleanly behind one interface, so **Phase 7's `app/graph/` owns orchestration
instead** and this service was never built. The same seam problem — Phase 4
discarding the search responses Phase 5 needs — is what `RecordingSearchService`
exists to bridge. Do not build this façade.

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

### 2.3c Risk layer (Phase 6, complete)

```
RedFlag (Phase 1) ────┐
Claim (Phase 2) ──────┤
VerificationResult ───┼─► RiskService.assess()
EvidenceResponse ─────┘        │
                              ▼
                       RiskAssessment
                       (score + level + factors + ratios + warnings)
```

```
app/schemas/risk.py                          RiskLevel, VerificationFactorType,
                                            RiskFactorOrigin, RiskFactor, RiskWeights,
                                            RiskThresholds, RiskAssessment,
                                            RISK_RELEVANT_CLAIM_TYPES,
                                            SCORE_NOT_A_PROBABILITY
app/services/risk/risk_scoring.py            weights_from_settings(),
                                            thresholds_from_settings(), band_for(),
                                            score_from_contributions(),
                                            apply_ceiling(), level_for_score()
app/services/risk/risk_factors.py            factors_from_red_flags(),
                                            factors_from_verification(),
                                            factor_type_for(), severity_for_factor(),
                                            red_flag_id_for(), risk_factor_id_for()
app/services/risk/risk_aggregation.py        collapse_duplicate_red_flags(),
                                            collapse_duplicate_results(),
                                            attach_claims(), attach_evidence(),
                                            absorb_duplicate_signals(),
                                            attach_absorption_context()
app/services/risk/risk_service.py            RiskService, build_risk_service()
```

Contracts:

- **No network, no provider, no LLM, no clock-dependent branch.** Phase 6 is pure
  arithmetic over objects earlier phases produced. `assessed_at` is the only
  wall-clock value and is excluded from every id.
- **One weight table.** Red-flag weights resolve through Phase 1's
  `RULES_BY_CODE` and each rule's `weight_attr`, using the same fallback as
  `RedFlagEngine.weight_for`. `RiskWeights.red_flag_weights` is keyed by the
  `RedFlagCode` enum. Phase 6 adds four settings and reads the existing fifteen
  (D-028).
- **One signal scores once.** A behaviour noticed by four stages is one factor.
  A verification factor describing a signal a scoring red-flag factor already
  counted is kept at `contribution = 0` with an `absorbed_into` pointer, and the
  primary gains the claim's verification status (D-026).
- **`contribution <= weight` is validated.** A factor can never contribute more
  than its declared weight, and never more than once. This makes "do not double
  count" arithmetic rather than aspirational.
- **Absorption needs a scoring primary.** A zero-weight red flag must not silence
  the claim's own finding, and a factor that already weighs nothing is not
  absorbed.
- **Absence of evidence never scores.** `INSUFFICIENT_EVIDENCE` weights `0`;
  `VERIFIED` and `NOT_APPLICABLE` produce no factor. An `UNVERIFIED` result
  scores only for completed-search reason codes, so `SEARCH_UNAVAILABLE` can
  never become an `UNVERIFIED_CLAIM` (D-027).
- **Nothing nets off.** No factor can contribute a negative amount and no member
  of `VerificationFactorType` encodes a risk reduction, so a `VERIFIED` claim
  can never lower a score (D-020).
- **Evidence never contributes.** It attaches `ev_`/`src_` ids to whichever
  factors bear on its claim. The same document reachable from ten queries cannot
  move the score (D-007).
- **The caveat is a schema invariant.** `RiskAssessment` appends
  `SCORE_NOT_A_PROBABILITY` during validation, so no caller can omit it. The
  score is a bounded heuristic indicator sum, never a probability, and the bands
  are product heuristics (D-025).
- **The cap is a ceiling, not a rescaling.** Two documents far past `ceiling`
  score identically, which is what makes one score comparable with another.
- **Derived, never minted.** `rf_` and `rsk_` ids are `sha256` digests over the
  factor's origin, type and cited ids — no counter, no clock — so a re-run is
  comparable to a stored report and `absorbed_into` points at something durable.
- **Wording is templated.** Every user-readable string is a fixed template, never
  generated, and is checked by a negation-aware vocabulary ban (D-029).

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
  (D-007). Phase 6 honours this — it reads the evidence items only to attach
  their `ev_`/`src_` ids to the factors bearing on the same claim, and sums no
  evidence field into the score at all. It reports `evidence_coverage` as a count
  of claims with at least one proof-grade document, which is a transparency
  measure and not a weighted input.

---

## 7. Risk Engine

Implemented in Phase 6. The model below is the one that shipped; the shape
sketched here in the pre-Phase-6 draft is unchanged in intent and now enforced by
schema validation.

```
raw_score = Σ factor.contribution          (each contribution ≤ its weight)
risk_score = min(raw_score, ceiling)       (a ceiling, never a rescale)
risk_level = band(risk_score)              → LOW | MEDIUM | HIGH | CRITICAL
```

Bands, inclusive at the top of each range, from `Settings`:

| Band | Score range | Setting |
| --- | --- | --- |
| `LOW` | 0–20 | `risk_band_medium_max = 20` |
| `MEDIUM` | 21–50 | `risk_band_high_max = 50` |
| `HIGH` | 51–90 | `risk_band_critical_max = 90` |
| `CRITICAL` | 91–100 | `risk_score_ceiling = 100` |

`RiskThresholds` rejects a configuration whose bands are not strictly increasing
or whose top band reaches the ceiling, so a score can never belong to two levels.

Red-flag weights are Phase 1's existing `risk_weight_*` settings, read through the
same rule table:

| Indicator | Weight | Indicator | Weight |
| --- | --- | --- | --- |
| Guaranteed return | +20 | Guaranteed return, unrealistic figure | +20 |
| Urgency / pressure | +15 | Fake regulatory claim | +25 |
| Unverified adviser | +20 | Impersonation | +25 |
| Suspicious payment | +15 | Withdrawal fee | +20 |
| Activation fee | +15 | APK download | +15 |
| Borrow to invest | +15 | Telegram group | +10 |
| WhatsApp group | +10 | Suspicious URL | +10 |
| Fake profit screenshot | +10 | | |

Phase 6 adds four verification weights:

| Verification factor | Weight | Setting |
| --- | --- | --- |
| `CONTRADICTED_CLAIM` | +25 | `risk_weight_contradicted_claim` |
| `UNVERIFIED_CLAIM` | +8 | `risk_weight_unverified_claim` |
| `INSUFFICIENT_EVIDENCE` | +0 | `risk_weight_insufficient_evidence` |

`RiskWeights` and `RiskThresholds` are snapshotted onto every assessment, so a
stored report shows the configuration that produced its own level rather than
today's (D-007, D-028).

Every factor is returned as a `RiskFactor` carrying `id`, `origin`, `factor_type`,
`label`, `description`, `reason`, `source`, `severity`, `weight`, `contribution`,
`absorbed_into`, and the `claim_ids` / `red_flag_ids` / `evidence_ids` /
`source_ids` / `verification_statuses` behind it — so the report can show exactly
why the level was chosen, and a reader can walk from any factor to the document
that produced it.

**De-duplication.** One behaviour noticed by several stages scores once. The
canonical case: "SEBI approved" is fired on by Phase 1, extracted by Phase 2,
unverified by Phase 4 and evidenced by Phase 5. The assessment reports `25` from
the `FAKE_REGULATORY_CLAIM` factor and `0` from the absorbed `UNVERIFIED_CLAIM`
factor — not `33`. The absorbed factor is kept, with `absorbed_into` pointing at
the factor that counted it, so the breakdown still shows that the claim was
unconfirmed (D-026).

**What the score is not.** It is a transparent heuristic indicator of documented
risk factors. It is not a probability of fraud, of financial loss, or of the
investment failing, and it is not a recommendation to invest or not invest. The
weights and bands are declared judgements, not calibrated measurements, and
neither is derived from a dataset. `SCORE_NOT_A_PROBABILITY` is appended to
`RiskAssessment` by validation itself, so no output can present a score without
it (D-025).

**Ratios, not confidences.** `evidence_coverage` is the share of assessed
risk-relevant claims for which a proof-grade document was assembled;
`analysis_completeness` is the share of the three per-claim analyses (extract,
verify, assemble evidence) that were performed. Both are in `[0, 1]`, both are
documented in the schema as transparency measures, and neither is a probability,
a confidence, or an accuracy score. Evidence relevance is never summed into a
number (D-006, D-007, D-027).

---

## 8. Database Architecture

SQLAlchemy 2.x ORM. **Fourteen tables** (Phase 9, complete), normalized around
`investigations`:

```
investigations ──┬── claims ──┬── claim_entity_links ── entities
                 │            ├── evidence ──── sources
                 │            └── evidence_responses
                 ├── red_flags
                 ├── verification_results
                 ├── risk_assessments ── risk_factors
                 ├── timeline_events
                 ├── investigation_warnings
                 └── investigation_errors
```

There is no `users` table — there is no authentication — and no `reports` table,
which is Phase 10's to design. `DATABASE_SCHEMA.md` is authoritative and
tabulates where this differs from the Phase 0 sketch.

**Identity.** `id` (surrogate autoincrement) is every primary key. The Phase 7
`investigation_id` content digest is stored in the indexed, non-unique
`public_id` (and duplicated in `content_hash`), so Phase 8 clients keep the id
they already hold while re-running identical content produces a second row rather
than a constraint violation (D-040).

**Time.** `app/db/types.py` defines `UtcDateTime`, which writes naive UTC and
reads back aware UTC. SQLite has no timezone type, so without it the same column
returns naive values on SQLite and aware ones on PostgreSQL — an asymmetry that
does not fail locally and fails later, on the hosted database only.

**Ordering.** Every ordered collection carries an explicit `sequence` column, and
`evidence_responses.source_refs_json` preserves source order that a join cannot
reconstruct (D-042).

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

Every external dependency is wrapped. Failures are recorded as typed codes and
surfaced as Limitations rather than crashing:

```
Groq unavailable     → LLM_SERVICE_ERROR          → EXTRACTION_FALLBACK limitation, 200
SerpAPI unavailable  → SEARCH_SERVICE_ERROR       → SEARCH_UNAVAILABLE limitation, 200
OCR unavailable      → OCR_UNAVAILABLE            → 503 (Phase 12; 422 in Phase 8)
PDF extraction fails → PDF_EXTRACTION_FAILED      → 503 (Phase 12; 422 in Phase 8)
Empty text           → INPUT_EMPTY                → 422
Unanalysed input type→ INPUT_TYPE_NOT_SUPPORTED   → 422
Stage contract broken→ *_FAILED                   → 500
```

An investigation always produces a result, even if every external service is down
— in that case `status` is `PARTIAL` and the body says exactly which check did
not happen.

**Phase 8 made the HTTP mapping explicit.** The status code is chosen by whose
fault the failure is, not by how serious it feels:

- **The caller's mistake** is `422`. Empty text, an over-length body, an unknown
  field, or an input type this version does not analyse.
- **Our contract violation** is `500`. Phase 2 documents that `extract` never
  raises and Phase 4 that a provider failure never propagates, so an escaping
  exception means the service beneath it is wrong. Anything softer would dress a
  defect up as a normal outcome.
- **An external provider being down** is neither — the request succeeds with `200`
  and `status: PARTIAL`. A SerpAPI outage says nothing about whether the request
  was good, and failing it would make the product look broken at the exact moment
  it is correctly reporting that it checked less than it wanted to (D-009).

Every failing endpoint returns the same envelope —
`{"error": {"code", "message", "detail"}}` — with `detail` a structured object so
a client can branch on it. `message` and `detail` carry the graph's own fixed
wording and, where relevant, an exception's **class name**. Provider messages,
stack traces and connection strings never leave the log; `probe_database` was
returning `str(exc)` until Phase 8, and most drivers put the DSN — credentials
included — in exactly that string.

Unknown error codes default to `500` rather than to a client fault. A code nobody
recognises is more likely a defect below the API layer than a bad request, and
defaulting the other way would let a bug present as the caller's mistake.

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

**Phase 8 note.** The API response is built from objects the pipeline already
produced, so no new surface was opened — but two boundaries got tightened. The
database probe no longer returns an exception message, only its type, because
SQLAlchemy and most drivers embed the DSN in it. And `GraphWarning.error_type`
is dropped from the response body while `GraphError.error_type` is kept: an error
diagnoses a defect for an operator, whereas a warning is something a user may
read and its diagnostic field is not for them.

**Phase 10 note — these boundaries are now tested by planting the secret.**
`tests/api/test_secret_leakage.py` injects a real-shaped credential into a genuine
failure and asserts it does not reach a response: a
`postgresql://user:password@…` DSN in an unreachable database, a provider key in a
search failure, a `GROQ_API_KEY` in an extraction exception, a DSN quoted inside a
provider error, and credential-shaped values in an unknown field, a wrong-type
field, a form body, malformed JSON, a query parameter and a URL path. No response may
contain a traceback, a stack trace, SQL, a DSN, a password, an API key or a
filesystem path (D-047).

The reason for planting rather than reading handlers is that leak paths are not where
they look. A DSN in a SQLAlchemy message does not arrive through the handler that
formats SQLAlchemy errors; it arrives because the message is logged, the log record
attaches to the exception, and a *different* handler renders it. This was not
theoretical: the form-encoded 422 handler really did crash, inside the error path,
with a credential in the payload.

Two further boundaries were found or confirmed in Phase 10:

- **Pydantic validation details carry submitted values by default.** The 422 handler
  now copies only `type`, `loc` and `msg`, so a submitted secret is excluded by
  construction rather than by filtering.
- **`/api/health` names credential environment variables.** An unauthenticated health
  check returns detail such as `"GROQ_API_KEY is not set."` No value is exposed and
  the `configured` boolean a legitimate client reads is unaffected, but on a public
  deployment the wording is reconnaissance. Left as Phase 8 behaviour, recorded as a
  known limitation, and pinned by
  `TestHealthReportsConfigurationWithoutDisclosingIt`.

**Outstanding, not a Phase 10 item.** A credential was committed in an earlier state
of this repository and remains in Git history. Rotating it is an operational action
and history rewriting is out of scope. The current tree is clean: `.env` is
gitignored, every settings factory passes `_env_file=None`, and no `.env` value is
read, printed or asserted on anywhere.

### 2.3d Orchestration layer (Phase 7, complete)

`app/graph/` is the seam between the services and everything that drives them. It
owns **order and bookkeeping only**. No rule, prompt, regex, threshold, weight or
score is computed in it, and `tests/graph/test_graph_package.py` fails the build
if that changes.

```
                     ┌───────────────────────────┐
                     │  LangGraph StateGraph     │
                     │  orchestration only       │
                     └─────────────┬─────────────┘
                                   │
       ┌───────────────────────────┼───────────────────────────┐
       ▼                           ▼                           ▼
 ExtractionService          RedFlagEngine           VerificationService
   (Phase 2)                 (Phase 1)                  (Phase 4)
                                                           │
        ┌──────────────────────────────────────────────────┘
        ▼
 EvidenceService                            RecordingSearchService
   (Phase 5)                                wraps SearchService so Phase 5
                                            can cite the queries Phase 4 ran
        │
        ▼
  RiskService
   (Phase 6)
```

#### Files

| File | Responsibility |
| --- | --- |
| `state.py` | `InvestigationState`, the stage and timeline vocabularies, `GraphWarning`/`GraphError`, `semantic_view` |
| `context.py` | `GraphDependencies`, `GraphContext`, `RecordingSearchService`, `build_default_context` |
| `nodes.py` | The six stage nodes and the fixed warning/error message tables |
| `edges.py` | The single conditional predicate, `route_on_recorded_error` |
| `investigation_graph.py` | `build_investigation_graph()`, `run_investigation()`, `investigation_summary()` |

#### State

`InvestigationState` is a `TypedDict` carrying the Phase 1-6 objects themselves —
`Claim`, `Entity`, `RedFlag`, `VerificationResult`, `EvidenceResponse`,
`RiskAssessment` — never plain dictionaries. Re-deriving dicts would mean
re-validating the same data and discarding the invariants the schemas enforce.

Three channels carry `operator.add` reducers: `warnings`, `errors` and
`timeline`. Appending is therefore a property of the schema rather than a
convention each node must remember. Without the reducers a node returning one
warning would silently discard the ones already recorded, and a happy-path test
would not catch it because that path never accumulates twice.

`investigation_id` is a SHA-256 digest of the input type and submitted text, not
a counter or a database id: Phase 7 has no persistence (that is Phase 9), and a
derived id makes a re-run comparable.

#### Nodes

| Node | Calls | Notes |
| --- | --- | --- |
| `input` | nothing | Validates the submission. Normalization belongs to Phase 2, so nothing is rewritten here. |
| `extraction` | `ExtractionService.extract` | Keeps the whole `ExtractionResult` so Phase 4's `verify_extraction` can consume it. |
| `red_flags` | `RedFlagEngine.detect` | Runs on `raw_input`, never on `extracted_text`: a `RedFlag`'s span indexes the submitted string, so a normalized variant would point at the wrong characters. |
| `verification` | `VerificationService.verify_claims` | Builds no query, classifies no source, decides no status. |
| `evidence` | `EvidenceService.build_all` | Given the responses Phase 4 retrieved, grouped by claim. |
| `risk` | `RiskService.assess_batch` | Stores what Phase 6 computed. No arithmetic here. |

#### The search seam

Phase 4 performs the searches and discards the `SearchResponse` objects; Phase 5
needs them to know which query surfaced which document. `RecordingSearchService`
wraps the real `SearchService`, delegates every call unchanged and keeps what it
saw, so the searches are still Phase 4's, still issued once, and the graph makes
no network call of its own (D-031).

Responses are mapped back to claims through `VerificationResult.queries`, which
Phase 4 populates from the responses it actually received — so a query Phase 4
never issued cannot pull a result into a claim that did not cause it.

#### Failure policy

One predicate guards every stage: **a recorded `GraphError` ends the run.** A
`GraphWarning` never does.

The distinction is between typed degradation and contract violation. Phase 3-5
already turn real-world problems into typed values — `SEARCH_UNAVAILABLE`,
`SEARCH_FAILED`, `INSUFFICIENT_EVIDENCE` — so a missing key is a limitation and
the pipeline continues. An exception escaping a service means a contract is
broken, and continuing would report an assessment built on a stage that silently
did nothing; for the stages feeding the score that understates risk.

The one deliberate exception is the evidence node. Phase 6 established that
evidence contributes no weight and only attaches provenance ids, so losing it
degrades the explanation without changing the assessment. It records a warning,
logs the full exception and names the class, then lets the run finish (D-032).

#### Dependency injection

`GraphDependencies` holds the five services and is frozen, so a run cannot swap a
service halfway. `GraphContext` wraps it with the run's clock and is passed to
each node as LangGraph's runtime context. The state therefore carries
investigation data only, never a live service, which keeps it serialisable and
the nodes testable in isolation.

`build_investigation_graph()` compiles a **fresh** graph per call rather than
exposing a module-level instance, so one test's wiring cannot leak into another's.

#### Determinism

Two runs of the same input with the same fixtures agree field for field.
Wall-clock timestamps are metadata and are excluded by `semantic_view`, which
strips the six names in `TIMESTAMP_FIELDS` — including three stamped by Phases 3,
5 and 6 rather than by the graph. `GraphContext.clock` is injectable, so the
graph's *own* stamps are asserted equal outright (D-034).

#### Timeline

Each stage appends `TimelineEvent(stage, status, message, at)`, where status is
`STARTED`, `COMPLETED`, `PARTIAL`, `FAILED` or `SKIPPED`. `PARTIAL` and
`SKIPPED` are distinct on purpose: collapsing them would make "we could not check
this" indistinguishable from "we checked and found nothing", which is the exact
confusion D-006 exists to prevent. This is metadata for a Phase 11/16 UI; there is
no field for an explanation, so no stage narrative can be smuggled in.

---

### 2.3e API layer (Phase 8, complete)

Wraps `run_investigation` over HTTP. It adds no pipeline behaviour and rewrites
nothing; the graph remains the library entry point that scripts and tests also
call.

#### The request path

```
1. Pydantic validates the body          extra="forbid"; unknown fields are 422
2. run_investigation(text, input_type)   the real graph, the real services
3. raise_for_graph_errors(state.errors)  only when something FAILED
4. serialize_investigation(state)        absent keys become empty lists
```

Step 3 is where the status-code policy lives. It runs only when the state carries
an error; a state with warnings and no errors is a successful, possibly partial,
investigation and goes straight to step 4.

#### What the API layer owns, and what it does not

| Owns | Does not own |
| --- | --- |
| The request and response contracts | Any threshold, weight, score or verdict |
| The top-level `status` | Which limitations "matter" — the graph decided that when it wrote a stage status |
| The `limitations` code array | Filtering the warning set |
| Mapping a recorded failure to an HTTP status | Judging the content |

#### Status mapping

| Recorded graph error | HTTP | Why |
| --- | --- | --- |
| `INPUT_EMPTY` | `422` | The caller sent nothing to investigate |
| `INPUT_TYPE_NOT_SUPPORTED` | `422` | The caller declared a kind this version does not analyse |
| `EXTRACTION_FAILED` | `500` | Phase 2 documents that `extract` never raises; if it did, the contract is broken |
| `RED_FLAG_DETECTION_FAILED` | `500` | A quiet failure here would score content as though nothing were wrong with it |
| `VERIFICATION_FAILED` | `500` | Reporting unchecked claims as checked is the failure this product exists to avoid |
| `RISK_ASSESSMENT_FAILED` | `500` | No score means no assessment, and nothing downstream may imply one |
| *(none recorded)* | `200` | Including a partial run — an external provider being down is not a failed request |

An unknown code defaults to `500`, not to the caller's fault. A code nobody
recognises is more likely to be a defect below this layer than a bad request, and
defaulting the other way would let a bug present as the caller's mistake.

#### `limitations`

The deduplicated `GraphWarning.code` values, first-seen order. Alongside them,
`warnings` carries the same entries with their human-readable message and stage.
Both exist because they serve different consumers: codes are for branching,
messages are for display, and a client must never have to parse English to find
out what a run could not do (D-009).

`NO_RED_FLAGS_DETECTED` appears in `limitations` but does **not** make the status
`PARTIAL`. It is recorded whenever content matched none of Phase 1's rules, which
is most benign content; treating it as a degradation would badge every clean
investigation as a partial one. `status` is read from the timeline, where the
graph already made that distinction (D-037).

#### Synchronous by design

There is no `202`, no job handle and no `GET /api/investigations/{id}` in Phase 8.
The run finishes before the response is written, so a `202` would be a lie about
outstanding work, and an `investigation_id` with nothing stored behind it would
invite a client to poll an endpoint that does not exist. Retrieval is added
alongside the POST in Phase 9, which is purely additive — a client that persists
nothing keeps working (D-036).

#### What reaches the wire

Only exception **class names**. `GraphError.error_type` is forwarded because it
diagnoses a defect; `GraphWarning.error_type` is dropped, because a warning is
something a user may read and its diagnostic field is not for them. Provider
messages, stack traces and connection strings never leave the log.
---

### 2.3f Persistence layer (Phase 9, complete)

#### Files

| File | Role |
| --- | --- |
| `app/models/investigation.py` | The 14 ORM tables |
| `app/models/__init__.py` | Re-exports; importing a model is what registers it on `Base.metadata` |
| `app/db/base.py` | `Base(DeclarativeBase)` |
| `app/db/session.py` | `Database` — engine, session factory, `create_all()`, request-scoped dependency |
| `app/db/types.py` | `UtcDateTime`, `utc_now()`, `as_utc()` |
| `app/repositories/investigations.py` | `InvestigationRepository` — the only code that writes rows |

#### The write path

```
POST /api/investigations/text
  → run_investigation()          graph returns InvestigationState
  → serialize_investigation()    Phase 8 adapter, unchanged
  → repository.save(state)       state → 14 tables, one transaction
  → 200 with the Phase 8 body
```

The adapter runs **before** the save, not after. A storage failure then returns
an error instead of a response claiming the investigation exists, which is the
opposite of the two-step ordering that produces a client holding an id for a run
the database never accepted.

`save()` builds the full object graph and flushes once. SQLAlchemy assigns the
surrogate keys, so the parents are known before the children are inserted and the
foreign keys are populated by the ORM rather than by hand.

#### The read path

```
GET /api/investigations/{public_id}
  → repository.load(public_id)   rows → InvestigationState
  → serialize_investigation()    the same Phase 8 adapter
  → 200
```

Both paths converge on one adapter, so a retrieved investigation cannot drift
from the live one field by field — the alternative gives two mappers that
disagree silently (D-041). The guarantee is measured directly:
`tests/db/test_round_trip.py` asserts
`semantic_view(reloaded) == semantic_view(live)`.

#### Transaction boundary

`save()`, `delete()` and the list/load reads all take an explicit session and
leave `commit()`/`rollback()` to the caller. The route owns the boundary because
the route is the only layer that knows whether the response has already been
decided.

#### Dependency injection

`get_db(request)` resolves the session factory from `request.app.state.database`.
The module-level `SessionLocal` from Phase 0 is no longer on the request path, so
tests that point `DATABASE_URL` at a temporary file get that database rather than
the one the import happened to bind — the defect that made Phase 8's tests unable
to see their own writes.

`lifespan` calls `create_all()` once and swallows the failure, so a database that
is reachable but not writable degrades the POST to an error rather than stopping
the app from serving the endpoints that do not read.

#### Reading history

`GET /api/investigations` reads the `investigations` table alone — id, status,
timestamps, counts. It does not load the state, because the dashboard list needs
a summary and loading fourteen tables per row to render it would make the
endpoint quadratic in content size for no benefit. `InvestigationSummary` is
therefore a repository type rather than a pydantic response model holding a full
investigation.

Because `public_id` is not unique, two runs of the same content both appear in
the history and `load()` returns the most recent.

---

### 2.3g Test architecture (Phase 10, complete)

The test suite surrounds the architecture above; it does not replace or duplicate
any part of it. Every test that claims something about a layer exercises the real
layer, with only the *outside world* faked — a search provider, a clock, a
repository. A test that stubbed the graph to test the API, or the adapter to test
the repository, would assert that the stub behaves as intended.

#### Network isolation

`tests/network_guard.py` wraps `socket.socket.connect`, `connect_ex`,
`socket.create_connection` and `socket.getaddrinfo`. A blocked call raises
`NetworkAccessBlocked` naming the target and the remedy. The autouse fixture that
installs it lives in `tests/conftest.py` — **not** in the guard module, because an
`autouse` fixture is only collected from a conftest or a plugin, and importing a
module registers nothing. That distinction cost the project its first draft of this
guard: it was importable, looked installed, and had never blocked a connection
(D-045).

The guarantee is that **no traffic leaves the machine**. Two exemptions:

- **Loopback literals**, because `TestClient(app)` as a context manager starts an
  anyio blocking portal and Windows emulates `socketpair` with a real loopback TCP
  connection. Only literals qualify; `localhost` still has to resolve, and
  resolution is still blocked. A hostname beginning with `127.` does not qualify.
- **`@pytest.mark.integration`** tests, which are deselected by default.

**The guard cannot see `psycopg2`.** libpq performs its own DNS and TCP in C and
never enters Python's `socket` module, so a database pointed at a routable host
would connect regardless. This is why the suite's poison DSN targets loopback port
1 — refused by the kernel in milliseconds — and why no test points a database
anywhere else.

#### Shared helpers

| File | Role |
| --- | --- |
| `tests/network_guard.py` | The guard itself: four wrapped entry points, `allow_network`, `is_integration` |
| `tests/vocabulary.py` | The Phase 6 judgement-and-advice matcher, extracted so one definition governs a violation across every layer |
| `tests/contract_helpers.py` | Response-walking helpers: every string in a body at a path, timestamp stripping, ignored paths |
| `tests/graph/graph_factories.py` | Recording services and real offline dependencies (Phase 7) |
| `tests/persistence_factories.py` | Investigation content and run helpers (Phase 9) |

#### Coverage by concern

| Concern | Modules |
| --- | --- |
| API input and error contract | `tests/api/test_api_contract_inputs.py`, `test_api_errors.py` |
| OpenAPI surface | `tests/api/test_openapi_contract.py`, `test_api_schemas.py` |
| Graph wiring | `tests/api/test_graph_integration.py`, `tests/graph/` |
| Status semantics | `tests/api/test_status_semantics.py` (D-037) |
| Safety vocabulary | `tests/test_risk_safety.py`, `tests/api/test_api_risk_safety.py` |
| Failure injection | `tests/test_failure_injection.py` |
| Secret leakage | `tests/api/test_secret_leakage.py` |
| Persistence and retrieval | `tests/db/`, `tests/api/test_persistence_endpoints.py`, `test_retrieval_contract.py` |
| Query cost | `tests/db/test_query_efficiency.py`, `tests/api/test_query_efficiency.py` (D-048) |
| Determinism | `tests/test_determinism.py`, `tests/api/test_determinism_http.py` |
| Isolation itself | `tests/test_network_guard.py` |

#### Two conventions worth knowing

**Semantic comparison, not byte comparison.** Where a wall-clock timestamp is
involved, states are compared through `semantic_view` (`app/graph/state.py`), which
strips `TIMESTAMP_FIELDS` at every depth and reduces the timeline to
`(stage, status, message)`. In the API the same field is named `at`, so
`RESPONSE_TIME_FIELDS` adds it — the two sets are combined rather than one replacing
the other, so a new timestamp field has to be added deliberately in both places.

Note what the fixed test clock does and does not cover: it pins the graph's
`started_at`, but Phase 5 and Phase 6 stamp `built_at` and `assessed_at` from their
own clocks. Raw states therefore differ by microseconds, which is exactly what
`semantic_view` exists to exclude.

**Flatness, not a magic number.** N+1 regressions are detected by comparing the
statement count for one stored run against twenty, not by asserting a fixed count
(D-048). `list_page` costs a count query, a page query, and one per child collection
— the last because every relationship is `lazy="selectin"`, which is what makes the
cost independent of history size. A fixed-count assertion would break on the next
legitimate query and invite the threshold ratchet that quietly retires an N+1 test.