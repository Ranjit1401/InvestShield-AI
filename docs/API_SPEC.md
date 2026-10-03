# API_SPEC — InvestShield AI

Base URL: `http://localhost:8000`
Prefix: `/api`
Content type: `application/json` (uploads use `multipart/form-data`)

API schemas are **separate** from SQLAlchemy database models (D-010). This
document is the contract; the frontend client mirrors it.

> **Machine-readable mirror.** The Phase 11 frontend carries a TypeScript copy of
> this contract at `frontend/src/types/api.ts`. Every union and field in it was
> taken from the **running** FastAPI application (`app.openapi()` plus the enums in
> `app/schemas/*`) rather than transcribed from this prose, so where the two ever
> disagree, the generated types and the code are authoritative. The frontend
> adapts to the API; it does not redefine it. Phase 11 changed no contract;
> Phase 12 changed it additively (a new endpoint, a new `url_source` field, a
> new limits field), which is why the mirror still holds.

---

## Status Codes

| Code | Meaning | In Phase 8? |
| --- | --- | --- |
| 200 | Success — including a **partial** run, which returns `status: PARTIAL` | yes |
| 201 | Resource created | not used |
| 202 | Investigation accepted and processing | **withdrawn**, see below |
| 400 | Malformed request | not used |
| 404 | Investigation not found | Phase 9 |
| 413 | Upload exceeds size limit | not used — an oversized upload is a `422` validation error (`OCR_IMAGE_TOO_LARGE`, Phase 13) |
| 422 | Validation error (empty text, over-length, unanalysed input type, SSRF-blocked URL) | yes (URL in Phase 12) |
| 429 | Upstream provider rate limit | not used — a rate limit is a degradation, not a client error (D-009) |
| 500 | A stage broke its documented contract | yes |
| 502 | The submitted site itself failed (DNS, TLS, timeout, HTTP error, wrong content type) | Phase 12 |
| 503 | A required service is unavailable (or a capability is disabled by configuration) | Phase 12 for `URL_FETCH_UNAVAILABLE`; see below for the general rule |

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

`detail` is a structured object, not a string, so a client can branch on it. For
investigation failures it is a `FailureDetail`:

```json
{
  "error": {
    "code": "INPUT_TYPE_NOT_SUPPORTED",
    "message": "This version analyses text, URL and image input. The submitted input type was recognised but is not analysed, so no investigation was performed.",
    "detail": {
      "errors": [
        { "code": "INPUT_TYPE_NOT_SUPPORTED", "stage": "input", "error_type": null }
      ],
      "submitted_input_type": "PDF",
      "supported_input_types": ["TEXT", "URL", "IMAGE"]
    }
  }
}
```

Three rules govern every failing endpoint:

- **One shape.** Every failure uses the envelope above, so a client handles one
  error contract rather than learning a new one per route.
- **The status says whose fault it is.** `INPUT_EMPTY`,
  `INPUT_TYPE_NOT_SUPPORTED` and the URL guard refusals are `422` — the
  caller must fix them. A site that cannot be fetched is `502` — the
  request was fine, the site failed. Every other recorded graph error is a
  stage breaking a contract that documented it never would, and is `500`.
- **A degraded run is not a failure.** When search, the LLM or extraction is
  unavailable the request still returns `200` with `status: PARTIAL` and the
  limitation listed. Failing the request would make the product look broken at
  the exact moment it is correctly reporting that it checked less than it wanted
  (D-009). The same rule covers a page whose text cannot be retrieved: the run
  continues with a `PAGE_TEXT_NOT_RETRIEVED` warning.

`message` and `detail` never contain a provider response, a stack trace or a
connection string — only the graph's own fixed wording and, where relevant, an
exception's *class name*.

### The 202 design is withdrawn

Earlier revisions of this document specified `202 Accepted` plus
`GET /api/investigations/{id}`. That design presupposes persistence, which is
Phase 9. Phase 8 returns the **complete investigation synchronously with `200`**,
because by the time the response is written the run has already finished; a `202`
would be a lie about outstanding work. Returning an `investigation_id` with nothing
stored behind it would also invite a client to poll an endpoint that does not
exist. Retrieval is added alongside the POST in Phase 9, which is purely additive
(D-036).

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
- `status` is `ok` or `degraded`. `degraded` means **the configured database could
  not be reached**; it says nothing about the optional services, whose unavailability
  is reported per-service and is never a failure.
- `database.dialect` is reported even when `connected` is `false`, because the
  dialect is a fact about configuration and the connection is a fact about the
  moment. A client that could not tell an unconfigured database from a
  misconfigured one would have nothing to act on.
- `database.error` is the **exception class name only**. Driver messages embed the
  DSN, including credentials, so they are logged and never returned.

**Phase 8 change — the probe follows the app, not the environment.** The route
used to probe a module-level engine built from `get_settings()` at import time,
which meant a test passing its own `Settings` still probed the developer's actual
database, and an unreachable ambient `DATABASE_URL` made a healthy app report
`degraded`. `create_app` now builds one `Database` from the settings it was given,
parks it on `app.state.database`, exposes it through `get_database_dep`, and
disposes it on shutdown.

---

## POST /api/investigations

Typed entry point. Dispatches on `input_type`. **Implemented in Phase 8; stores
its result as of Phase 9.**

**Request**

```json
{
  "input_type": "TEXT",
  "text": "Our SEBI-approved expert team guarantees 35% monthly returns.",
  "language": "en"
}
```

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `input_type` | `TEXT` \| `URL` \| `IMAGE` \| `PDF` | no (default `TEXT`) | all four declared; only `TEXT` is analysed |
| `text` | string | yes | 1..20000 chars; whitespace-only is refused |
| `language` | `en` \| `hi` \| `mr` | no | recorded and echoed; **not translated** in Phase 8 |

Unknown fields are refused (`422`). `input_type` is explicit rather than inferred
so that an unsupported kind is a typed rejection rather than a guess.

**200 Response** — the complete `InvestigationResponse`, documented under
[Investigation Response](#investigation-response).

The response is built **before** the store write. A storage failure therefore
returns an error rather than a `200` whose `investigation_id` names a run the
database never accepted.

**422** when `text` is empty or over-length, or when `input_type` is `URL`,
`IMAGE`, `PDF` or unrecognised. The `detail` names the supported kinds.

---

## POST /api/investigations/text

Shorthand for `input_type = TEXT`, with `input_type` fixed by the route so a
client cannot accidentally submit a URL and have it analysed as prose.
**Implemented in Phase 8; stores its result as of Phase 9.**

**Request**

```json
{ "text": "🚨 Exclusive AI Trading Opportunity 🚨 ...", "language": "en" }
```

**200 Response** — identical to `POST /api/investigations`.

**422** when `text` is empty, over-length, or an unknown field is present.

---

## GET /api/investigations/limits

Discovery: what this version accepts. **Implemented in Phase 8.**

Exists so a client can learn the constraint before building a submission rather
than after being refused. Needs no investigation and never fails.

**200 Response**

```json
{
  "supported_input_types": ["TEXT", "URL", "IMAGE"],
  "max_text_length": 20000,
  "max_url_length": 2048,
  "max_upload_bytes": 10485760,
  "allowed_image_types": ["image/png", "image/jpeg", "image/webp"],
  "ocr_languages": "eng",
  "languages": ["en", "hi", "mr"],
  "translation_enabled": false
}
```

**Schema limitation (Phase 10).** This route is declared
`-> dict[str, object]`, so its OpenAPI schema is an inline
`additionalProperties: true`. A client reading `/openapi.json` or generating a typed
client from it gets no machine-readable shape for these four fields, and must treat
them as a documented convention. The response body itself is correct, stable and
pinned by `tests/api/test_openapi_contract.py`; only the schema is loose.

This is recorded rather than fixed. Adding a response model would give the schema
generator something to work with, but it means hand-maintaining a model for four
fields to satisfy a tool rather than a client — and a client that needs typed
discovery is a better reason to add it than a coverage percentage would have been.
It should be revisited when a frontend client is actually generated from this
document.

**Route ordering note.** `/api/investigations/limits` is declared *before*
`/api/investigations/{investigation_id}`. FastAPI matches routes in declaration
order, so declaring it after would make `limits` a path parameter and return a
`404`. Pinned by `test_the_limits_endpoint_is_not_shadowed_by_the_id_route`.

---

## Investigation Response

The shape both `POST` endpoints return. Generated from
`app/schemas/api.py::InvestigationResponse`; `/docs` is always authoritative.

```json
{
  "investigation_id": "inv_3f2b1c9e4a7d8e01",
  "status": "PARTIAL",
  "input_type": "TEXT",
  "language": "en",
  "current_stage": "risk",

  "claims": [ /* Phase 2 Claim objects, unchanged */ ],
  "entities": [ /* Phase 2 Entity objects, unchanged */ ],
  "red_flags": [ /* Phase 1 RedFlag objects, with spans, unchanged */ ],
  "verification_results": [ /* Phase 4 VerificationResult objects */ ],
  "evidence": [ /* Phase 5 EvidenceResponse objects */ ],
  "risk_assessment": { /* Phase 6 RiskAssessment object, verbatim, caveats included */ },

  "timeline": [
    { "stage": "extraction", "status": "PARTIAL",
      "message": "Extraction completed without the language model.",
      "at": "2026-10-01T00:00:00Z" }
  ],
  "limitations": ["EXTRACTION_FALLBACK", "SEARCH_UNAVAILABLE"],
  "warnings": [
    { "code": "SEARCH_UNAVAILABLE", "stage": "verification",
      "message": "External search was unavailable, so no external record could be consulted for any claim." }
  ],
  "errors": [],

  "url_source": null,

  "started_at": "2026-10-01T00:00:00Z",
  "completed_at": "2026-10-01T00:00:04Z"
}
```

`url_source` is present on every response and is `null` for `TEXT`
investigations. For a URL investigation it describes what was fetched
(Phase 12):

### Fields the API layer owns

Everything else — `claims`, `entities`, `red_flags`, `verification_results`,
`evidence`, `risk_assessment` — is the **Phase 1-6 domain object itself**, embedded
unchanged. A projection would create a second definition of "a claim" that could
drift from Phase 2's, and every such drift would be a silent wrong answer rather
than a type error (D-038).

| Field | Meaning |
| --- | --- |
| `investigation_id` | A digest of the submitted input, so a re-run is comparable. **Not unique per investigation** — Phase 9 gives it a real id |
| `status` | `COMPLETED` \| `PARTIAL` \| `FAILED`, derived from the stage timeline |
| `current_stage` | The last stage that ran |
| `limitations` | Deduplicated warning **codes**, first-seen order. Branch on these |
| `warnings` | The same limitations, human-readable, with the stage that recorded each |
| `errors` | Recorded failures. Any entry means `status: FAILED` |
| `url_source` | URL investigations only: what was fetched (submitted/normalized/final URL, hostname, resolved addresses, redirect facts, status, content type, TLS, charset, byte size, page title, meta description, fetch time). `null` for `TEXT` |
| `started_at` / `completed_at` | Wall-clock metadata |
| `language` | Echo of the request. No message is translated |

Absent state keys become empty lists rather than being omitted, so a client can
read `claims.length` without first checking for the field's existence. The
distinction that must survive is preserved by `status` and `limitations`: a client
can always tell a stage that ran and found nothing from a stage that never ran.

### `limitations` is codes, not prose

The earlier draft of this section showed `limitations` as an array of English
sentences. That is changed. A client must be able to branch on a limitation
without parsing natural language (D-009), so `limitations` holds the stable codes
and `warnings` holds the readable form. Both are present because they serve
different consumers: the codes are for logic, the messages are for display, and
collapsing them into one list would force every client to re-derive half of it.

Stable codes, from `app/graph/nodes.py`:

`INPUT_TYPE_NOT_ANALYSED`, `EXTRACTION_FALLBACK`, `EXTRACTION_PARTIAL`,
`NO_CLAIMS_EXTRACTED`, `NO_RED_FLAGS_DETECTED`, `SEARCH_UNAVAILABLE`,
`PARTIAL_VERIFICATION`, `EVIDENCE_UNAVAILABLE`, `SEARCH_RESULTS_NOT_RECORDED`,
`URL_CONTENT_TRUNCATED`, `PAGE_TEXT_NOT_RETRIEVED`.

Stable failure codes:

`INPUT_EMPTY`, `INPUT_TYPE_NOT_SUPPORTED`, `EXTRACTION_FAILED`,
`RED_FLAG_DETECTION_FAILED`, `VERIFICATION_FAILED`, `RISK_ASSESSMENT_FAILED`.

Stable URL fault codes (Phase 12), answered before the graph runs.
Caller faults — `422`: `URL_EMPTY`, `URL_INVALID`, `URL_TOO_LONG`,
`URL_SCHEME_UNSUPPORTED`, `URL_HOST_MISSING`, `URL_HOST_TOO_LONG`,
`URL_CREDENTIALS_NOT_ACCEPTED`, `URL_CONTROL_CHARACTERS_NOT_ACCEPTED`,
`URL_ADDRESS_BLOCKED`, `URL_METADATA_ADDRESS_BLOCKED`,
`URL_METADATA_HOSTNAME_BLOCKED`. Site faults — `502`:
`URL_DNS_FAILED`, `URL_FETCH_FAILED`, `URL_TIMEOUT`,
`URL_TOO_MANY_REDIRECTS`, `URL_HTTP_ERROR`, `URL_CONTENT_TOO_LARGE`,
`URL_CONTENT_TYPE_UNSUPPORTED`. Capability faults — `503`:
`URL_FETCH_DISABLED`, `URL_FETCH_UNAVAILABLE`.

### `status` is derived from the timeline, not the warnings

`COMPLETED` when every stage that ran ran to completion. `PARTIAL` when any stage
was `PARTIAL` or `SKIPPED`. `FAILED` when the state recorded any error.

Note that `NO_RED_FLAGS_DETECTED` is a **limitation code but not a degraded run** —
it is recorded whenever content matched none of Phase 1's rules, which is most
benign content. Treating "warnings exist" as "the run is incomplete" would badge
every clean investigation as a partial one. The stage statuses already encode the
right distinction, so the adapter reads those rather than re-deciding (D-037).

---

## POST /api/investigations/url

Shorthand for `input_type = URL`. **Implemented in Phase 12; stores its
result like the text endpoint.** The server fetches the submitted page
under an SSRF guard, extracts its visible text, and runs the standard
pipeline over that text.

**Request**

```json
{ "url": "https://example.com/investment", "language": "en" }
```

`url` is required, 1–2048 characters, and must be `http` or `https`.
Unknown fields are rejected.

**Guards, in order.** Syntax and length → scheme → host → credentials →
control characters → address policy (every address the connection would
actually reach, including each redirect hop and every name a hostname
resolves to). The policy is an allowlist of global unicast space:
private, loopback, link-local and metadata addresses are refused, as are
obfuscated IPv4 literals (`127.1`, `0x7f000001`, `2130706433`) and
hostnames that *resolve* inward (`localhost`, `ip-127-0-0-1.sslip.io`).

**Fetch budget.** One pinned connection per redirect hop, at most 3
redirects, at most 2 MiB of body, a 5-second connect timeout, a
10-second read timeout and a 20-second total deadline.
Non-HTML content types are refused. Over-budget content is truncated and
the truncation is recorded as a `URL_CONTENT_TRUNCATED` warning.

**200 Response** — the standard `InvestigationResponse`, with
`input_type: "URL"` and `url_source` describing the fetch:

```json
{
  "investigation_id": "inv_3f2b1c9e4a7d8e01",
  "status": "PARTIAL",
  "input_type": "URL",
  "url_source": {
    "submitted_url": "https://example.com/investment",
    "normalized_url": "https://example.com/investment",
    "final_url": "https://example.com/investment",
    "hostname": "example.com",
    "resolved_addresses": ["93.184.216.34"],
    "redirected": false,
    "redirect_count": 0,
    "page_title": "Example Investment",
    "meta_description": null,
    "http_status": 200,
    "content_type": "text/html",
    "is_https": true,
    "charset": "UTF-8",
    "byte_size": 577,
    "fetched_at": "2026-10-03T00:00:04Z"
  },
  …the rest of the response, unchanged…
}
```

Every `url_source` field is either a fact about the HTTP exchange
or a string the page published about itself. None of them is a
judgement: `page_title` is what the page called itself, `is_https`
is a transport fact, and neither implies anything about
legitimacy (D-055). A page whose visible text is empty still runs
the pipeline and returns `200` with `status: PARTIAL` and a
`PAGE_TEXT_NOT_RETRIEVED` warning — an unreadable page is a
limitation, not a failure (D-009).

**422** for caller faults: empty, over-length, invalid, non-http(s)
scheme, embedded credentials, control characters, and every SSRF
refusal. **502** for site faults: DNS failure, connection failure,
timeout, too many redirects, non-2xx HTTP status, over-budget content
and unsupported content type. **503** when fetching is disabled by
configuration. The taxonomy answers *whose fault it is* — a blocked
destination is the caller's mistake; a dead site is not (D-054).

No `message` or `detail` ever carries a socket error, TLS diagnostic,
resolved address, response header or provider text; wording comes from
the fixed `URL_MESSAGES` table, and tests plant real-shaped failures to
assert it.

**The domain itself is never labelled malicious.** The pipeline analyses
claims *in* the page; it does not score the hostname (D-006, D-020).

---

## POST /api/investigations/image

Shorthand for `input_type = IMAGE`. **Implemented in Phase 13;
stores its result like the text endpoint.** The server validates
the upload, decodes the image locally, reads it with the
text-recognition engine (Tesseract), and runs the standard
pipeline over the recovered text.

**Request** — `multipart/form-data`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `file` | binary | yes | PNG, JPEG or WebP |
| `language` | string | no | form field, default `en` |

**Guards, in order.** Byte budget (10 MiB) → declared media type
(`image/png`, `image/jpeg`, `image/webp`) → decode (the image
library opens the bytes; the decoded format is the authority, so a
GIF wearing a PNG label is refused) → recognition (the engine,
with a 30-second timeout).

**Recognition budget.** Recovered text is cut at a 20,000-character
budget; the cut is recorded in `image_source.truncated` and
`image_source.truncated_at`, and surfaced as an `OCR_CONTENT_TRUNCATED`
limitation, so the pipeline never claims to have read text it did not
see.

**200 Response** — the standard `InvestigationResponse`, with
`input_type: "IMAGE"` and `image_source` describing the submission
and the recognition run:

```json
{
  "investigation_id": "inv_7a1c2e9f4b8d3e01",
  "status": "PARTIAL",
  "input_type": "IMAGE",
  "image_source": {
    "filename": "offer.png",
    "content_type": "image/png",
    "detected_content_type": "image/png",
    "format": "PNG",
    "byte_size": 9531,
    "width": 2400,
    "height": 320,
    "ocr_language": "eng",
    "text_recovered": true,
    "truncated": false,
    "truncated_at": null,
    "processed_at": "2026-10-03T00:00:04Z"
  },
  …the rest of the response, unchanged…
}
```

Every `image_source` field is either a fact about the submission, a
measurement of the decode, or a fact about the recognition run. None is
a judgement: `text_recovered` says the engine produced text, not that
the image is legitimate, and `detected_content_type` is what the bytes
parse as, which can differ from the declared `content_type`.

**Degradation, not failure.** An image that decodes but yields no
readable text returns `200` with `status: PARTIAL` and an
`OCR_TEXT_NOT_RETRIEVED` limitation. A wired graph whose engine is not
installed returns `200` with `status: PARTIAL` and an `OCR_UNAVAILABLE`
limitation and `text_recovered: false` — no text is fabricated (D-009).

**422** for caller faults: an oversized upload (`OCR_IMAGE_TOO_LARGE`),
a declared type this version does not read (`OCR_IMAGE_TYPE_UNSUPPORTED`),
and bytes that do not decode (`OCR_IMAGE_UNREADABLE`). **503**
`IMAGE_INPUT_UNAVAILABLE` when the graph is built without an OCR service
at all — a capability refusal, distinct from the `OCR_UNAVAILABLE`
*limitation* a wired graph reports when the engine is merely absent.

No `message` or `detail` ever carries a filesystem path, an engine
diagnostic or provider text; wording comes from the fixed
`OCR_MESSAGES` / `ENGINE_MESSAGES` tables, and tests plant real-shaped
failures to assert it.

---

## POST /api/investigations/upload — *not implemented*

PDF upload. **Phase 14.** The image half of this surface is
implemented as `POST /api/investigations/image` (Phase 13, above);
PDF remains refused with `422` `INPUT_TYPE_NOT_SUPPORTED` up front,
because there is no PDF ingestion path to be unavailable.

---

---

## GET /api/investigations/{id}

Retrieval of a stored investigation. **Implemented in Phase 9.**

The response body is `InvestigationResponse` — the same schema, built by the same
Phase 8 adapter, as `POST /api/investigations`. A retrieved investigation is
indistinguishable from the live one, and the round-trip test
(`tests/db/test_round_trip.py`) asserts that equality rather than leaving it to
review.

**Path parameter:** `id` — the `investigation_id` returned by the POST.

**200 Response:** `InvestigationResponse`.

**404 Response**

```json
{
  "error": {
    "code": "INVESTIGATION_NOT_FOUND",
    "message": "Investigation not found."
  }
}
```

The envelope carries no detail. A `404` that named the ids it checked would
confirm the existence of other investigations to anyone able to ask.

### The id is not unique

`investigation_id` is a digest of the submitted content, so re-running identical
content produces the same id by construction. `id` is therefore not unique, and
this endpoint returns the **most recently stored** run under it. Every run remains
visible in `GET /api/investigations`.

The consequence is stated rather than smoothed over: a client cannot fetch *that
specific second run* by id alone. It can see the run in the history. Adding a
second identifier for the client to learn would be a worse trade than a listing
endpoint (D-040).

### Enumerations

`status`: `COMPLETED` | `PARTIAL` | `FAILED`

The older `PENDING` | `PROCESSING` values are withdrawn with the `202` design
(D-036). A request that is still being processed is never visible over HTTP,
because Phase 8 answers only once the run has finished.

---

## GET /api/investigations

Paginated run history. **Implemented in Phase 9.**

Ordered by when each run was **stored**, not by when it started, with the
surrogate key as a tie-break. Two runs of the same content share a public id and
can start in the same millisecond; without a total order a client paging through
the history would see a row twice or skip one.

**Query parameters**

| Name | Type | Default | Notes |
| --- | --- | --- | --- |
| `limit` | int | 20 | 1..100 |
| `offset` | int | 0 | >= 0 |

`input_type` and `risk_level` filters appeared in the Phase 8 draft and are **not
implemented**. `input_type` needs no join; `risk_level` lives on
`risk_assessments`, so filtering on it would either join or denormalize it onto
`investigations`, and neither is worth it until something actually pages by
level.

**200 Response**

```json
{
  "total": 42,
  "limit": 20,
  "offset": 0,
  "investigations": [
    {
      "investigation_id": "inv_3f2b1c9e0a7d4e11",
      "status": "COMPLETED",
      "input_type": "TEXT",
      "current_stage": "risk",
      "started_at": "2026-10-01T00:00:00Z",
      "completed_at": "2026-10-01T00:00:04Z",
      "created_at": "2026-10-01T00:00:04Z",
      "claim_count": 5,
      "entity_count": 7,
      "red_flag_count": 6,
      "verification_count": 5,
      "source_count": 12,
      "evidence_count": 9,
      "factor_count": 6
    }
  ]
}
```

The envelope key is `investigations`, not the `items` of the Phase 8 draft.

### Counts are stored, not aggregated

The per-kind counts are columns on `investigations`, written at save time. Listing
a hundred runs therefore costs one indexed query instead of a thousand joins —
which is what keeps this endpoint a listing rather than a report.

The trade is that a count can be stale if a run is ever modified. Nothing modifies
a stored run: it is written once and read many times, so the trade costs nothing
today and would be revisited only by a re-run feature that rewrites history.

### What is deliberately absent

There is no `excerpt`. The Phase 8 draft included one, and truncating the
submitted text into every history response would copy user content into a listing
endpoint that a dashboard calls on every page — a wider blast radius for the same
data the detail endpoint already returns.

`completed_at` is currently `null`. Phase 7 does not populate it; that is a Phase 7
defect carried into Phase 9, not a persistence failure. It is stored faithfully as
`null` and returned as `null`.

---

### Search payload (Phase 3; exposed inside `evidence[].source`, not as a top-level field)

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
| `POST /api/investigations` | 8 (stores in 9) | Implemented |
| `POST /api/investigations/text` | 8 (stores in 9) | Implemented |
| `GET /api/investigations/limits` | 8 | Implemented |
| `GET /api/investigations/{id}` | 9 | Implemented |
| `GET /api/investigations` | 9 | Implemented |
| `POST /api/investigations/url` | 12 | Implemented |
| `POST /api/investigations/image` | 13 | Implemented |
| `POST /api/investigations/upload` | 13 / 14 | Planned |

Phase 8 built the two POST endpoints and answered them from memory. Phase 9 added
the store; both POSTs now persist what they return, and the response shape is
unchanged, so a client written against Phase 8 keeps working. Phase 12 added the
URL endpoint and the `url_source` field — additive only: a client that ignores
`url_source` and reads `input_type: "TEXT"` responses is unaffected.

The `risk_*` fields in the two response examples below are Phase 6 contracts:
`risk_score` is bounded by `risk_score_ceiling` (100), so a response can never
show a score above it, and `raw_score` carries the uncapped sum when the value was
clamped.

### Extraction payload (Phase 2; exposed as `claims` and `entities`)

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

### Verification payload (Phase 4; exposed as `verification_results`)

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

### Evidence payload (Phase 5; exposed as `evidence`)

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

### Risk payload (Phase 6; exposed as `risk_assessment`, verbatim)

`RiskService.assess()` produces a `RiskAssessment`. It becomes part of the
`GET /api/investigations/{id}` response in Phase 8; until then it is a library
contract only.

```json
{
  "risk_score": 45,
  "raw_score": 45,
  "risk_level": "MEDIUM",
  "factors": [
    {
      "id": "rsk_1f2e3d4c5b6a",
      "origin": "RED_FLAG",
      "factor_type": "FAKE_REGULATORY_CLAIM",
      "label": "Regulatory Claim Requiring Verification",
      "description": "A regulator's name is directly attached to an approval or registration claim.",
      "reason": "A regulator's name is directly attached to an approval or registration claim.",
      "source": "red flag rf_9f8e7d6c5b4a",
      "severity": "CRITICAL",
      "weight": 25,
      "contribution": 25,
      "absorbed_into": null,
      "claim_ids": ["claim_001"],
      "red_flag_ids": ["rf_9f8e7d6c5b4a"],
      "evidence_ids": ["ev_2c1f0a9b3d47"],
      "source_ids": ["src_2d84c639a686"],
      "verification_statuses": ["UNVERIFIED"],
      "is_uncertainty": false,
      "is_scoring": true,
      "is_evidence_backed": true
    },
    {
      "id": "rsk_7a8b9c0d1e2f",
      "origin": "VERIFICATION",
      "factor_type": "UNVERIFIED_CLAIM",
      "label": "Material Claim Not Confirmed by Any Searched Source",
      "description": "A search for a material claim completed without finding a source that establishes it. This is a gap in the public record, not a finding that the claim is false.",
      "reason": "Claim \"claim_001\" — UNVERIFIED. Phase 4 searched for this claim and no authoritative source was found that establishes it. No source disputes it either.",
      "source": "claim claim_001 (UNVERIFIED)",
      "severity": "MEDIUM",
      "weight": 8,
      "contribution": 0,
      "absorbed_into": "rsk_1f2e3d4c5b6a",
      "claim_ids": ["claim_001"],
      "red_flag_ids": [],
      "evidence_ids": ["ev_2c1f0a9b3d47"],
      "source_ids": ["src_2d84c639a686"],
      "verification_statuses": ["UNVERIFIED"],
      "is_uncertainty": true,
      "is_scoring": false,
      "is_evidence_backed": true
    }
  ],
  "relevant_claims": 1,
  "assessed_claims": 1,
  "evidence_coverage": 1.0,
  "analysis_completeness": 1.0,
  "weights": {
    "red_flag_weights": { "GUARANTEED_RETURN": 20, "FAKE_REGULATORY_CLAIM": 25, "...": 0 },
    "contradicted_claim": 25,
    "unverified_claim": 8,
    "insufficient_evidence": 0
  },
  "thresholds": { "medium_max": 20, "high_max": 50, "critical_max": 90, "ceiling": 100 },
  "warnings": [
    "At least one claim material to the investment could not be confirmed by any source that was searched.",
    "Some signals were detected by more than one stage of the analysis. Each is counted once; the factors that describe an already-counted signal are listed with a zero contribution.",
    "The risk score is a transparent heuristic indicator of documented risk factors. It is not a probability of fraud, of financial loss, or of the investment failing, and it is not a recommendation to invest or not invest."
  ],
  "assessed_at": "2026-10-02T00:00:00Z",
  "total_factors": 2,
  "scoring_factors": 1,
  "evidence_backed_factors": 1,
  "uncertainty_factors": 1,
  "deduplicated_factors": 1,
  "red_flag_factors": 1
}
```

Guarantees a client may rely on:

- `risk_score` is `min(raw_score, thresholds.ceiling)` — a **bounded heuristic
  indicator sum, never a probability** of fraud, of loss, or of the investment
  failing, and never a recommendation to invest or not invest. The last entry of
  `warnings` is always `SCORE_NOT_A_PROBABILITY`, appended by `RiskAssessment`
  validation so that no caller can omit it (D-025).
- The bands are product heuristics, not calibrated or empirically validated.
  Bounds are inclusive at the top of each range: `0..20` LOW, `21..50` MEDIUM,
  `51..90` HIGH, `91..100` CRITICAL.
- `raw_score` is the uncapped sum, retained so a client can show that the score
  was clamped rather than rescaled. Two documents far past the ceiling score
  identically.
- `factor_type` is a Phase 1 `RedFlagCode` or a Phase 4 `VerificationFactorType`.
  There is no risk-specific copy of the red-flag vocabulary, so the two cannot
  drift apart.
- `contribution` is never greater than `weight`, and a factor contributes at most
  once. Both are enforced at construction.
- `absorbed_into` is non-null exactly when `contribution` is `0` **because the
  signal was already counted**. De-duplicated factors are kept, not dropped, so
  the breakdown stays complete and a reader can see that a claim was
  contradicted even though it added nothing (D-026).
- `weights` and `thresholds` are snapshotted from the configuration in force, so
  a stored report can be audited after the configuration moves on (D-007).
- `evidence_coverage` and `analysis_completeness` are in `[0, 1]` and are
  transparency measures, **not** confidence or accuracy scores.
- `id` is `rsk_` + `sha256(origin, factor_type, cited ids)[:12]`, and
  `red_flag_ids` are `rf_` + `sha256(code, span start, span end, matched text)[:12]`.
  Both are derived, never minted, so a re-run is comparable to a stored report
  and `absorbed_into` points at something durable.
- `assessed_at` is the only wall-clock value and is excluded from every id.
- Absence of evidence never raises the score. `INSUFFICIENT_EVIDENCE` weighs `0`;
  `VERIFIED` and `NOT_APPLICABLE` produce no factor; and an `UNVERIFIED` result
  produces a factor only when its reason code says a search actually completed
  (D-027). `is_uncertainty` marks the factors that record something which could
  not be established, as distinct from something that was found.


## Enumerations

`evidence_type`: `REGULATORY_RECORD` | `GOVERNMENT_RECORD` | `EXCHANGE_RECORD` |
`OFFICIAL_ENTITY_SOURCE` | `SEARCH_RESULT`
`relationship`: `SUPPORTS` | `CONTRADICTS` | `IDENTITY_REFERENCE` | `CONTEXT` |
`MENTIONS`
`relevance`: `HIGH` | `MEDIUM` | `LOW`
`excerpt_origin`: `snippet` | `title`
`risk_level`: `LOW` | `MEDIUM` | `HIGH` | `CRITICAL`
`origin`: `RED_FLAG` | `VERIFICATION`
`factor_type` (verification): `CONTRADICTED_CLAIM` | `UNVERIFIED_CLAIM` |
`INSUFFICIENT_EVIDENCE`

---

## Phase 7 Note — The Graph Is a Library

Phase 7 added **no HTTP surface**. The graph is a library, callable from Python:

```python
from app.graph import run_investigation

state = run_investigation("Guaranteed 30% monthly returns, pay the fee today")
state["risk_assessment"].risk_score
state["warnings"]
state["timeline"]
```

It accepts a `GraphContext` so an API process injects real services once while
tests inject fakes. `run_investigation` remains the entry point the route calls;
Phase 8 wraps it rather than rewriting it.

What Phase 7 defined is the **result shape** the endpoint returns, and it is worth
reading `ARCHITECTURE.md` §2.3d and `app/graph/state.py` alongside this section:

- `warnings` and `errors` are structured objects with stable `code` values, so a
  client can branch on a limitation without parsing English. Phase 8 surfaces
  those codes verbatim as `limitations`.
- `timeline` is stage metadata for a future UI, and carries no explanation field.
  Phase 8 returns it as-is and reads `TimelineStatus` to derive the top-level
  `status` (D-037).
- `risk_assessment` is the Phase 6 object, including its standing caveat. It is a
  heuristic indicator count, never a probability (D-025), and the endpoint
  forwards it unchanged — a test asserts the response's risk keys are exactly the
  assessment's own.

---

## Phase 8 — What Is Actually Live

| Endpoint | Status |
| --- | --- |
| `GET /api/health` | implemented (Phase 0, database probe fixed in Phase 8) |
| `POST /api/investigations` | implemented — `TEXT` and `URL` (for `URL` the `text` field carries the address) |
| `POST /api/investigations/text` | implemented |
| `GET /api/investigations/limits` | implemented |
| `POST /api/investigations/url` | implemented (Phase 12) |
| `POST /api/investigations/image` | implemented (Phase 13) |
| `POST /api/investigations/upload` | **not implemented** — PDF only, `422` (Phase 14); the image half is `/investigations/image` above |
| `GET /api/investigations/{id}` | implemented (Phase 9) |
| `GET /api/investigations` | implemented (Phase 9) |

Fields deliberately **absent** from the Phase 8 response, and where each belongs:

| Field | Belongs to | Why not now |
| --- | --- | --- |
| `summary` | Phase 10 | A restatement the pipeline never makes, and the easiest place for a verdict to creep in (D-039) |
| `why_flagged` | Phase 10 | Would duplicate `RedFlag.description` with looser wording |
| `safety_guidance` | Phase 10 | Advice belongs to one place, with the surrounding non-advice disclaimer |
| `sources` (flattened) | Phase 5 / Phase 10 | Already reachable through `evidence[].source`; a second flat copy would drift |
| `risk_level` / `risk_score` (top level) | Phase 10 | Available inside `risk_assessment`; lifting it invites treating it as the headline verdict (D-025) |

OpenAPI is generated from the code and served at `/openapi.json`; `/docs` and
`/redoc` are the rendered contract. When this document and the generated schema
disagree, the schema is correct — it is generated from the same models the
handlers validate against.
