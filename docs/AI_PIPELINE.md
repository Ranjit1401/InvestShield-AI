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
Claim Extraction
  ↓
Entity Extraction
  ↓
Red Flag Detection
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

### Stage 2 — Claim Extraction

**Owner:** `ClaimEntityAgent`

Splits content into **atomic, individually verifiable claims**. Never returns the
whole message as one claim.

```json
{
  "claim_id": "c1",
  "claim_text": "Our team is SEBI approved",
  "claim_type": "REGULATORY",
  "entities": ["Example Team"],
  "confidence": 0.8,
  "verification_required": true
}
```

**Claim types:** `REGULATORY`, `RETURN`, `PAYMENT`, `ENTITY_IDENTITY`,
`URGENCY`, `PLATFORM`, `COST`, `PERFORMANCE`, `OTHER`.

Implementation: `LLMService.structured_generate()` with a deterministic regex
fallback so the stage works with no API key.

### Stage 3 — Entity Extraction

**Owner:** `ClaimEntityAgent`

**Entity types:** `PERSON`, `COMPANY`, `BROKER`, `INVESTMENT_ADVISER`,
`PLATFORM`, `WEBSITE`, `DOMAIN`, `REGISTRATION_NUMBER`, `REGULATOR`,
`SOCIAL_HANDLE`, `PAYMENT_IDENTIFIER`.

```json
{
  "entity_id": "e1",
  "name": "Rahul Sharma",
  "entity_type": "PERSON",
  "registration_number": null,
  "mentions": 2,
  "confidence": 0.7
}
```

Deterministic extraction handles URLs, domains, registration numbers, UPI/IFSC
patterns, and currency amounts. Capitalised-name heuristics cover the common
case where no LLM is available.

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

**Claims extracted**

| id | claim | type | verifiable |
| --- | --- | --- | --- |
| c1 | The team is SEBI-approved | `REGULATORY` | yes |
| c2 | The team provides investment advice | `ENTITY_IDENTITY` | yes |
| c3 | Returns of 35% per month are guaranteed | `RETURN` | yes |
| c4 | Minimum investment is ₹25,000 | `COST` | no |
| c5 | Payment is required to activate the trading account | `PAYMENT` | yes |
| c6 | Investment is conducted through a Telegram group | `PLATFORM` | no |

**Entities:** the team (identity, unnamed), a Telegram group (platform), a
payment account (payment identifier), SEBI (regulator, mentioned not claimed).

**Red flags detected:** `GUARANTEED_RETURN`, `UNREALISTIC_RETURN`,
`URGENCY_PRESSURE`, `FAKE_REGULATORY_CLAIM`, `UNVERIFIED_ADVISER`,
`TELEGRAM_INVESTMENT_GROUP`, `THIRD_PARTY_PAYMENT`,
`ACCOUNT_ACTIVATION_FEE`.

**Verification:** c1 → targeted SEBI registry search. If nothing matches,
status `UNVERIFIED` with the reason *"No matching registration could be
independently verified from the searched authoritative records."*

**Risk:** multiple independent indicators → HIGH or CRITICAL band, with each
contribution itemised.

---

## 4. Failure Behaviour Matrix

| Failure | Stage | Result |
| --- | --- | --- |
| Groq down / no key | 2, 3, 11 | `LLM_SERVICE_ERROR`, regex fallback, report still generated |
| SerpAPI down / no key | 6, 7 | `SEARCH_SERVICE_ERROR`, claims become `INSUFFICIENT_EVIDENCE`, limitation stated |
| Tesseract missing | 1 | `OCR_UNAVAILABLE`, image investigation rejected with a clear message |
| PDF text extraction fails | 1 | `PDF_EXTRACTION_FAILED`, empty-text investigation with limitation |
| Invalid URL | 0 | 422 validation error, no investigation created |
| DB write fails | final | Investigation still returned to the user; persistence warning logged |
