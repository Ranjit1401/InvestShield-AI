# AI_PIPELINE — InvestShield AI

The complete investigation pipeline, stage by stage.

---

## 1. Overview

```
Input  (TEXT | URL | IMAGE | PDF)
  ↓
Input Processing
  ↓
Text Extraction
  ↓
Input Normalisation          ← Phase 2, complete
  ↓
Claim Extraction             ← Phase 2, complete
  ↓
Entity Extraction            ← Phase 2, complete
  ↓
Red Flag Detection            ← Phase 1, complete
  ↓
Entity Verification
  ↓
Claim Verification
  ↓
Evidence Collection
  ↓
Evidence Ranking
  ↓
Risk Calculation
  ↓
Explainability
  ↓
Report Generation
  ↓
Investigation Report
```

Each stage is implemented as a service or agent, is unit-testable in isolation,
and records a `TimelineStep` for the UI.

---

## 2. Stage Details

### Stage 0 — Input Processing

**Owner:** `InputProcessor` (`app/services/input_processor.py`)

Normalizes every input type into a common representation:

```python
{
    "input_type": "TEXT" | "URL" | "IMAGE" | "PDF",
    "raw_input": "<original>",
    "extracted_text": "<text to analyse>",
    "source_files": [...],
    "warnings": [...]
}
```

Validation happens here: URL scheme check, file size/type check, empty-input
rejection.

### Stage 1 — Text Extraction

**Owner:** `OCRService` (IMAGE), `PDFService` (PDF), pass-through (TEXT),
URL fetch + strip (URL, Phase 12).

- IMAGE → pytesseract → text. Missing Tesseract binary ⇒ `OCR_UNAVAILABLE`.
- PDF → PyMuPDF page text. Empty text layer (scanned PDF) ⇒ note to try OCR.
- Failure never aborts the investigation; it becomes a stated limitation.

### Stage 1.5 — Input Normalisation

**Owner:** `app/services/text_normalization.py` — `normalize_text()`

Extraction runs on normalised text; **every evidence span is reported against
the original input**. `NormalizedText` carries the reversible index map that
makes that exact rather than approximate.

Normalisation is deliberately minimal: Unicode NFC composition, removal of
control/zero-width characters, collapsing of *horizontal* whitespace runs, and
end trimming. **Newlines are preserved** — collapsing them would merge unrelated
lines into one "sentence", and with it one giant claim.

### Stage 2 — Claim Extraction

**Owner:** `ExtractionService` (LLM) + `ClaimExtractor` (deterministic).
Status: **complete (Phase 2)**.

Splits content into **atomic, individually verifiable claims**. Never returns the
whole message as one claim. The demo input yields six.

```json
{
  "id": "claim_003",
  "text": "Our SEBI-approved expert team guarantees 35% monthly returns",
  "claim_type": "GUARANTEE_CLAIM",
  "confidence": 0.85,
  "evidence_span": { "start": 42, "end": 96, "text": "Our SEBI-approved expert team guarantees 35% monthly returns" },
  "entity_ids": ["entity_001"],
  "is_complete_sentence": true,
  "signals": ["GUARANTEE_CLAIM", "RETURN_PROMISE", "REGULATORY_STATUS"],
  "metadata": { "monetary_amounts": "", "percentages": "35%", "periods": "monthly" }
}
```

**Claim types (14):** `GUARANTEE_CLAIM`, `RETURN_PROMISE`, `PROFIT_PROMISE`,
`PERFORMANCE_CLAIM`, `CREDENTIAL_CLAIM`, `REGULATORY_STATUS`, `COMPANY_CLAIM`,
`PRODUCT_CLAIM`, `OWNERSHIP_CLAIM`, `AFFILIATION_CLAIM`, `WITHDRAWAL_CLAIM`,
`PAYMENT_INSTRUCTION`, `INVESTMENT_OPPORTUNITY`, `OTHER`.

Notes:

- `signals` preserves every type a clause matched without emitting duplicate
  claims. `metadata` carries values found in the claim text itself — monetary
  amounts, percentages, periods. Amounts are **not** entities.
- `confidence` is extraction confidence (how sure the extractor is that the text
  makes this kind of claim). It is **never** a probability that the claim is true.
- A clause that is filler ("Hi there!") or non-assertive is not a claim.
  `"High returns are possible"` produces no guarantee claim.

### Stage 3 — Entity Extraction

**Owner:** `ExtractionService` (LLM) + `EntityExtractor` (deterministic).
Status: **complete (Phase 2)**.

**Entity types (18):** `PERSON`, `COMPANY`, `ORGANIZATION`, `REGULATOR`,
`BROKER`, `INVESTMENT_ADVISER`, `PLATFORM`, `WEBSITE`, `DOMAIN`, `PRODUCT`,
`FINANCIAL_INSTRUMENT`, `LOCATION`, `SOCIAL_HANDLE`, `REGISTRATION_NUMBER`,
`BANK_ACCOUNT`, `UPI_ID`, `PHONE_NUMBER`, `EMAIL`, `OTHER`.

```json
{
  "id": "entity_001",
  "name": "SEBI",
  "entity_type": "REGULATOR",
  "normalized_name": "securities and exchange board of india",
  "confidence": 0.9,
  "evidence_span": { "start": 45, "end": 49, "text": "SEBI" },
  "metadata": {}
}
```

Deterministic extraction handles URLs, domains, emails, UPI ids, IFSC codes,
phone numbers, registration numbers, social handles, known regulators and
platforms, financial instruments, and capitalised name sequences with
organisation/adviser suffixes. It is deliberately **high-precision**: a token it
cannot locate confidently is not emitted. Known regulator acronyms expand to
their full names; nothing else is invented, and `@`, `.` and `-` are preserved
inside identifiers so distinct payment ids never collapse.

**Claim ↔ entity linking:** an entity is linked to a claim when its span falls
inside the claim or its name appears in the claim text. The earliest-mentioned
identity entity of an identity-type claim is marked `SUBJECT`; all others are
`MENTIONS`.

```json
{
  "claim_id": "claim_002",
  "entity_id": "entity_001",
  "relationship": "SUBJECT"
}
```

#### Stage 2+3 operating rules

| Rule | Behaviour |
| --- | --- |
| One structured call per input | Claims and entities are extracted together in a single `structured_generate()` call. No per-sentence loop. |
| Deterministic always runs | The guaranteed-available half; it runs whether or not Groq answers. |
| Hallucination filter | Any model-supplied claim or entity not locatable verbatim in the input is dropped and recorded in `processing_warnings`. |
| Mode is honest | `ExtractionMode.LLM` when the model contributed fully; `PARTIAL` when some model output was discarded or nothing survived; `FALLBACK` when the model was unavailable or failed. |
| No verification | `ExtractionResult` has no verdict field. `VERIFIED`/`CONTRADICTED` belong to Stage 6 (D-006). |

### Stage 4 — Red Flag Detection

**Owner:** `RedFlagEngine` (deterministic) + `ScamIntelligenceAgent`

**This stage is deterministic code, not LLM inference.**

**Implementation:** `app/services/red_flag_rules.py` (rule catalogue) +
`app/services/red_flag_engine.py` (`RedFlagEngine.detect`). Status: **complete
(Phase 1)**.

Codes, severities and default heuristic weights:

| Code | Severity | Weight |
| --- | --- | --- |
| `GUARANTEED_RETURN` | HIGH | 20 |
| `UNREALISTIC_RETURN` | HIGH | 20 |
| `URGENCY_PRESSURE` | MEDIUM | 15 |
| `FAKE_REGULATORY_CLAIM` | HIGH | 25 |
| `UNVERIFIED_ADVISER` | MEDIUM | 20 |
| `SUSPICIOUS_URL` | MEDIUM | 10 |
| `THIRD_PARTY_PAYMENT` | HIGH | 15 |
| `APK_DOWNLOAD` | HIGH | 15 |
| `TELEGRAM_INVESTMENT_GROUP` | MEDIUM | 10 |
| `WHATSAPP_INVESTMENT_GROUP` | MEDIUM | 10 |
| `BORROW_TO_INVEST` | HIGH | 15 |
| `WITHDRAWAL_FEE` | HIGH | 20 |
| `ACCOUNT_ACTIVATION_FEE` | MEDIUM | 15 |
| `FAKE_PROFIT_SCREENSHOT` | MEDIUM | 10 |
| `IMPERSONATION` | HIGH | 25 |

All weights are **configurable** via `risk_weight_*` settings and are heuristic
indicators, not probabilities.

Each detection carries the exact evidence span from the submitted content:

```json
{
  "code": "GUARANTEED_RETURN",
  "name": "Guaranteed Return",
  "severity": "HIGH",
  "weight": 20,
  "description": "The content promises or guarantees an investment return.",
  "matched_text": "guarantees 35% monthly returns",
  "evidence_span": { "start": 42, "end": 73, "text": "guarantees 35% monthly returns" },
  "rule_reason": "The content uses guaranteed-return language.",
  "occurrence_count": 2
}
```

**False-positive discipline (§43):** a single suspicious phrase is never
sufficient for a fraud verdict. "High returns are possible" and "Investment
returns vary depending on market conditions" produce **no** findings, and a
plain-HTTPS regulator URL or a phone number is not a suspicious link. Guard
mechanisms:

| Mechanism | Effect |
| --- | --- |
| Negation cues (before and inside a match) | "Past performance does not guarantee future results" is a disclaimer, not a claim |
| Historical-figure exclusion | "reported 30% annual return last year" is not an unrealistic *promised* return |
| Sentence-level exclusions | "SEBI requires advisers to be registered" is a requirement statement, not a claim |
| `ContextKeywordDetector` | "Contact support on WhatsApp" is not an investment group |
| Threshold comparison | A promised monthly return must exceed `unrealistic_monthly_return_threshold` |

Risk level aggregates **multiple independent indicators**; a lone match never
produces a verdict.

### Stage 5 — Entity Verification

**Owner:** `VerificationAgent` (entity path)

For each identity-type entity, build a targeted query against the authoritative
registry tier and record what was found. Output is a `VerificationResult`
attached to the entity, never to a person named as a fraudster.

### Stage 5.5 — External Search Retrieval

**Owner:** `SearchService` → `SearchProvider` → `SerpAPIProvider`
Status: **infrastructure complete (Phase 3)**. The *interpretation* of results
is Stage 6 and is deliberately not built.

```
Claim / Entity
      ↓  normalize_query()  (deterministic; no LLM)
SearchService.search(query, max_results)
      ↓
SearchProvider.search()               ← abstraction
      ↓
SerpAPIProvider                        ← q, api_key, engine=google, num
      ↓
SearchResult[] + source classification
```

```json
{
  "query": "SEBI approved trading platform",
  "results": [
    {
      "title": "SEBI | Securities and Exchange Board of India",
      "url": "https://www.sebi.gov.in/",
      "snippet": "Securities and Exchange Board of India",
      "source_domain": "sebi.gov.in",
      "source_type": "REGULATOR",
      "position": 1,
      "retrieved_at": "2026-10-01T00:00:00Z"
    }
  ],
  "provider": "serpapi",
  "status": "OK",
  "error_code": null,
  "provider_query_sent": true,
  "warnings": []
}
```

What this stage guarantees, and nothing more:

| Guarantee | Detail |
| --- | --- |
| Three states stay distinct | `OK` (zero results is a success), `UNAVAILABLE` (never asked), `ERROR` (asked and failed) |
| No invented sources | Missing key ⇒ explicit `UNAVAILABLE`, empty results, stated limitation |
| Authority by hostname identity | Exact registry + label-boundary matching; `fake-sebi-example.com` is `GENERAL_WEB` |
| Deterministic query handling | Whitespace/control cleanup only; identifiers and URLs untouched |
| Bounded results | Clamped to `[1, min(SEARCH_MAX_RESULTS, 50)]` |
| De-duplicated | Canonical URL only; similar titles are never merged |
| Secrets protected | Key never logged, returned, or placed in a warning |

**This stage draws no conclusion about any claim.** `source_type` and
`SOURCE_PRIORITY` describe *who published a document*, not whether it supports or
refutes anything. A `REGULATOR` result is not "verified" and a `GENERAL_WEB`
result is not "false" (D-017).

### Stage 6 — Claim Verification

**Owner:** `VerificationAgent`

```
CLAIM
  ↓  targeted query construction (claim_type-aware)
SEARCH AUTHORITATIVE SOURCES
  ↓
COLLECT EVIDENCE
  ↓
COMPARE claim vs evidence
  ↓
STATUS ∈ {VERIFIED, UNVERIFIED, CONTRADICTED, INSUFFICIENT_EVIDENCE, NOT_APPLICABLE}
```

**Hard invariant (D-006):** no search result ⇒ `UNVERIFIED` or
`INSUFFICIENT_EVIDENCE`. `CONTRADICTED` requires an authoritative source that
directly disputes the claim.

A failed Stage 5.5 search maps here as `INSUFFICIENT_EVIDENCE` — *verification
could not be completed* — and never as `CONTRADICTED`. The distinction depends
entirely on `SearchStatus`, which is why the three states are modelled
separately in Phase 3 (D-017).

Reason text is always hedged appropriately:
*"No matching registration could be independently verified from the searched
authoritative records."* — never *"This person is a scammer."*

### Stage 7 — Evidence Collection

**Owner:** `EvidenceAgent`

For each verification result and each red flag, create an `Evidence` record:

```json
{
  "claim_id": "c1",
  "source_id": "s3",
  "evidence_text": "No matching adviser found in searched records",
  "relevance": 0.8,
  "relationship": "CONTEXT"
}
```

Relationships: `SUPPORTS`, `CONTRADICTS`, `CONTEXT`.

User-provided content is itself a valid evidence source
(`credibility = USER_PROVIDED`) — e.g. a guaranteed-return promise inside the
message is evidence *for* the red flag.

**No evidence is ever fabricated.** If search returns nothing, the evidence
source is the user-provided content with the statement: *"No authoritative
evidence was found during this investigation."*

### Stage 8 — Evidence Ranking

**Owner:** `EvidenceAgent`

Order by: source tier → credibility → relevance → recency. Tier 1 outranks
Tier 4. General-web results are supporting context only and never establish
verification.

### Stage 9 — Risk Calculation

**Owner:** `RiskEngine`

```
score = Σ weights of distinct detected indicators
level = LOW | MEDIUM | HIGH | CRITICAL
```

Each contribution is preserved as a `RiskFactor` with a human-readable reason.
The report must state *"HIGH RISK — based on N detected indicators"*, never
*"87% probability of scam"*.

### Stage 10 — Explainability

**Owner:** `ReportAgent` + `RiskEngine`

Produces the mandatory "Why was this flagged?" section in plain language:

```
1. Guaranteed returns
   The message promises a fixed monthly return.

2. Urgency
   The user is asked to invest immediately.

3. Regulatory claim
   The claimed regulatory status could not be independently verified.
```

### Stage 11 — Report Generation

**Owner:** `ReportAgent`

Report sections:

```
Overall Risk Level
Investigation Summary
Detected Claims
Entities
Red Flags
Claim Verification
Evidence
Sources
Why This Was Flagged
Safety Guidance
Investigation Limitations
```

**Limitations are mandatory** and include every degraded service. Safety
guidance is verification advice only — never a buy/sell/hold instruction.

### Stage 12 — Translation (Phase 15)

Applied **only at the presentation layer**. English / Hindi / Marathi. The
investigation itself stays language-independent; there is one pipeline, not one
per language.

---

## 3. Worked Example (synthetic demo input)

Input:

> 🚨 Exclusive AI Trading Opportunity 🚨
> Our SEBI-approved expert team guarantees 35% monthly returns.
> Join our private Telegram group today.
> Minimum investment ₹25,000.
> Pay directly to our account to activate your trading account.

**Claims extracted** (deterministic mode, exactly as the implementation returns
them)

| id | claim | type | confidence | verifiable |
| --- | --- | --- | --- | --- |
| claim_001 | 🚨 Exclusive AI Trading Opportunity 🚨 | `INVESTMENT_OPPORTUNITY` | 0.85 | no |
| claim_002 | Our SEBI-approved expert team | `REGULATORY_STATUS` | 0.70 | yes |
| claim_003 | Our SEBI-approved expert team guarantees 35% monthly returns | `GUARANTEE_CLAIM` | 0.85 | yes |
| claim_004 | Join our private Telegram group today | `INVESTMENT_OPPORTUNITY` | 0.85 | no |
| claim_005 | Minimum investment ₹25,000 | `INVESTMENT_OPPORTUNITY` | 0.85 | no |
| claim_006 | Pay directly to our account to activate your trading account | `PAYMENT_INSTRUCTION` | 0.85 | yes |

claim_002 is the fragment that precedes the guarantee verb; claim_003 is the
whole sentence. Both are reported because the regulatory claim and the guarantee
are verified separately later.

**Entities**

| id | name | type | normalized |
| --- | --- | --- | --- |
| entity_001 | SEBI | `REGULATOR` | securities and exchange board of india |
| entity_002 | Telegram | `PLATFORM` | telegram |

**Links:** claim_002 → SEBI (`SUBJECT`), claim_003 → SEBI (`MENTIONS`),
claim_004 → Telegram (`MENTIONS`).

**Red flags detected:** `GUARANTEED_RETURN`, `UNREALISTIC_RETURN`,
`URGENCY_PRESSURE`, `FAKE_REGULATORY_CLAIM`, `UNVERIFIED_ADVISER`,
`TELEGRAM_INVESTMENT_GROUP`, `THIRD_PARTY_PAYMENT`,
`ACCOUNT_ACTIVATION_FEE`.

**Verification (Phase 4, not built yet):** claim_002 → targeted SEBI registry
search. If nothing matches, status `UNVERIFIED` with the reason *"No matching
registration could be independently verified from the searched authoritative
records."*

**Risk:** multiple independent indicators → HIGH or CRITICAL band, with each
contribution itemised.

---

## 4. Failure Behaviour Matrix

| Failure | Stage | Result |
| --- | --- | --- |
| Groq down / no key | 2, 3, 11 | `LLM_NOT_CONFIGURED` / `LLM_TIMEOUT`; `extraction_mode = FALLBACK`, deterministic extraction used, warning recorded, report still generated |
| Groq returns malformed JSON | 2, 3 | `LLM_INVALID_RESPONSE`; mode `FALLBACK`, deterministic extraction used |
| Groq output fails schema validation | 2, 3 | `LLM_SCHEMA_VIOLATION`; mode `FALLBACK`, deterministic extraction used |
| Groq invents a claim or entity | 2, 3 | Offending item dropped, warning recorded; mode `PARTIAL` if other model output survived |
| Model returns nothing usable | 2, 3 | Mode `PARTIAL` |
| `SERPAPI_KEY` missing | 5.5 | `SearchStatus.UNAVAILABLE`, `SEARCH_NOT_CONFIGURED`, no request sent, limitation stated → `INSUFFICIENT_EVIDENCE` in Stage 6 |
| Search provider timeout | 5.5 | `SEARCH_TIMEOUT`, `SearchStatus.ERROR`, zero results, limitation stated |
| Search provider rate-limited | 5.5 | `SEARCH_RATE_LIMITED`; never reported as "no results" |
| Search provider rejects the key | 5.5 | `SEARCH_AUTH_ERROR`; the key is never echoed back or logged |
| Search provider returns malformed JSON | 5.5 | `SEARCH_INVALID_RESPONSE` |
| One malformed search result | 5.5 | That entry skipped and counted in warnings; the rest of the search is kept |
| Query has no searchable content | 5.5 | `SEARCH_INVALID_QUERY`, no request sent |
| SerpAPI down / no key | 6, 7 | `INSUFFICIENT_EVIDENCE`, limitation stated |
| Tesseract missing | 1 | `OCR_UNAVAILABLE`, image investigation rejected with a clear message |
| PDF text extraction fails | 1 | `PDF_EXTRACTION_FAILED`, empty-text investigation with limitation |
| Invalid URL | 0 | 422 validation error, no investigation created |
| Empty input text | 1.5–3 | Mode `FALLBACK`, empty result, warning *"Input text was empty; nothing to extract."* |
| DB write fails | final | Investigation still returned to the user; persistence warning logged |
