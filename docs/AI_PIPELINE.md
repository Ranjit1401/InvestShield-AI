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
Claim Verification            ← Phase 4, complete
  ↓
Evidence Assembly              ← Phase 5, complete
  ↓
Evidence Ordering              ← Phase 5, complete
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

**No LLM after Stage 2.** Extraction is the only stage that calls a model. Every
stage below it — verification, evidence, ordering, risk — is deterministic code, so
a status, a quote and a score are reproducible from the same inputs (D-008).

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

### Stage 7 — Evidence Assembly (Phase 5, complete)

**Owner:** `app/services/evidence/` — `EvidenceService`, not an LLM agent.

This stage is deterministic. There is no model call anywhere in it, and there
will not be one: an evidence quote is either retrieved text or it is a
fabrication (D-023).

For each claim, `EvidenceService.build_claim()` turns Phase 3 results and the
Phase 4 `VerificationResult` into an `EvidenceResponse`:

```json
{
  "claim_id": "claim_001",
  "verification_status": "VERIFIED",
  "verification_reason_code": "AUTHORITATIVE_SOURCE_CONFIRMS",
  "evidence": [
    {
      "id": "ev_2c1f0a9b3d47",
      "claim_id": "claim_001",
      "evidence_type": "REGULATORY_RECORD",
      "relationship": "SUPPORTS",
      "relevance": "HIGH",
      "excerpt": "Acme Capital Advisors is registered as an investment adviser.",
      "excerpt_origin": "snippet",
      "matched_cue": "registered as",
      "provider_query": "site:sebi.gov.in \"Acme Capital Advisors\"",
      "source": {
        "source_id": "src_2d84c639a686",
        "result_id": "res_2d84c639a686",
        "url": "https://www.sebi.gov.in/intermediaries/acme",
        "domain": "sebi.gov.in",
        "source_type": "REGULATOR",
        "source_tier": "TIER_1_PRIMARY_REGULATOR",
        "is_authoritative": true
      }
    }
  ],
  "sources": ["…"],
  "warnings": [],
  "evidence_count": 1,
  "supports_count": 1,
  "proof_count": 1
}
```

Relationships: `SUPPORTS`, `CONTRADICTS`, `IDENTITY_REFERENCE`, `CONTEXT`,
`MENTIONS`. Relevance: `HIGH`, `MEDIUM`, `LOW` — a label, never a number.

Rules that hold in code, not just in this document:

- **The excerpt is verbatim.** `SearchResult.snippet`, or `SearchResult.title`
  when there is no snippet, and `excerpt_origin` says which. Title and snippet are
  never stitched together, summarised or paraphrased. An excerpt longer than 400
  characters is truncated on a word boundary — always a prefix of real text.
- **`relationship` comes from Phase 4**, not from re-reading the snippet. Phase 5
  may only *narrow* Phase 4: a supporting cue in a document that failed the
  identity or authority gate is `CONTEXT`/`MENTIONS`, never `SUPPORTS` (D-024).
- **No evidence without a retrieved document.** Unavailable search, failed
  search, zero results and `NOT_APPLICABLE` all produce an empty `evidence` tuple
  plus a factual warning. There is no placeholder item and no synthesised "no
  authoritative evidence was found" document — that sentence is a *warning*, and
  it is the only thing produced when nothing was retrieved.
- **Only documents Phase 4 recorded may be cited.** A supplied result whose
  `src_`/`res_` id is absent from the claim's `VerificationResult` is dropped, and
  the count is reported.
- **Neutral items are kept.** `MENTIONS`, `CONTEXT` and `IDENTITY_REFERENCE`
  documents are shown, never suppressed; hiding them would hide the searches that
  found nothing (D-006).
- **The status is copied, not recomputed.** Where the status and the showable
  evidence diverge, Phase 4's verdict stands and a warning names the gap.

**User-provided content is a separate concern.** Red-flag evidence spans come
from Phase 1's `RedFlag.evidence_span`, which is sliced from the submitted input
and is exact by construction (Phase 2's reversible offset map). The Phase 5
evidence layer never cites the user's own message as a retrieved document, because
it has no `SearchResult` to quote.

### Stage 8 — Evidence Ordering (Phase 5, complete)

**Owner:** `app/services/evidence/evidence_dedupe.py`

De-duplication key: claim + canonical URL + result id + excerpt origin + normalized
excerpt + relationship. First seen wins, so the survivor is the earliest provider
position. Two pages on one regulator's site saying different things are two
findings and are both kept — merging them would erase the distinction a reader is
being asked to check.

Ordering, in sequence:

1. Source authority tier (Phase 4 `TIER_PRIORITY`) — most authoritative first;
2. Relevance — most directly bearing on the claim first;
3. Relationship — support, then conflict, then identity, then context, then
   mention;
4. Provider position, then the stable `ev_` id, so ties never depend on dict or
   set iteration order.

No new credibility score is introduced. General-web results are supporting context
only and never establish verification.

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

**Verification (Phase 4, complete):** claim_002 is a `REGULATORY_STATUS` claim, so
`build_target()` resolves it to SEBI as a relevant authority and builds targeted
queries — the subject plus the register's own vocabulary, then
`site:sebi.gov.in` variants. With no `SERPAPI_KEY` the search is never attempted
and the status is `INSUFFICIENT_EVIDENCE` / `SEARCH_UNAVAILABLE`, stated as a
limitation.

With a key, the SEBI registry query returns nothing for that name. For a
registration-style claim that is `UNVERIFIED` / `ZERO_RESULTS` with the reason
*"No matching entry for the claimed party was found in the authoritative records
searched. This is an absence of confirmation, not a finding that the claim is
false."*

Two things it will **not** say, whatever the outcome:

- It will not say `CONTRADICTED` merely because nothing was found. Only a direct
  conflict in an identity-matched SEBI record produces that status (D-021).
- It will not say anything about the Telegram group, the 35% figure, or whether
  the offer is safe. The guarantee is a red flag (Phase 1) and risk is Phase 6;
  verification answers only whether a *fact* in the content is independently
  established (D-020).

The SEBI mention itself is never treated as confirmation: `SEBI` is a
`REGULATOR` entity, but no SEBI entity can be the subject that a "SEBI registered"
claim is about, so it is excluded from identity matching.

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
| Claim is an instruction or opinion | 6 | `NOT_APPLICABLE` / `NOT_A_FACTUAL_CLAIM`; **no query issued, no quota spent** |
| No query could be built for a claim | 6 | `INSUFFICIENT_EVIDENCE` / `NO_QUERY_BUILT`, confidence 0.0 |
| SerpAPI down / no key | 6, 7 | `INSUFFICIENT_EVIDENCE`, limitation stated |
| Searches partially complete | 6 | Status decided from what was retrieved; a warning states coverage is partial |
| Results name a similarly named organisation | 6 | `INSUFFICIENT_EVIDENCE` / `IDENTITY_AMBIGUOUS` — never treated as a match |
| Results come only from unlisted official hosts | 6 | `INSUFFICIENT_EVIDENCE` / `NO_CLAIM_RELEVANT_SOURCE`; the host is reported, not cited |
| Authoritative sources disagree | 6 | `INSUFFICIENT_EVIDENCE` / `CONFLICTING_AUTHORITATIVE_SOURCES`; no side is picked |
| A search provider raises despite its contract | 6 | Converted to `SEARCH_FAILED`; the investigation continues |
| Claim has no search results to show | 7 | `evidence = []` plus a factual warning naming the reason; never a placeholder item |
| Claim is `NOT_APPLICABLE` | 7 | `evidence = []` unconditionally, even if results were supplied (D-023) |
| A result was supplied that Stage 6 never saw | 7 | Dropped, with a warning stating how many were excluded |
| A result carries neither snippet nor title | 7 | That result cannot be quoted and raises rather than producing an empty excerpt |
| Status is `VERIFIED`/`CONTRADICTED` but nothing is showable | 7 | Status kept as Stage 6 decided it; a warning states the record could not be displayed |
| Tesseract missing | 1 | `OCR_UNAVAILABLE`, image investigation rejected with a clear message |
| PDF text extraction fails | 1 | `PDF_EXTRACTION_FAILED`, empty-text investigation with limitation |
| Invalid URL | 0 | 422 validation error, no investigation created |
| Empty input text | 1.5–3 | Mode `FALLBACK`, empty result, warning *"Input text was empty; nothing to extract."* |
| DB write fails | final | Investigation still returned to the user; persistence warning logged |
