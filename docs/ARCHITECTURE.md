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
│   │   │                     #   entities, extraction)
│   │   ├── prompts/          # versioned LLM prompts
│   │   ├── services/         # external + domain services
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
| `RiskEngine` | Weighted risk accumulation | none (pure) |
| `InvestigationService` | Façade orchestrating the pipeline | aggregates |

Bold names are implemented; the rest arrive in Phase 3+.

### 2.3 Extraction pipeline (Phase 2, complete)

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

### 2.4 Red-flag engine (Phase 1, complete)

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

### 2.4 Agents (`app/agents/`)

Five logical components only:

1. **`ClaimEntityAgent`** — extract claims, entities, URLs, registration
   numbers, financial promises.
2. **`ScamIntelligenceAgent`** — wrap `RedFlagEngine` plus heuristic reasoning
   over the extracted content.
3. **`VerificationAgent`** — search authoritative sources; assign verification
   statuses.
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

```python
class Evidence:
    evidence_id
    claim_id
    source_id
    evidence_text
    relevance        # 0..1
    relationship     # SUPPORTS | CONTRADICTS | CONTEXT
```

Evidence is always traceable: `claim → evidence → source(url, title, retrieved_at)`.
Evidence items are only ever created from a real search result or from the
user-provided content. Synthetic sources are forbidden and guarded by a test.

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
