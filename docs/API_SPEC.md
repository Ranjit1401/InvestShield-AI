# API_SPEC — InvestShield AI

Base URL: `http://localhost:8000`
Prefix: `/api`
Content type: `application/json` (uploads use `multipart/form-data`)

API schemas are **separate** from SQLAlchemy database models (D-010). This
document is the contract; the frontend client mirrors it.

---

## Status Codes

| Code | Meaning |
| --- | --- |
| 200 | Success |
| 201 | Resource created (investigation started) |
| 202 | Investigation accepted and processing |
| 400 | Malformed request |
| 404 | Investigation not found |
| 413 | Upload exceeds size limit |
| 422 | Validation error (invalid URL, empty text, wrong file type) |
| 429 | Upstream provider rate limit (search/LLM) |
| 500 | Internal error (must never leak stack traces) |
| 503 | A required service is unavailable (e.g. OCR for image input) |

Error body shape:

```json
{
  "error": {
    "code": "OCR_UNAVAILABLE",
    "message": "Text extraction from the image is currently unavailable.",
    "detail": null
  }
}
```

---

## GET /api/health

Liveness and configuration-coverage probe. Never exposes secrets — only
booleans indicating whether a key is configured.

**200 Response**

```json
{
  "status": "ok",
  "app": "investshield-ai",
  "version": "0.1.0",
  "environment": "local",
  "database": {
    "connected": true,
    "dialect": "sqlite"
  },
  "services": {
    "llm":   { "provider": "groq",    "configured": false, "healthy": null },
    "search":{ "provider": "serpapi", "configured": false, "healthy": null },
    "ocr":   { "available": false, "engine": "tesseract" },
    "pdf":   { "available": false, "engine": "pymupdf" }
  },
  "time": "2026-10-01T00:00:00Z"
}
```

Notes:

- `configured: false` is a valid, healthy state — the product degrades rather
  than failing (D-009).
- `version` and `status` are always present.

---

## POST /api/investigations

Generic entry point. Dispatches on `input_type`.

**Request**

```json
{
  "input_type": "TEXT",
  "text": "Our SEBI-approved expert team guarantees 35% monthly returns.",
  "url": null,
  "language": "en"
}
```

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `input_type` | `TEXT` \| `URL` \| `IMAGE` \| `PDF` | yes | |
| `text` | string | when `TEXT` | 1..20000 chars |
| `url` | string | when `URL` | must be `http`/`https` |
| `language` | `en` \| `hi` \| `mr` | no | report language, default `en` |

**202 Response**

```json
{
  "investigation_id": "3f2b1c9e-...",
  "status": "COMPLETED",
  "created_at": "2026-10-01T00:00:00Z"
}
```

---

## POST /api/investigations/text

Shorthand for `input_type = TEXT`.

**Request**

```json
{ "text": "🚨 Exclusive AI Trading Opportunity 🚨 ...", "language": "en" }
```

**202 Response** — identical to `POST /api/investigations`.

**422** when `text` is empty or exceeds the length cap.

---

## POST /api/investigations/url

Shorthand for `input_type = URL`. The server fetches and analyses the page
(Phase 12). SSRF guards apply.

**Request**

```json
{ "url": "https://example.com", "language": "en" }
```

**202 Response** — as above.

**422** for a malformed URL or a non-`http(s)` scheme.

---

## POST /api/investigations/upload

Screenshot or PDF upload.

**Request** — `multipart/form-data`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `file` | binary | yes | image or PDF, allow-listed MIME types |
| `language` | string | no | form field |

**Limits:** 10 MB max. Allowed: `image/png`, `image/jpeg`, `image/webp`,
`application/pdf`.

**202 Response** — as above.

**413** when the file exceeds the size limit.
**415/422** for a disallowed content type.
**503** `OCR_UNAVAILABLE` when an image is submitted but OCR is not installed.

---

## GET /api/investigations/{id}

Full result of one investigation.

**200 Response**

```json
{
  "investigation_id": "3f2b1c9e-...",
  "status": "COMPLETED",
  "input_type": "TEXT",
  "input_text": "...",
  "created_at": "2026-10-01T00:00:00Z",
  "risk_level": "HIGH",
  "risk_score": 125,
  "risk_factors": [
    { "code": "GUARANTEED_RETURN", "label": "Guaranteed return promise", "weight": 20, "reason": "The message promises a fixed monthly return." }
  ],
  "summary": "6 red flags, 5 claims and 4 entities were extracted from the submitted content.",
  "claims": [
    {
      "claim_id": "c1",
      "claim_text": "The team is SEBI approved",
      "claim_type": "REGULATORY",
      "entities": ["Example Team"],
      "confidence": 0.8,
      "verification_required": true,
      "status": "UNVERIFIED",
      "verification_reason": "No matching registration could be independently verified from the searched authoritative records."
    }
  ],
  "entities": [
    { "entity_id": "e1", "name": "Example Team", "entity_type": "COMPANY", "registration_number": null, "confidence": 0.6 }
  ],
  "red_flags": [
    {
      "code": "GUARANTEED_RETURN",
      "name": "Guaranteed return",
      "severity": "HIGH",
      "weight": 20,
      "description": "The message promises a fixed investment return.",
      "evidence": "guarantees 35% monthly returns"
    }
  ],
  "verification_results": [
    {
      "claim_id": "claim_001",
      "status": "VERIFIED",
      "reason_code": "AUTHORITATIVE_SOURCE_CONFIRMS",
      "reason": "A SEBI public record confirms this claim.",
      "confidence": 0.85,
      "source_ids": ["src_2d84c639a686"],
      "matched_result_ids": ["res_2d84c639a686"],
      "queries": ["site:sebi.gov.in \"Acme Capital Advisors\""],
      "warnings": []
    }
  ],
  "evidence": [
    {
      "id": "ev_2c1f0a9b3d47",
      "claim_id": "claim_001",
      "verification_status": "VERIFIED",
      "evidence_type": "REGULATORY_RECORD",
      "relationship": "SUPPORTS",
      "relevance": "HIGH",
      "excerpt": "Acme Capital Advisors is registered as an investment adviser.",
      "excerpt_origin": "snippet",
      "matched_cue": "registered as",
      "provider_query": "site:sebi.gov.in \"Acme Capital Advisors\"",
      "is_proof": true,
      "url": "https://www.sebi.gov.in/intermediaries/acme",
      "source": {
        "source_id": "src_2d84c639a686",
        "result_id": "res_2d84c639a686",
        "url": "https://www.sebi.gov.in/intermediaries/acme",
        "canonical_url": "https://www.sebi.gov.in/intermediaries/acme",
        "domain": "sebi.gov.in",
        "title": "Acme Capital Advisors",
        "source_type": "REGULATOR",
        "source_tier": "TIER_1_PRIMARY_REGULATOR",
        "retrieved_at": "2026-10-01T00:00:00Z",
        "position": 1,
        "source_priority": 1,
        "is_authoritative": true
      }
    }
  ],
  "sources": [
    {
      "source_id": "src_2d84c639a686",
      "result_id": "res_2d84c639a686",
      "url": "https://www.sebi.gov.in/intermediaries/acme",
      "domain": "sebi.gov.in",
      "title": "Acme Capital Advisors",
      "source_type": "REGULATOR",
      "source_tier": "TIER_1_PRIMARY_REGULATOR",
      "retrieved_at": "2026-10-01T00:00:00Z",
      "position": 1
    }
  ],
  "why_flagged": [
    { "title": "Guaranteed returns", "explanation": "The message promises a fixed monthly return." }
  ],
  "safety_guidance": [
    "Verify the intermediary's registration independently before transferring funds.",
    "Do not rely solely on information provided by the investment promoter."
  ],
  "limitations": [
    "External verification could not be completed because the search service is not configured."
  ],
  "timeline": [
    { "step": "input_received",     "label": "Input received",          "status": "COMPLETED", "detail": null, "timestamp": "..." },
    { "step": "claims_extracted",   "label": "Claims extracted",        "status": "COMPLETED", "detail": "5 claims", "timestamp": "..." },
    { "step": "red_flags_detected", "label": "Red flags detected",      "status": "COMPLETED", "detail": "6 red flags", "timestamp": "..." },
    { "step": "sources_searched",   "label": "Authoritative sources searched", "status": "SKIPPED", "detail": "Search service unavailable", "timestamp": "..." },
    { "step": "evidence_assembled", "label": "Evidence assembled",      "status": "COMPLETED", "detail": "1 evidence item", "timestamp": "..." },
    { "step": "risk_calculated",    "label": "Risk assessment completed", "status": "COMPLETED", "detail": "HIGH", "timestamp": "..." },
    { "step": "report_generated",   "label": "Report generated",        "status": "COMPLETED", "detail": null, "timestamp": "..." }
  ],
  "language": "en"
}
```

**404** when the investigation id is unknown.

### Enumerations

`status`: `PENDING` | `PROCESSING` | `COMPLETED` | `FAILED`
`claim_type` (14): `GUARANTEE_CLAIM` | `RETURN_PROMISE` | `PROFIT_PROMISE` |
`PERFORMANCE_CLAIM` | `CREDENTIAL_CLAIM` | `REGULATORY_STATUS` | `COMPANY_CLAIM` |
`PRODUCT_CLAIM` | `OWNERSHIP_CLAIM` | `AFFILIATION_CLAIM` | `WITHDRAWAL_CLAIM` |
`PAYMENT_INSTRUCTION` | `INVESTMENT_OPPORTUNITY` | `OTHER`
`entity_type` (18): `PERSON` | `COMPANY` | `ORGANIZATION` | `REGULATOR` |
`BROKER` | `INVESTMENT_ADVISER` | `PLATFORM` | `WEBSITE` | `DOMAIN` |
`PRODUCT` | `FINANCIAL_INSTRUMENT` | `LOCATION` | `SOCIAL_HANDLE` |
`REGISTRATION_NUMBER` | `BANK_ACCOUNT` | `UPI_ID` | `PHONE_NUMBER` | `EMAIL` |
`OTHER`
`extraction_mode`: `LLM` | `FALLBACK` | `PARTIAL`
`claim_entity_relationship`: `MENTIONS` | `SUBJECT`
`risk_level`: `LOW` | `MEDIUM` | `HIGH` | `CRITICAL`
`verification status`: `VERIFIED` | `UNVERIFIED` | `CONTRADICTED` | `INSUFFICIENT_EVIDENCE` | `NOT_APPLICABLE`
`evidence_type` (Phase 5): `REGULATORY_RECORD` | `GOVERNMENT_RECORD` |
`EXCHANGE_RECORD` | `OFFICIAL_ENTITY_SOURCE` | `SEARCH_RESULT`
`relationship` (Phase 5): `SUPPORTS` | `CONTRADICTS` | `IDENTITY_REFERENCE` |
`CONTEXT` | `MENTIONS`
`relevance` (Phase 5): `HIGH` | `MEDIUM` | `LOW`
`excerpt_origin` (Phase 5): `snippet` | `title`

Note: `evidence_type`, `relationship` and `relevance` are three independent
axes, not one taxonomy. `evidence_type` describes *the kind of record*,
`relationship` describes *its bearing on the claim*, and `relevance` describes
*how directly it bears on it*. They are kept separate because a combined
vocabulary encodes the same fact twice and the two copies eventually disagree
(D-023).

There is no `source_credibility` enum. The credibility of a publisher is already
expressed by Phase 3's `source_type` and Phase 4's `source_tier`, both carried
through unchanged; a third credibility enum would be a second answer to the same
question (D-012, D-018).

`relevance` is a **label, not a number**. The `0..1` score sketched before Phase 5
was deliberately not implemented, because any such number becomes an undeclared
input to the Phase 6 risk score (D-007). Phase 6 consumes `proof_count` and
`source_count` as counts and shows the items behind them.

Note that `claim.confidence` is **extraction** confidence — how sure the
extractor is that the text makes this kind of claim. It is not a probability
that the claim is true, and it is never a fraud score.

---

## GET /api/investigations

Paginated history.

**Query parameters**

| Name | Type | Default | Notes |
| --- | --- | --- | --- |
| `limit` | int | 20 | 1..100 |
| `offset` | int | 0 | |
| `input_type` | string | — | optional filter |
| `risk_level` | string | — | optional filter |

**200 Response**

```json
{
  "total": 42,
  "limit": 20,
  "offset": 0,
  "items": [
    {
      "investigation_id": "3f2b1c9e-...",
      "status": "COMPLETED",
      "input_type": "TEXT",
      "excerpt": "🚨 Exclusive AI Trading Opportunity 🚨 Our SEBI-approved...",
      "risk_level": "HIGH",
      "risk_score": 125,
      "red_flag_count": 6,
      "claim_count": 5,
      "created_at": "2026-10-01T00:00:00Z"
    }
  ]
}
```

---

### Search payload (built in Phase 3, not yet exposed over HTTP)

`SearchService.search(query, max_results)` produces this object. Phase 4 consumes
it internally; it reaches `POST /api/investigations/text` only in Phase 8.

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
      "canonical_url": "https://sebi.gov.in/",
      "retrieved_at": "2026-10-01T00:00:00Z",
      "position": 1
    }
  ],
  "provider": "serpapi",
  "status": "OK",
  "error_code": null,
  "provider_query_sent": true,
  "warnings": [],
  "searched_at": "2026-10-01T00:00:00Z"
}
```

Guarantees a client may rely on:

- `status` distinguishes `OK` (**including** zero results), `UNAVAILABLE` (never
  searched) and `ERROR` (searched and failed). A failure is never presented as an
  empty successful search.
- `success` is `true` only for `OK`. `degraded` is `true` for `UNAVAILABLE` and
  `ERROR`.
- `provider_query_sent` is `false` whenever `UNAVAILABLE`, so "we never looked" is
  auditable.
- `source_type` describes **who published** a document. It is not a verdict; see
  `SourceType` below.
- `error_code`, when present, is one of `SEARCH_NOT_CONFIGURED`,
  `SEARCH_INVALID_QUERY`, `SEARCH_SERVICE_ERROR`, `SEARCH_TIMEOUT`,
  `SEARCH_RATE_LIMITED`, `SEARCH_AUTH_ERROR`, `SEARCH_INVALID_RESPONSE`.
- API keys never appear in any field, warning, or log.

## Enumerations

`source_type`: `REGULATOR` | `GOVERNMENT` | `EXCHANGE` | `OFFICIAL_ENTITY` |
`TRUSTED_SECONDARY` | `GENERAL_WEB` | `UNKNOWN`
`search status`: `OK` | `UNAVAILABLE` | `ERROR`

`source_type` ranks *who published a document*, by hostname identity only. It is
**not** a verification outcome: `REGULATOR` does not mean "verified" and
`GENERAL_WEB` does not mean "false".

## Implementation Status

| Endpoint | Phase | Status |
| --- | --- | --- |
| `GET /api/health` | 0 | Implemented |
| `POST /api/investigations` | 8 | Planned |
| `POST /api/investigations/text` | 8 | Planned |
| `POST /api/investigations/url` | 8 / 12 | Planned |
| `POST /api/investigations/upload` | 8 / 13 / 14 | Planned |
| `GET /api/investigations` | 8 / 9 | Planned |
| `GET /api/investigations/{id}` | 8 | Planned |

### Extraction payload (built in Phase 2, not yet exposed over HTTP)

`ExtractionService.extract(raw_text)` produces this object. It becomes part of
the `POST /api/investigations/text` response in Phase 8; until then it is a
library contract only.

```json
{
  "claims": [
    {
      "id": "claim_003",
      "text": "Our SEBI-approved expert team guarantees 35% monthly returns",
      "claim_type": "GUARANTEE_CLAIM",
      "confidence": 0.85,
      "evidence_span": { "start": 42, "end": 96, "text": "Our SEBI-approved expert team guarantees 35% monthly returns" },
      "entity_ids": ["entity_001"],
      "is_complete_sentence": true,
      "signals": ["GUARANTEE_CLAIM", "RETURN_PROMISE", "REGULATORY_STATUS"],
      "metadata": { "percentages": "35%", "periods": "monthly" }
    }
  ],
  "entities": [
    {
      "id": "entity_001",
      "name": "SEBI",
      "entity_type": "REGULATOR",
      "normalized_name": "securities and exchange board of india",
      "confidence": 0.9,
      "evidence_span": { "start": 45, "end": 49, "text": "SEBI" },
      "metadata": {}
    }
  ],
  "relationships": [
    { "claim_id": "claim_002", "entity_id": "entity_001", "relationship": "SUBJECT" }
  ],
  "extraction_mode": "FALLBACK",
  "prompt_version": "extraction-v1",
  "model": null,
  "source_text": "<the untouched submitted text>",
  "normalized_text": "<what extraction actually scanned>",
  "processing_warnings": [
    "LLM extraction is unavailable; deterministic extraction was used. Claim and entity coverage is limited to pattern-based extraction."
  ]
}
```

Guarantees a client may rely on:

- `evidence_span.start/end` are offsets into `source_text`, and
  `source_text[start:end] == text` holds for every claim and entity. `model` is
  `null` whenever `extraction_mode` is `FALLBACK`.
- There is **no** verification verdict anywhere in this payload. `confidence` is
  extraction confidence only.

---

### Verification payload (built in Phase 4, not yet exposed over HTTP)

`VerificationService.verify_claims(claims, entities)` produces this object. It
becomes part of the `POST /api/investigations/text` response in Phase 8; until
then it is a library contract only.

```json
{
  "results": [
    {
      "claim_id": "claim_002",
      "claim_type": "REGULATORY_STATUS",
      "status": "UNVERIFIED",
      "reason": "No matching entry for Acme Capital Advisors was found in the authoritative records searched. This is an absence of confirmation, not a finding that the claim is false.",
      "reason_code": "ZERO_RESULTS",
      "confidence": 0.45,
      "source_ids": [],
      "matched_result_ids": [],
      "queries": [
        "\"Acme Capital Advisors\" registration",
        "\"Acme Capital Advisors\" registration site:sebi.gov.in"
      ],
      "warnings": [
        "Absence of a matching record is not evidence that the claim is false."
      ],
      "is_positive": false,
      "is_inconclusive": true
    }
  ],
  "warnings": [],
  "verified_count": 0,
  "unverified_count": 1,
  "contradicted_count": 0,
  "insufficient_evidence_count": 0,
  "not_applicable_count": 0
}
```

Guarantees a client may rely on:

- `status` is one of `VERIFIED`, `UNVERIFIED`, `CONTRADICTED`,
  `INSUFFICIENT_EVIDENCE`, `NOT_APPLICABLE`. There is no other value, and there
  is no verdict status (D-020).
- `reason` is rendered from a fixed template keyed by `reason_code`. It is never
  generated, and it never contains verdict vocabulary.
- `confidence` is confidence **in the status assigned**, given the evidence
  found. It is **not** a probability that the investment is fraudulent, will
  lose money, or that the claim is true.
- `UNVERIFIED` and `INSUFFICIENT_EVIDENCE` both mean "this could not be
  established". Neither means "the claim is false".
- `CONTRADICTED` occurs only when a claim-relevant authoritative record directly
  conflicts with the claim for the identified entity. Zero results, a failed
  search, an absent key and an identity mismatch never produce it (D-021).
- `source_ids` is non-empty exactly for `VERIFIED` and `CONTRADICTED`, and every
  id refers to a document that was actually retrieved.
- `matched_result_ids` and `source_ids` are `src_` / `res_` identifiers derived
  as `sha256(canonical_url)[:12]`. Phase 5 attaches evidence objects to them, and
  rejects any supplied document whose id is absent from here.
- The five counts are recomputed from `results` by the schema and cannot
  disagree with them.
- `reason_code` is one of `AUTHORITATIVE_SOURCE_CONFIRMS`, `NO_CONFIRMATION_FOUND`,
  `AUTHORITATIVE_SOURCE_CONTRADICTS`, `CONFLICTING_AUTHORITATIVE_SOURCES`,
  `SEARCH_UNAVAILABLE`, `SEARCH_FAILED`, `ZERO_RESULTS`, `IDENTITY_AMBIGUOUS`,
  `IDENTITY_NOT_FOUND`, `NO_CLAIM_RELEVANT_SOURCE`, `NO_QUERY_BUILT`,
  `NOT_A_FACTUAL_CLAIM`.
- A verification result says nothing about whether the offer is safe, and a
  `VERIFIED` claim may still carry red flags and a high risk band.

## Enumerations

`verification status`: `VERIFIED` | `UNVERIFIED` | `CONTRADICTED` |
`INSUFFICIENT_EVIDENCE` | `NOT_APPLICABLE`

`source_tier`: `TIER_1_PRIMARY_REGULATOR` | `TIER_2_GOVERNMENT` |
`TIER_3_EXCHANGE` | `TIER_4_OFFICIAL_ENTITY` | `TIER_5_TRUSTED_SECONDARY` |
`TIER_6_GENERAL_WEB` | `UNKNOWN`

Tiers 1–3 are authoritative enough to settle a claim they are relevant to. A
tier describes the publisher, not the document: a `TIER_1` page that says nothing
about a claim verifies nothing (D-006, D-021).

---

### Evidence payload (built in Phase 5, not yet exposed over HTTP)

`EvidenceService.build_claim()` produces an `EvidenceResponse`. It becomes part of
the `GET /api/investigations/{id}` response in Phase 8; until then it is a library
contract only.

```json
{
  "claim_id": "claim_001",
  "verification_status": "VERIFIED",
  "verification_reason_code": "AUTHORITATIVE_SOURCE_CONFIRMS",
  "evidence": [
    {
      "id": "ev_2c1f0a9b3d47",
      "claim_id": "claim_001",
      "verification_status": "VERIFIED",
      "evidence_type": "REGULATORY_RECORD",
      "relationship": "SUPPORTS",
      "relevance": "HIGH",
      "excerpt": "Acme Capital Advisors is registered as an investment adviser.",
      "excerpt_origin": "snippet",
      "matched_cue": "registered as",
      "provider_query": "site:sebi.gov.in \"Acme Capital Advisors\"",
      "is_proof": true,
      "trace_path": "claim_001 -> ev_2c1f0a9b3d47 -> res_2d84c639a686 -> src_2d84c639a686 (https://www.sebi.gov.in/intermediaries/acme)"
    }
  ],
  "sources": [
    {
      "source_id": "src_2d84c639a686",
      "result_id": "res_2d84c639a686",
      "url": "https://www.sebi.gov.in/intermediaries/acme",
      "domain": "sebi.gov.in",
      "title": "Acme Capital Advisors",
      "source_type": "REGULATOR",
      "source_tier": "TIER_1_PRIMARY_REGULATOR",
      "retrieved_at": "2026-10-01T00:00:00Z",
      "position": 1
    }
  ],
  "warnings": [],
  "built_at": "2026-10-01T00:00:00Z",
  "evidence_count": 1,
  "supports_count": 1,
  "contradicts_count": 0,
  "context_count": 0,
  "source_count": 1,
  "proof_count": 1
}
```

Guarantees a client may rely on:

- `excerpt` is **verbatim text the provider returned** — `SearchResult.snippet`, or
  `SearchResult.title` when there is no snippet. `excerpt_origin` names which. It
  is never summarised, paraphrased, or stitched together from two fields
  (D-023).
- `id` is `ev_` + `sha256(claim, source, result, excerpt origin, relationship,
  excerpt)[:12]`. It is derived, never minted, so rebuilding the same evidence
  yields the same id. `trace_path` uses ASCII `->` and is safe to print anywhere.
- `source.source_id` / `source.result_id` are Phase 4's `src_` / `res_` ids for
  the same document, and `source.source_type` / `source.source_tier` are Phase 3's
  and Phase 4's classifications carried through unchanged.
- `retrieved_at` is the provider's retrieval time, never re-stamped when the
  evidence bundle is assembled.
- `verification_status` is **copied** from the claim's `VerificationResult`, never
  recomputed. Evidence cannot contradict the decision it explains (D-024).
- `evidence` is empty — never a placeholder item — whenever nothing was
  genuinely retrieved: search unavailable, search failed, zero results, or a
  `NOT_APPLICABLE` claim. `warnings` states which (D-023).
- `warnings` may report that supplied results were excluded because the claim's
  verification never examined them, and may report that a `VERIFIED` or
  `CONTRADICTED` status has no retrieved text to show. In both cases the status
  stands as verification decided it.
- `proof_count` counts items that speak to the claim — authoritative,
  identity-matched, claim-relevant records that say something about it. It is not
  a probability and not a safety judgement; a claim can be `VERIFIED` and still
  carry red flags and a high risk band.
- `context_count` counts items that inform without establishing anything. These
  are shown, not suppressed, so a reader can see what was searched and found
  nothing (D-006).
- The five counts are recomputed by the schema and cannot disagree with `evidence`.
- `EvidenceBundleResponse.claims_without_evidence` is tracked explicitly, because
  "we found nothing" must stay visible rather than vanish.

## Enumerations

`evidence_type`: `REGULATORY_RECORD` | `GOVERNMENT_RECORD` | `EXCHANGE_RECORD` |
`OFFICIAL_ENTITY_SOURCE` | `SEARCH_RESULT`
`relationship`: `SUPPORTS` | `CONTRADICTS` | `IDENTITY_REFERENCE` | `CONTEXT` |
`MENTIONS`
`relevance`: `HIGH` | `MEDIUM` | `LOW`
`excerpt_origin`: `snippet` | `title`
