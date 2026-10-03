# CURRENT_STATE — InvestShield AI

> **This is the most important file for session recovery.** Read it first.
> Update it at the end of every meaningful work session.

---

## Current Project State

**Current Phase:** Phase 14 — PDF analysis — **COMPLETE**
**Current Subphase:** none — the planned roadmap is complete

> **Phase 10 naming, reconciled.** Two project documents disagreed about what
> Phase 10 is. `IMPLEMENTATION_PLAN.md` has always called it "Backend Test Suite";
> this file's phase table called it "Report generation", which is
> `AI_PIPELINE.md`'s **Stage 11** — a pipeline stage, on a different numbering axis,
> not a project phase. **Phase 10 is Testing & Quality Hardening.** Report
> generation is unstarted and remains later scope; it is deliberately *not* renumbered
> into a phase here, because inventing a number for it is exactly the kind of
> premature decision `DATABASE_SCHEMA.md` was careful to avoid when it declined to
> create the `reports` table.

**Last Completed Task:** Phase 14 — PDF analysis. A new `POST
/api/investigations/pdf` endpoint accepts a `multipart/form-data`
upload, validates it (size, declared type, and what the bytes actually
parse to), reads the text out of its pages locally with PyMuPDF, and
runs the existing pipeline over the extracted text. The backend gained
three new modules (`pdf_guards`, `pdf_service`, `schemas/pdf.py`), the
graph gained a PDF input path, persistence gained a `pdf_metadata`
column, and the frontend gained a working PDF input mode.

- **Real PDF validation** — the declared media type is only a first
  filter; the PDF library parses the bytes and `is_pdf` is the
  authority. A non-PDF wearing a PDF label is refused, as are bytes
  that do not parse at all.
- **Local extraction** — `pymupdf` opens the document in memory and
  reads the text out of the first `pdf_max_pages` (100) pages. The
  recovered text carries a character budget (20,000), and over-budget
  text is truncated with a recorded limitation; a document longer than
  the page limit is read to the limit and recorded as such.
- **Honest degradation** — a wired graph with no engine is not a failure:
  the run continues and answers `200 PARTIAL` with a `PDF_UNAVAILABLE`
  limitation and `text_recovered: false`. No text is ever fabricated. A
  PDF that parses but yields no readable text (a scan) is a real, if
  thin, result with a `PDF_TEXT_NOT_RETRIEVED` limitation.
- **The same pipeline, unchanged** — the graph's input node accepts PDF
  input, extracts it, and feeds the recovered text to extraction as
  untrusted data. Caller faults (oversized, wrong type, unparseable) are
  `422`; a disabled PDF capability is `503`.
- **Frontend PDF mode** — the PDF tab on `/investigate` is now a working
  file picker with client-side size and type checks; its availability is
  still read from `GET /api/investigations/limits`.
- **`pdf_source` on every response** — `null` for non-PDF runs; for PDF
  runs a frozen record of the submission and the extraction run: filename,
  declared and detected media types, format, byte size, page count, pages
  processed, whether text was recovered, truncation facts and the
  processing time. Every field is a measurement or a submission fact —
  none is a judgement about the document.
- **Verified end to end** — the full backend suite passes (**2966 passed,
  4 deselected, 0 failed**) and the frontend typechecks, lints and builds.

**Latest Commit:** `feat: implement PDF analysis` (Phase 14)
**Working Tree:** clean.

### Phase Status Summary

| Phase | Scope | Status |
| --- | --- | --- |
| Phase 0 | Project foundation | **COMPLETE** |
| Phase 1 | Red flag engine | **COMPLETE** |
| Phase 2 | Claim & entity extraction | **COMPLETE** |
| Phase 3 | External services & search infrastructure | **COMPLETE (search only; see limitations)** |
| Phase 4 | Verification agent | **COMPLETE** |
| Phase 5 | Evidence engine | **COMPLETE** |
| Phase 6 | Risk engine | **COMPLETE** |
| Phase 7 | LangGraph orchestration | **COMPLETE** |
| Phase 8 | API layer (FastAPI endpoints) | **COMPLETE** |
| Phase 9 | Database models & repositories | **COMPLETE** |
| Phase 10 | Testing & quality hardening | **COMPLETE** |
| Phase 11 | React frontend | **COMPLETE** |
| Phase 12 | URL analysis | **COMPLETE** |
| Phase 13 | Screenshot / OCR | **COMPLETE** |
| Phase 14 | PDF analysis | **COMPLETE** |

> Phase 10's scope is testing and hardening, per `IMPLEMENTATION_PLAN.md` ("Backend
> Test Suite") and as executed. Report generation — `AI_PIPELINE.md` Stage 11 — is
> **not** a numbered project phase and is not started.
>
> **Phase 14 completes the planned roadmap.** The PDF input
> surface is now fully operational end to end — the backend parses the
> document and the frontend offers a working PDF mode. Every input type
> the UI discloses is backed by a real endpoint.

### Phase 11 — React frontend

**Location:** `frontend/` — a Vite + React + TypeScript SPA. See `frontend/README.md` for
the full architecture, scripts and product rules.

| Route                | Page                  | Endpoints used                                  |
| -------------------- | --------------------- | ----------------------------------------------- |
| `/`                  | Landing               | none (static explanatory content only)           |
| `/dashboard`         | Dashboard             | `GET /api/health`, `GET /api/investigations`     |
| `/investigate`       | New investigation     | `GET /api/investigations/limits`, `POST /api/investigations/text` |
| `/investigation/:id` | Report                | `GET /api/investigations/{id}`                   |
| `/history`           | History               | `GET /api/investigations` (limit/offset)         |
| `*`                  | Not found             | none                                            |

**Backend contract, as implemented in Phase 11.** At the time, `GET
/api/investigations/limits` returned `supported_input_types: ["TEXT"]`,
and the OpenAPI document exposed **no** `/url` or `/image` endpoint.
The frontend therefore implemented only the endpoints that existed, which
is why the input-mode tabs were a planned-surface disclosure rather than a
working multi-upload form. **Phase 12 has since added
`POST /api/investigations/url`** and **Phase 13 has added
`POST /api/investigations/image`** and **Phase 14 has added
`POST /api/investigations/pdf`**, so the URL, Screenshot and PDF tabs
are all operational.

**Charting.** Recharts is used in exactly one place: risk contribution by severity, built
from `risk_assessment.factors[].contribution`. No chart is rendered where the API provides
no data — in particular the dashboard does **not** show a risk distribution, because
`InvestigationSummaryResponse` carries no risk level. Both the dashboard and the history
page say this in the interface rather than approximating it.

**Product invariants enforced in the UI** (see `frontend/README.md` for the full list):

- An `UNVERIFIED` or `INSUFFICIENT_EVIDENCE` claim is never presented as fraudulent.
- Absence of evidence is never presented as a contradiction.
- The risk caveat is fixed copy, shown verbatim, and never strengthened.
- Limitations and recorded stage failures appear above the findings, never hidden.
- Evidence links open with `rel="noopener noreferrer"` and `referrerPolicy="no-referrer"`;
  a non-`http(s)` URL renders as inert text.

**Security posture.** `VITE_API_BASE_URL` is the only environment variable read. A secret
scan across `frontend/` confirms no `GROQ_API_KEY`, `SERPAPI_KEY`, `DATABASE_URL`, token or
credential reference exists outside the `.env.example` comment warning against them.
`frontend/.env` is gitignored; `.env.example` is committed.

### Phase 11 verification

All checks below were executed against the **running** backend, not mocks.

| Check | Command | Result |
| --- | --- | --- |
| Type check | `npm run typecheck` | pass, no errors |
| Lint | `npm run lint` | pass, 0 errors / 0 warnings |
| Production build | `npm run build` | pass, 2233 modules, split into app / react / charts chunks |
| API client vs live API | `npm run verify:api` | **48 passed, 0 failed** |
| Live TEXT round-trip | `RUN_LIVE=1 npm run verify:flow` | **17 passed, 0 failed** |
| Component render (populated + empty) | `node scripts/verify-render.mjs …` | **26 passed, 0 failed** |
| All five routes served | `GET /` … `/history` on the dev server | 200, every module compiles |
| CORS preflight | `OPTIONS /api/investigations/text`, origin `localhost:5173` | `access-control-allow-origin: http://localhost:5173` |

`verify:api` covered the happy paths for all five endpoints plus the error paths:
`INPUT_EMPTY`, over-length text, `INVESTIGATION_NOT_FOUND`, an out-of-range `limit`, and a
simulated unreachable backend. `verify:flow` creates a real investigation through the same
client function the Investigate page calls, then reads it back and confirms it appears in
the history list with matching counts. (Phase 12 extended `verify:api` to 57 checks,
adding the limits discovery fields and the URL client's error paths — see
"Phase 12 verification" below.)

**Not verified in a real browser.** No automated browser was available in this environment,
so mouse-driven interaction, visual layout at each breakpoint and the dev-server HMR
experience were not exercised. Rendering correctness, the API contract and route serving
were verified as described above.

### Phase 12 — URL analysis

**Location:** `backend/app/services/url_guards.py`, `url_fetch.py`,
`website_extractor.py`, `backend/app/schemas/url.py`, the URL input path in
`backend/app/graph/nodes.py`, and `frontend/src/hooks/use-url-investigation.ts`.

| Concern | Where |
| --- | --- |
| URL request contract | `app/schemas/url.py` — `UrlInvestigationRequest` (url 1–2048 chars, optional language), frozen `UrlSource` and `WebsiteDocument` |
| SSRF guard | `app/services/url_guards.py` — allowlist-only address policy, obfuscated-IPv4 detection, scheme allow-list |
| Fetching | `app/services/url_fetch.py` — stdlib `http.client`, pinned connections, per-hop redirect re-validation, byte/redirect/timeout budgets |
| Text extraction | `app/services/website_extractor.py` — stdlib `HTMLParser`, drops scripts/styles/comments/hidden content |
| Graph input | `app/graph/nodes.py::_url_input` — fetches, records `url_source` on the state, feeds extracted text to extraction |
| API surface | `app/api/routes/investigations.py::create_url_investigation` — `POST /api/investigations/url` |
| Response record | `app/schemas/url.py::UrlSource` — 15 frozen fields describing the fetch; `WebsiteDocument` — extracted title/text/truncation |
| Fault taxonomy | `app/api/errors.py` — caller faults `422`, site faults `502`, disabled capability `503` |
| Persistence | `source_metadata` JSON column on `investigations`, written by the repository |
| Frontend | URL mode on `/investigate`, availability read from `GET /api/investigations/limits` |

**What it does.** The endpoint accepts `{ "url": "https://…", "language": "en" }`,
validates and guards the URL, fetches the page over a pinned connection, extracts
the visible text, and runs the *existing* pipeline (red flags → extraction →
verification → evidence → risk) over that text. The response is the standard
`InvestigationResponse` plus a `url_source` object describing what was fetched:
submitted URL, hostname, HTTP status, whether TLS was used, byte size, and the
truncation/timeout/content-type flags.

**Fault taxonomy — whose fault is it?**

| Fault | Status | Code |
| --- | --- | --- |
| Empty / over-length / invalid / non-http(s) URL, embedded credentials, control characters, unknown field | 422 | `URL_EMPTY`, `URL_INVALID`, `URL_TOO_LONG`, `URL_SCHEME_UNSUPPORTED`, `URL_HOST_MISSING`, `URL_HOST_TOO_LONG`, `URL_CREDENTIALS_NOT_ACCEPTED`, `URL_CONTROL_CHARACTERS_NOT_ACCEPTED` |
| SSRF-blocked destination (literal or resolving inward) | 422 | `URL_ADDRESS_BLOCKED`, `URL_METADATA_ADDRESS_BLOCKED`, `URL_METADATA_HOSTNAME_BLOCKED` |
| Site fault — DNS, connect, TLS, timeout, too many redirects, HTTP error, oversize or wrong content type | 502 | `URL_DNS_FAILED`, `URL_FETCH_FAILED`, `URL_TIMEOUT`, `URL_TOO_MANY_REDIRECTS`, `URL_HTTP_ERROR`, `URL_CONTENT_TOO_LARGE`, `URL_CONTENT_TYPE_UNSUPPORTED` |
| Fetch capability disabled by configuration | 503 | `URL_FETCH_DISABLED`, `URL_FETCH_UNAVAILABLE` |

No message ever contains a socket error, TLS detail, resolved address or
provider text — wording comes from the fixed `URL_MESSAGES` table, and tests
plant real-shaped failures and assert none of that detail reaches a response.

**Degradation, not failure.** A JavaScript-only page whose visible text is
empty still runs the pipeline; the run returns `200` with `PARTIAL` and a
`PAGE_TEXT_NOT_RETRIEVED` warning. Truncated content is recorded as a
limitation. An LLM-less deployment returns `PARTIAL` with the familiar
`EXTRACTION_PARTIAL` / `EXTRACTION_FALLBACK` limitations, exactly as the
text path does.

### Phase 12 verification

All checks below were executed against the **running** backend.

| Check | Command | Result |
| --- | --- | --- |
| Full backend suite | `cd backend && python -m pytest` | **2796 passed, 4 deselected, 0 failed** |
| URL fetch / guard unit tests | `pytest tests/test_url_fetch.py tests/test_url_guards.py tests/test_website_extractor.py tests/graph/test_graph_url_execution.py` | pass |
| SSRF redirects | parametrized over metadata / loopback / private / IPv6 literals and inward-resolving names | all refused with `URL_ADDRESS_BLOCKED` / `URL_METADATA_ADDRESS_BLOCKED` |
| URL investigation round-trip | `POST /api/investigations/url` then `GET /api/investigations/{id}` | `200`, `input_type=URL`, `url_source` persisted and returned |
| TEXT regression | `POST /api/investigations/text` (demo scam text) | unchanged: 3 red flags, full pipeline |
| SSRF via API | `127.0.0.1`, `169.254.169.254` | 422 `URL_ADDRESS_BLOCKED` / `URL_METADATA_ADDRESS_BLOCKED` |
| Scheme / HTTP faults via API | `ftp://…`, a 404 URL | 422 `URL_SCHEME_UNSUPPORTED` / 502 `URL_HTTP_ERROR` |
| OpenAPI contract | `/openapi.json` | `/api/investigations/url` present; `UrlInvestigationRequest`, `UrlSource` (14 properties), 422/502/503 responses |
| Frontend | `npm run typecheck` / `lint` / `build` | pass, pass, pass (2234 modules) |
| API client vs live API | `npm run verify:api` | **57 passed, 0 failed** (48 from Phase 11 + 9 URL checks) |

**Known limitations of the phase** (recorded, not hidden): the fetch depends
on network availability; JavaScript-rendered pages yield little or no text
(`PAGE_TEXT_NOT_RETRIEVED`); fetched content is truncated at the byte budget
and the truncation is recorded; no translation is performed
(`translation_enabled: false`); and in this deployment the LLM is
unavailable, so extraction runs in fallback mode and the run reports
`PARTIAL`.

### Phase 13 — Screenshot / OCR

**Location:** `backend/app/services/ocr_service.py`,
`ocr_guards.py`, `backend/app/schemas/ocr.py`, the IMAGE input path in
`backend/app/graph/nodes.py`, and `frontend/src/hooks/use-image-investigation.ts`.

| Concern | Where |
| --- | --- |
| Image request contract | `app/schemas/ocr.py` — `ImageUpload` (bytes, declared type, filename), frozen `ImageSource` and `ImageDocument` |
| Upload guards | `app/services/ocr_guards.py` — byte budget, declared-media-type allow-list, format map |
| Recognition | `app/services/ocr_service.py` — lazy Pillow decode, `pytesseract` with a native timeout and a character budget |
| Graph input | `app/graph/nodes.py::_image_input` — recognises, records `image_source` on the state, feeds recovered text to extraction |
| API surface | `app/api/routes/investigations.py::create_image_investigation` — `POST /api/investigations/image` (multipart) |
| Response record | `app/schemas/ocr.py::ImageSource` — the submission and OCR facts; `ImageDocument` — recovered text, truncation, limitation |
| Fault taxonomy | `app/api/errors.py` — caller faults `422`, disabled capability `503` |
| Persistence | `image_metadata` JSON column on `investigations`, written by the repository |
| Frontend | Screenshot mode on `/investigate`, availability read from `GET /api/investigations/limits` |

**What it does.** The endpoint accepts a `multipart/form-data` upload,
validates it, decodes the image locally, reads it with Tesseract OCR, and
runs the *existing* pipeline (red flags → extraction → verification →
evidence → risk) over the recovered text. The response is the standard
`InvestigationResponse` plus an `image_source` object describing what was
uploaded and read: filename, declared and detected media types, decoded
format, byte size, dimensions, OCR language, whether text was recovered,
truncation facts and the processing time.

**Fault taxonomy — whose fault is it?**

| Fault | Status | Code |
| --- | --- | --- |
| Oversized upload, non-image declared type, a format this version does not read, undecodable bytes | 422 | `OCR_IMAGE_TOO_LARGE`, `OCR_IMAGE_TYPE_UNSUPPORTED`, `OCR_IMAGE_UNREADABLE` |
| OCR capability disabled by configuration (no Tesseract) | 503 | `IMAGE_INPUT_UNAVAILABLE` (capability not wired); `OCR_UNAVAILABLE` is a *limitation*, not an error |

No message ever contains a filesystem path, a provider text or an engine
detail — wording comes from the fixed `OCR_MESSAGES` / `ENGINE_MESSAGES`
tables, and tests assert none of that detail reaches a response.

**Degradation, not failure.** A wired graph with no Tesseract is not a
failure: the run continues and answers `200 PARTIAL` with an
`OCR_UNAVAILABLE` limitation and `text_recovered: false`. An image that
decodes but yields no readable text returns `200 PARTIAL` with an
`OCR_TEXT_NOT_RETRIEVED` limitation. Truncated recovered text is recorded
as a limitation. An LLM-less deployment returns `PARTIAL` with the familiar
`EXTRACTION_PARTIAL` / `EXTRACTION_FALLBACK` limitations, exactly as the
text path does.

### Phase 13 verification

All checks below were executed against the **running** backend.

| Check | Command | Result |
| --- | --- | --- |
| Full backend suite | `cd backend && python -m pytest` | **2890 passed, 4 deselected, 0 failed** |
| OCR unit tests | `pytest tests/test_ocr_guards.py tests/test_ocr_service.py` | pass (47 tests) |
| Image endpoint tests | `pytest tests/api/test_image_investigation.py` | pass (14 tests) |
| OCR unavailable | wired graph, engine path that does not exist | `200 PARTIAL`, `OCR_UNAVAILABLE` limitation, `text_recovered: false`, no fabricated text, no path leakage |
| Image investigation round-trip | `POST /api/investigations/image` then `GET /api/investigations/{id}` | `200`, `input_type=IMAGE`, `image_source` persisted and returned |
| OCR text reaches the pipeline | screenshot of "Guaranteed returns" | `GUARANTEED_RETURN` red flag detected, matched the recovered text |
| TEXT regression | `POST /api/investigations/text` | unchanged: `input_type=TEXT`, `image_source: null` |
| URL regression | `POST /api/investigations/url` (loopback) | unchanged: 422 `URL_ADDRESS_BLOCKED` (SSRF guard intact) |
| Upload validation via API | GIF, oversized, garbage bytes, empty file | 422 `OCR_IMAGE_TYPE_UNSUPPORTED` / `OCR_IMAGE_TOO_LARGE` / `OCR_IMAGE_UNREADABLE` |
| OpenAPI contract | `/openapi.json` | `/api/investigations/image` present; `ImageSource` on the response; 422/503 responses |
| Frontend | `npm run typecheck` / `lint` / `build` | pass, pass, pass |
| API client vs live API | `npm run verify:api` | **71 passed, 0 failed** (57 from Phase 12 + 14 image checks) |
| Live E2E | generated screenshot → OCR → pipeline → persist | 19/19 checks passed |

**Known limitations of the phase** (recorded, not hidden): recognition
depends on the Tesseract binary being installed and resolvable; OCR accuracy
is the engine's, not this phase's — a misread label is a wrong input, not a
fabricated finding; recovered text is truncated at the character budget and
the truncation is recorded; a blank or text-free image yields no text
(`OCR_TEXT_NOT_RETRIEVED`); only PNG, JPEG and WebP are read (PDF is
Phase 14); and in this deployment the LLM is unavailable, so extraction
runs in fallback mode and the run reports `PARTIAL`.

### Phase 14 — PDF analysis

**Location:** `backend/app/services/pdf_service.py`,
`pdf_guards.py`, `backend/app/schemas/pdf.py`, the PDF input path in
`backend/app/graph/nodes.py`, and `frontend/src/hooks/use-pdf-investigation.ts`.

| Concern | Where |
| --- | --- |
| PDF request contract | `app/schemas/pdf.py` — `PdfUpload` (bytes, declared type, filename), frozen `PdfSource` and `PdfDocument` |
| Upload guards | `app/services/pdf_guards.py` — byte budget, declared-media-type allow-list (`application/pdf`) |
| Extraction | `app/services/pdf_service.py` — lazy `pymupdf` open, first `pdf_max_pages` pages, character budget |
| Graph input | `app/graph/nodes.py::_pdf_input` — extracts, records `pdf_source` on the state, feeds extracted text to extraction |
| API surface | `app/api/routes/investigations.py::investigate_pdf` — `POST /api/investigations/pdf` (multipart) |
| Response record | `app/schemas/pdf.py::PdfSource` — the submission and extraction facts; `PdfDocument` — recovered text, truncation, limitation |
| Fault taxonomy | `app/api/errors.py` — caller faults `422`, disabled capability `503` |
| Persistence | `pdf_metadata` JSON column on `investigations`, written by the repository |
| Frontend | PDF mode on `/investigate`, availability read from `GET /api/investigations/limits` |

**What it does.** The endpoint accepts a `multipart/form-data` upload,
validates it, parses the PDF locally with PyMuPDF, reads the text out of
its pages, and runs the *existing* pipeline (red flags → extraction →
verification → evidence → risk) over the extracted text. The response is
the standard `InvestigationResponse` plus a `pdf_source` object describing
what was uploaded and read: filename, declared and detected media types,
format, byte size, page count, pages processed, whether text was
recovered, truncation facts and the processing time.

**Fault taxonomy — whose fault is it?**

| Fault | Status | Code |
| --- | --- | --- |
| Oversized upload, non-PDF declared type, a format this version does not read, unparseable bytes, no file | 422 | `PDF_FILE_TOO_LARGE`, `PDF_TYPE_UNSUPPORTED`, `PDF_UNREADABLE`, `PDF_EMPTY` |
| PDF capability disabled by configuration (no PyMuPDF) | 503 | `PDF_INPUT_UNAVAILABLE` (capability not wired); `PDF_UNAVAILABLE` is a *limitation*, not an error |

No message ever contains a filesystem path, a provider text or an engine
detail — wording comes from the fixed `PDF_MESSAGES` / `ENGINE_MESSAGES`
tables, and tests assert none of that detail reaches a response.

**Degradation, not failure.** A wired graph with no PyMuPDF is not a
failure: the run continues and answers `200 PARTIAL` with a
`PDF_UNAVAILABLE` limitation and `text_recovered: false`. A PDF that
parses but yields no readable text (a scan) returns `200 PARTIAL` with a
`PDF_TEXT_NOT_RETRIEVED` limitation — the pipeline still runs over the
document's own facts. An engine that runs and fails on one document
returns `200 PARTIAL` with a `PDF_EXTRACTION_FAILED` limitation. Truncated
recovered text is recorded as a `PDF_CONTENT_TRUNCATED` limitation, and a
document read only to the page limit as a `PDF_PAGE_LIMIT_REACHED`
limitation.

### Phase 14 verification

All checks below were executed against the **running** backend.

| Check | Command | Result |
| --- | --- | --- |
| Full backend suite | `cd backend && python -m pytest` | **2966 passed, 4 deselected, 0 failed** |
| PDF guards | `pytest tests/test_pdf_guards.py` | pass |
| PDF service | `pytest tests/test_pdf_service.py` | pass |
| PDF endpoint tests | `pytest tests/api/test_pdf_investigation.py` | pass |
| PDF unavailable | wired graph, engine path that does not exist | `200 PARTIAL`, `PDF_UNAVAILABLE` limitation, `text_recovered: false`, no fabricated text, no path leakage |
| PDF investigation round-trip | `POST /api/investigations/pdf` then `GET /api/investigations/{id}` | `200`, `input_type=PDF`, `pdf_source` persisted and returned |
| TEXT regression | `POST /api/investigations/text` | unchanged: `input_type=TEXT`, `pdf_source: null` |
| URL regression | `POST /api/investigations/url` (loopback) | unchanged: 422 `URL_ADDRESS_BLOCKED` (SSRF guard intact) |
| Upload validation via API | non-PDF, oversized, garbage bytes, empty file | 422 `PDF_TYPE_UNSUPPORTED` / `PDF_FILE_TOO_LARGE` / `PDF_UNREADABLE` / `PDF_EMPTY` |
| OpenAPI contract | `/openapi.json` | `/api/investigations/pdf` present; `PdfSource` on the response; 422/503 responses |
| Frontend | `npm run typecheck` / `lint` / `build` | pass, pass, pass |

**Known limitations of the phase** (recorded, not hidden): extraction
depends on the PyMuPDF library being installed; only the first
`pdf_max_pages` (100) pages are read and the skip is recorded; recovered
text is truncated at the character budget and the truncation is recorded;
a text-free PDF (a scan) yields no text (`PDF_TEXT_NOT_RETRIEVED`); and
in this deployment the LLM is unavailable, so extraction runs in fallback
mode and the run reports `PARTIAL`.

### Files Recently Changed

**Phase 12 — URL analysis:**

```
backend/app/schemas/url.py                        (new — URL request/response contracts)
backend/app/services/url_guards.py                (new — SSRF allowlist policy)
backend/app/services/url_fetch.py                 (new — pinned-connection fetcher)
backend/app/services/website_extractor.py         (new — visible-text HTML extraction)
backend/tests/url_factories.py                    (new — shared URL test fakes)
backend/tests/test_url_guards.py                  (new)
backend/tests/test_url_fetch.py                   (new)
backend/tests/test_website_extractor.py           (new)
backend/tests/graph/test_graph_url_execution.py   (new)
backend/app/graph/nodes.py                        (+ URL input node, URL fault codes)
backend/app/graph/state.py                        (+ url_source)
backend/app/graph/context.py                      (+ url_fetch_service, website_extractor, supports_url)
backend/app/schemas/api.py                        (+ UrlInvestigationRequest, url_source on the response)
backend/app/api/routes/investigations.py          (+ POST /api/investigations/url)
backend/app/api/errors.py                         (+ URL error classes and code sets)
backend/app/api/adapters.py                       (serialize url_source)
backend/app/core/config.py                        (+ fetch settings)
backend/app/models/investigation.py               (+ source_metadata column)
backend/app/db/session.py                         (additive DDL for source_metadata)
backend/app/repositories/investigations.py        (persist url_source)
backend/app/prompts/extraction.py                 (extraction-v2 frames web text as untrusted data)
backend/tests/api/*.py, backend/tests/graph/*.py  (+ URL contract, error, safety, persistence and graph cases)
backend/tests/test_extraction_service.py, test_failure_injection.py  (+ URL cases)
frontend/src/hooks/use-url-investigation.ts       (new)
frontend/src/services/api-client.ts               (+ createUrlInvestigation)
frontend/src/types/api.ts                         (+ UrlInvestigationRequest)
frontend/src/pages/InvestigatePage.tsx            (URL mode)
frontend/scripts/verify-api-client.mjs            (+ URL wiring checks)
docs/*                                            (Phase 12 documentation)
```

**Phase 13 — Screenshot / OCR:**

```
backend/app/schemas/ocr.py                        (new — image upload/response contracts)
backend/app/services/ocr_guards.py                (new — upload size/type guards)
backend/app/services/ocr_service.py               (new — local Pillow decode + pytesseract OCR)
backend/tests/image_factories.py                  (new — shared image test builders)
backend/tests/test_ocr_guards.py                  (new)
backend/tests/test_ocr_service.py                 (new)
backend/tests/api/test_image_investigation.py     (new — endpoint contract, degradation, round-trip)
backend/app/graph/nodes.py                        (+ IMAGE input node, IMAGE fault codes)
backend/app/graph/state.py                        (+ image_source, upload)
backend/app/graph/context.py                      (+ ocr_service, supports_image)
backend/app/graph/investigation_graph.py          (+ upload parameter)
backend/app/schemas/api.py                        (+ ImageSource on the response)
backend/app/api/routes/investigations.py          (+ POST /api/investigations/image)
backend/app/api/errors.py                         (+ IMAGE error classes and code sets)
backend/app/api/adapters.py                       (serialize image_source)
backend/app/core/config.py                        (+ OCR settings)
backend/app/models/investigation.py               (+ image_metadata column)
backend/app/db/session.py                         (additive DDL for image_metadata)
backend/app/repositories/investigations.py        (persist image_source)
backend/tests/api/*.py, backend/tests/graph/*.py  (+ IMAGE contract, error, safety, persistence and graph cases)
frontend/src/hooks/use-image-investigation.ts     (new)
frontend/src/services/api-client.ts               (+ createImageInvestigation, multipart)
frontend/src/types/api.ts                         (+ ImageSource, url_source, image_source, image limits)
frontend/src/pages/InvestigatePage.tsx            (Screenshot mode file picker)
frontend/scripts/verify-api-client.mjs            (+ image wiring checks)
docs/*                                            (Phase 13 documentation)
```

**Phase 14 — PDF analysis:**

```
backend/app/schemas/pdf.py                          (new — PDF upload/response contracts)
backend/app/services/pdf_guards.py                  (new — upload size/type guards)
backend/app/services/pdf_service.py                 (new — lazy pymupdf parse + text extraction)
backend/tests/pdf_factories.py                      (new — shared PDF test builders)
backend/tests/test_pdf_guards.py                    (new)
backend/tests/test_pdf_service.py                   (new)
backend/tests/api/test_pdf_investigation.py         (new — endpoint contract, degradation, round-trip)
backend/app/graph/nodes.py                          (+ PDF input node, PDF fault codes)
backend/app/graph/state.py                          (+ pdf_source, pdf_upload)
backend/app/graph/context.py                        (+ pdf_service, supports_pdf)
backend/app/graph/investigation_graph.py            (+ pdf_upload parameter)
backend/app/schemas/api.py                          (+ PdfSource on the response)
backend/app/api/routes/investigations.py            (+ POST /api/investigations/pdf)
backend/app/api/errors.py                           (+ PDF error classes and code sets)
backend/app/api/adapters.py                         (serialize pdf_source)
backend/app/core/config.py                          (+ PDF settings)
backend/app/models/investigation.py                 (+ pdf_metadata column)
backend/app/db/session.py                           (additive DDL for pdf_metadata)
backend/app/repositories/investigations.py          (persist pdf_source)
backend/tests/api/*.py, backend/tests/graph/*.py    (+ PDF contract, error, safety, persistence and graph cases)
backend/tests/test_failure_injection.py             (+ the PDF failure matrix)
frontend/src/hooks/use-pdf-investigation.ts         (new)
frontend/src/services/api-client.ts                 (+ createPdfInvestigation, multipart)
frontend/src/types/api.ts                           (+ PdfSource, pdf_source, PDF limits)
frontend/src/pages/InvestigatePage.tsx              (PDF mode file picker)
docs/*                                              (Phase 14 documentation)
```

**Phase 10 — testing and quality hardening:**

```
backend/app/main.py                              (2 defect fixes: 422 handler and HTTP exception handler)
backend/tests/conftest.py                        (autouse network-guard fixture)
backend/tests/network_guard.py                   (new — the guard itself)
backend/tests/vocabulary.py                      (new — the judgement/advice matcher, shared)
backend/tests/contract_helpers.py                (new — response-walking helpers)
backend/tests/test_risk_safety.py                (machinery moved out to tests/vocabulary.py)
backend/tests/test_failure_injection.py          (new — deterministic failure matrix)
backend/tests/test_network_guard.py              (new — proves the guard is installed)
backend/tests/test_determinism.py                (new — id derivation and semantic reproducibility)
backend/tests/api/test_api_contract_inputs.py    (new)
backend/tests/api/test_openapi_contract.py       (new)
backend/tests/api/test_graph_integration.py      (new)
backend/tests/api/test_status_semantics.py       (new)
backend/tests/api/test_api_risk_safety.py        (new)
backend/tests/api/test_secret_leakage.py         (new)
backend/tests/api/test_determinism_http.py       (new)
backend/tests/api/test_retrieval_contract.py     (new)
backend/tests/api/test_query_efficiency.py       (new)
backend/tests/db/test_query_efficiency.py        (new)
```

**Phase 9 — persistence:**

```
backend/app/models/__init__.py                   (new — registers every table on Base.metadata)
backend/app/models/investigation.py              (new — 14 tables)
backend/app/db/types.py                          (new — UtcDateTime for SQLite/PostgreSQL parity)
backend/app/repositories/__init__.py             (new)
backend/app/repositories/investigations.py       (new — state <-> rows, history, delete)
backend/app/api/routes/investigations.py         (+ 2 GET endpoints, stores on write)
backend/app/api/deps.py                          (+ get_repository_dep)
backend/app/api/errors.py                        (+ InvestigationNotFound, 404)
backend/app/schemas/api.py                       (+ InvestigationSummaryResponse, InvestigationListResponse)
backend/app/db/session.py                        (get_db reads app.state.database, not the global)
backend/app/main.py                              (lifespan creates the schema, tolerating failure)
backend/tests/conftest.py                        (+ 6 persistence fixtures)
backend/tests/persistence_factories.py           (new — content and run helpers)
backend/tests/db/__init__.py                     (new)
backend/tests/db/test_round_trip.py              (new)
backend/tests/db/test_history.py                 (new)
backend/tests/db/test_schema.py                  (new)
backend/tests/api/test_persistence_endpoints.py  (new)
docs/DATABASE_SCHEMA.md                          (rewritten to match the implemented schema)
```

### The retrieval contract, stated correctly

An earlier version of this file said a retrieved investigation is **byte-identical**
to the live one. That was an overclaim, and it is corrected here because the
difference matters to anyone reasoning about what the contract protects.

**The guarantee is semantic equivalence.** Every substantive field — claims,
entities and their relationships, red flags with their spans, verification results,
evidence with its provenance, the risk assessment with its factors and its caveat,
warnings, errors, and the timeline's decisions — survives storage and comes back
equal. Both paths are shaped by the same Phase 8 adapter from the same domain
models, so there is no second rendering path that could disagree (D-041).

Three precisions:

1. **Timestamps are stored, not regenerated.** A `GET` returns the `started_at` the
   run actually recorded. So `POST` then `GET` currently yields byte-equal bodies —
   a *stronger* property than the contract requires. The contract does not depend on
   it: if a future phase regenerates a timestamp on read, retrieval is still correct
   and only the exact-equality test needs updating. Both forms are asserted, in
   `tests/api/test_retrieval_contract.py`.
2. **`completed_at` is `null`, always** — see Known Bugs 1.
3. **Two runs of identical content share an id and are not one retrievable body.**
   The public id is a content fingerprint; Phase 9's surrogate key is what lets both
   runs be stored. `GET` by that id returns the *newest* run, while both remain
   listed in history. "Identical content implies an identical response" is true for
   any single run and false as a statement about two runs.

### Tests Passing

```
cd backend && python -m pytest
2796 passed, 4 deselected
```

**Zero failures.** The four deselected are the `integration`-marked tests, which
need a real external service and are excluded by `pytest.ini`
(`addopts = -q --strict-markers -m "not integration"`).

Phase 10 closed at `2510 passed, 4 deselected`. Phase 11 changed no backend
test. Phase 12 added **286 tests** — 189 in four new modules
(`test_url_guards.py` 82, `test_url_fetch.py` 47, `test_website_extractor.py`
39, `graph/test_graph_url_execution.py` 21) and 97 across twelve existing
modules — and changed no existing assertion's meaning.

| Area | Collected | Module |
| --- | --- | --- |
| API input contract | 62 | `tests/api/test_api_contract_inputs.py` |
| OpenAPI contract | 46 | `tests/api/test_openapi_contract.py` |
| Graph wiring (AST + runtime) | 34 | `tests/api/test_graph_integration.py` |
| Status semantics | 36 | `tests/api/test_status_semantics.py` |
| Risk safety over the API | 34 | `tests/api/test_api_risk_safety.py` |
| Secret leakage | 33 | `tests/api/test_secret_leakage.py` |
| Failure injection | 58 | `tests/test_failure_injection.py` |
| Network guard | 24 | `tests/test_network_guard.py` |
| Determinism | 68 | `tests/test_determinism.py` (65) + `tests/api/test_determinism_http.py` (3) |
| Retrieval contract | 14 | `tests/api/test_retrieval_contract.py` |
| Query cost / N+1 | 16 | `tests/db/test_query_efficiency.py` (8) + `tests/api/test_query_efficiency.py` (8) |
| **Total** | **425** | |

Counts are what pytest collects, which is larger than the number of `def test_`
functions wherever a test is parametrised — the status-semantics and failure-injection
modules in particular earn much of their coverage from parameterisation over input
shapes and error codes.

### Defects found in Phase 10

Two were real bugs in production code. Both are fixed; the rest were Phase 10's own
test code and are noted only because they say something about the design.

1. **The 422 handler crashed on form-encoded bodies.** It assumed Pydantic's
   `input` value was JSON-serialisable. For a form body it is raw `bytes`, so
   serialising it raised `TypeError` *inside* the error handler, which fell through
   to a generic `500 INTERNAL_ERROR` — turning a clean `422` into an internal error,
   and doing so with a submitted credential in the payload. Fixed by
   `_validation_detail()` in `app/main.py`, which copies only `type`, `loc` and
   `msg` and so excludes submitted values by construction. Pinned by
   `test_a_credential_in_form_data_is_not_reflected`.
2. **No handler for `StarletteHTTPException`.** A `404` on an unknown path and a
   `405` on a wrong method bypassed the error envelope entirely and returned
   FastAPI's default body. Fixed with a handler mapping `404 → ROUTE_NOT_FOUND` and
   `405 → METHOD_NOT_ALLOWED` in the documented envelope.

Two more were found in Phase 10's *own* additions, which is worth recording because
each would have been invisible:

3. **The network guard was never installed.** `tests/network_guard.py` defined an
   `autouse` fixture, and `conftest.py` imported the module — but an `autouse`
   fixture is only collected from a file pytest treats as a conftest or a plugin, so
   importing the module registered nothing. The guard existed, was importable, and
   had never once blocked a connection. The suite was green either way, which is
   exactly why it went unnoticed. Fixed by defining the fixture in
   `tests/conftest.py`; `tests/test_network_guard.py` now asserts from inside a
   normal test that a connection is blocked, so this cannot recur silently.
4. **`allow_network(host)` did not work as documented for DNS.** An entry with no
   port was documented as permitting any port, but `_is_allowed` matched only an
   exact `(host, port)` pair, so a `getaddrinfo` call — which is always handed a
   concrete port — was still blocked. Fixed, and pinned.

### Network isolation

`tests/network_guard.py`, installed by an autouse fixture in `tests/conftest.py`,
wraps four entry points: `socket.socket.connect`, `socket.socket.connect_ex`,
`socket.create_connection` and `socket.getaddrinfo`. A blocked call raises
`NetworkAccessBlocked` naming the target and the remedy, so an offending test fails
loudly instead of quietly succeeding on a developer's real API key.

**The guarantee is that no traffic leaves the machine.** Two exemptions, both
deliberate and both pinned by tests so neither can widen quietly:

- **Loopback literals.** `TestClient(app)` used as a context manager starts an anyio
  blocking portal, and Windows has no `socketpair`, so CPython emulates it with a
  real TCP connection to `127.0.0.1`. Without this exemption the guard breaks every
  API test on Windows for a reason unrelated to isolation. Only literals are exempt —
  the name `localhost` still has to resolve, and resolution is still blocked, so
  nothing hides behind it. A hostname beginning with `127.` is *not* exempt, which a
  test pins because the obvious prefix check would allow
  `127.0.0.1.example.invalid`.
- **Tests marked `@pytest.mark.integration`**, which are deselected by default
  anyway. No live service is required to run the suite.

**One library bypasses the guard entirely.** `psycopg2` connects through libpq,
which does its own DNS and TCP in C and never enters Python's `socket` module. A
test pointed at a real PostgreSQL host would therefore make a live connection that
the guard cannot see. This is why
`tests/api/test_secret_leakage.py` points its poison DSN at loopback port 1 —
refused by the kernel in milliseconds — rather than at a public host name, where it
would have sat in a real connect timeout. No test in the suite should point a
database at a routable address.

### Tests Failing

None.

### Known Bugs

1. **Phase 7 declares `InvestigationState.completed_at` and no node ever writes
   it.** The key is *absent* from the state and the Phase 8 adapter renders it as
   `null`; the Phase 8 response exposes the field and Phase 9 persists it, so it is
   stored and returned faithfully — but it is always `null`. This is a Phase 7 gap,
   deliberately not fixed in Phase 9 or Phase 10: inventing a finish time would be a
   claim the pipeline never made. Pinned by
   `test_run_timings_are_preserved_exactly_as_recorded`,
   `test_completed_at_is_null_because_it_is_never_stamped` and
   `test_completed_at_is_still_null_after_a_round_trip`.
2. **Phase 4 discards the search responses Phase 5 needs.** Phase 7 works around
   this with `RecordingSearchService` rather than editing Phase 4 (D-031). The
   clean fix is for `VerificationService` to return its responses, which should be
   done if Phase 4 is ever reopened.
3. **Phase 1's `SUSPICIOUS_URL` description asserts the word "fraudulent".**
   Known, pinned by `TestPhaseOneCatalogueTripwire`, and a Phase 1 fix.
4. **The network guard cannot see `psycopg2` connections.** See "Network isolation"
   above. Not a defect in the guard's design — a Python-level guard cannot intercept
   a C-level connect — but it is a real limit on what "the suite is offline" means.

> The Phase 7 revision's Known Bug 1 — `/api/health` ignoring injected settings —
> was **fixed in Phase 8** and is now covered by
> `tests/api/test_health_boundary.py`.

> `/api/health` reports the **names** of the credential environment variables it
> looks for — `"GROQ_API_KEY is not set."` — and is unauthenticated. No value is
> ever exposed, and the `configured` boolean a legitimate client reads is
> unaffected, but on a public deployment the wording is reconnaissance. Recorded in
> Phase 10 rather than changed, because it is Phase 8 behaviour, the strings are
> arguably useful to an operator, and no credential leaks. Pinned by
> `TestHealthReportsConfigurationWithoutDisclosingIt`.

### Blocked Items

None.

### Available Services

| Service | Status | Evidence |
| --- | --- | --- |
| `GROQ_API_KEY` | present but **not working** | a local `.env` supplies one; the LLM returns an error status and Phase 2 degrades to deterministic patterns, which it reports honestly |
| `SERPAPI_KEY` | **absent** | `SearchService.available = false`; verification degrades to `SEARCH_UNAVAILABLE` |
| SQLite | working | tests use per-test temporary files; `PRAGMA foreign_keys=ON` is set per connection |
| PostgreSQL | **not installed** | DDL is verified by compiling every table and index against the PostgreSQL dialect in `tests/db/test_schema.py`, but no live PostgreSQL run has been made |
| `httpx` | installed | used by `LLMService`, `SerpAPIProvider` and `UrlFetchService` |
| `langgraph` | **installed, 1.2.12** | `requirements.txt`; verified on CPython 3.14 |
| Tesseract binary | present, not on `PATH` | `resolve_tesseract_cmd()` finds it; OCR is verified working end to end |
| `pytesseract` 0.3.13 / `Pillow` 12.2.0 | installed | the image modality's decode + OCR; `python-multipart` is installed too |
| PyMuPDF / `sentence-transformers` | **not installed** | PyMuPDF is Phase 14; local embeddings were deferred (search is API-only) |

> A repository-root `.env` exists (gitignored, contains real credentials). It is
> **not** committed. `tests/graph/graph_factories.py`, `tests/api/conftest.py` and
> `tests/conftest.py` all pass `_env_file=None` so the suites stay offline
> regardless of what it contains — and as of Phase 10 that isolation is enforced
> rather than merely intended: see "Network isolation" above.
>
> A credential was committed in an earlier state of this repository and is still in
> Git history. Rotating it is an outstanding operational task. Phase 10 neither
> rewrote history nor read any `.env` value.

### Phase 9 Architecture

```
POST /api/investigations/text ─┐
                                ├─► run_investigation ─► raise_for_graph_errors ─► repo.save() + commit
POST /api/investigations ─────┘                                                          │
                                                                                          ▼
                                                          14 tables, one transaction
                                                                                          │
GET /api/investigations/{id} ──► repo.load() ──► InvestigationState ─┐                    │
GET /api/investigations ──────► repo.list_page() ──► summaries (1 table)                   │
                                                                                          ▼
                                                        serialize_investigation() ──► 200
```

| Concern | Where |
| --- | --- |
| Table definitions | `app/models/investigation.py`, registered by `app/models/__init__.py` |
| State ⇄ rows mapping | `app/repositories/investigations.py` |
| Datetime portability | `app/db/types.py::UtcDateTime` — naive UTC in storage, aware UTC out |
| Session lifecycle | `get_db(request)` reads `app.state.database`; the route commits, never the dependency |
| Schema creation | `lifespan` calls `database.create_all()`, logging and continuing on failure |
| Response shaping | **unchanged** — `app/api/adapters.py::serialize_investigation` serves both paths |
| 404 on a missing id | `app/api/errors.py::InvestigationNotFound` |

### Phase 9 Design Decisions

| Decision | Rationale |
| --- | --- |
| The repository returns an `InvestigationState`, not a bespoke result type | Both the write and read paths then feed the same Phase 8 adapter. A `GET` that disagreed with the `POST` that stored the run would be worse than no retrieval (D-039) |
| `id` is the primary key; `public_id` is **not** unique | `investigation_id_for` digests the submitted content, so a unique constraint would make re-running content an error and would destroy the evidence that it was run twice (D-040) |
| Retrieval returns the newest run under a shared id | A client posts and then retrieves with the id it was just handed; returning the older run would show it a result disagreeing with the one it was just given |
| `red_flags` unique on `(investigation_id, code, span_start, span_end)` | Phase 1 emits one flag per span with its own `rf_` id and Phase 6 cites those ids. The sketch's "one row per indicator" would destroy findings the risk trace points at |
| `sources` keyed on `(investigation_id, source_id, result_id)` | Phase 5 derives `ev_` ids from both. Keying on `source_id` alone would force one retrieval to borrow another's `result_id` (D-023) |
| No `caveat` column on `risk_assessments` | The caveat is `SCORE_NOT_A_PROBABILITY`, which `RiskAssessment` appends to `warnings` during validation. A second column would be a copy free to drift from the one the model emits (D-025) |
| `investigation_warnings` has no `error_type` | A warning is something a user may read; Phase 8 already established that the log-side diagnostic does not cross that boundary |
| `investigation_errors` stores `error_type` but never a message | An error diagnoses our own defect and a class name is not a secret; driver text routinely embeds a DSN (D-024) |
| No `users` and no `reports` tables | `reports` is Phase 10; an empty placeholder table looks decided when nothing has been asked to design it |
| Seven denormalised counts on `investigations` | Listing a hundred runs is one indexed query instead of a thousand joins. Pinned by a test so a count cannot drift from its children |
| `sequence` on every ordered collection | Phase 5's ordering is a function the database cannot reproduce; inferring it would list a retrieved run's findings in a different order with nothing to detect it |
| `create_all()` at startup, failures tolerated | A database that is reachable but not writable must not stop the read-only endpoints. `create_all` is idempotent, so this is safe on every boot |
| A storage failure is a `500`, not a swallowed error | Returning `200` while discarding the result would tell a client its investigation is safe to look up later when it is not |
| The Phase 8 response shape is unchanged | A client written against Phase 8 keeps working. No `persisted` flag: the id was already there and is what the client uses |

### Known Limitations (Phase 10)

Carried forward from Phase 9, unchanged:

- **No authentication, therefore no ownership check.** Anyone holding an id can
  read that investigation. Acceptable only because there is nothing to
  authenticate against; it must be revisited before there is.
- **No `DELETE` endpoint.** The repository exposes `delete()`, which removes every
  run under a public id, but no route calls it.
- **No migrations.** `create_all()` creates missing tables and will **not** add a
  column to an existing one. Fine while the schema is still changing and the
  database is disposable; Alembic is the right tool the first time a deployed
  database has to be migrated in place. Recorded in `docs/DATABASE_SCHEMA.md`
  rather than glossed over.
- **No live PostgreSQL run.** DDL compiles against the PostgreSQL dialect and
  `UtcDateTime` handles the offset asymmetry, but no test has connected to a real
  PostgreSQL server. `SERPAPI_KEY` is absent and there is no database URL for one.
- **`limit`/`offset` paging only.** Fine at this scale; offset paging would need
  replacing for a table large enough for it to hurt.
- **`limitations` is still a flat code array** with no per-claim attribution, so a
  limitation cannot be joined back to the claim it affected.
- **`completed_at` is always `NULL`** — see Known Bugs 1.
- **URL fetching depends on the network, yields no text from
  JavaScript-only pages, and truncates over-budget content — each
  recorded as a limitation, never hidden.** All four input kinds
  (`TEXT`, `URL`, `IMAGE`, `PDF`) are analysed; the gap that once
  refused `IMAGE` and `PDF` with `422` was closed by Phases 12–14.

Identified in Phase 10, recorded rather than fixed, because fixing any of them means
redesigning something Phase 10 was explicitly not scoped to redesign:

- **`GET /api/investigations/limits` is typed `dict[str, object]`,** so its OpenAPI
  schema is an inline `additionalProperties: true`. A client gets no machine-readable
  shape for the four fields it returns. The response is correct and the endpoint is
  documented; only the schema is loose. Pinned as a known limitation by
  `tests/api/test_openapi_contract.py` rather than fixed, because the alternative is
  introducing a response model to satisfy a schema generator, and a hand-written
  model for four fields is a thing to add when a client needs it.
- **Graph nodes do not validate every malformed dependency return.** A dependency
  that breaks its contract can escape as a raw `AttributeError` or `TypeError`
  rather than the stage's typed failure code — a malformed `RiskAssessment` from
  Phase 6, for instance, does not reliably become `RISK_ASSESSMENT_FAILED`. The
  existing coverage asserts that *an* error is recorded and that the run does not
  score, which is the safety-relevant part; what is missing is the specific error
  *code*. Fixing this means validating every node's inputs, which is a Phase 7
  change, not a testing change. Pinned by
  `TestRiskFailures::test_malformed_dependency_data_does_not_reach_a_score`.
- **`psycopg2` bypasses the network guard** — see Known Bugs 4.
- **`/api/health` names credential variables** — see the note under Known Bugs.
- **No coverage percentage was produced.** `pytest-cov` is not installed, and it was
  not added for this phase: a number generated by a tool the project does not
  otherwise use would be a metric with no process behind it. The 425 tests added are
  organised by concern and each module states what property it defends, which is the
  more useful artefact. `ruff`, `mypy` and `hypothesis` are likewise absent and were
  not added.

### Security Posture (Phase 10)

The suite plants a real-shaped secret at every place one could escape and proves it
does not: a `postgresql://user:password@...` DSN in an unreachable database, a
provider key in a search failure, a `GROQ_API_KEY` in an extraction exception, a
DSN quoted inside a provider error, and credential-shaped values in an unknown
field, a wrong-type field, a form body, malformed JSON, a query parameter and a URL
path. No response may contain a traceback, a stack trace, SQL, a DSN, a password, an
API key or a filesystem path.

The vocabulary ban is checked end to end too — graph, adapter, JSON, database, `GET`
response, history entry, error body and the OpenAPI document — with the negation
handling Phase 6 built, so the disclaimers that *name* fraud to rule it out are not
violations. One narrow, documented exemption exists: the product tagline in the
OpenAPI description, because a description of this tool cannot avoid naming it.

**Historical credential exposure.** A credential was committed in an earlier state
of this repository and remains in Git history. It is **not** addressed in Phase 10:
rotating it is an operational action, and rewriting history is explicitly out of
scope. It remains an outstanding task for whoever holds the affected accounts. The
current tree is clean — `.env` is gitignored, every settings factory passes
`_env_file=None`, and no `.env` value is read, printed or asserted on anywhere.

### Environment Reality Check (verified)

| Capability | Status | Evidence |
| --- | --- | --- |
| `langgraph` | **1.2.12, installed** | `python -m pip show langgraph`; graph compiles and runs |
| `sqlalchemy` | **installed** | 14 tables create on SQLite; all DDL compiles for PostgreSQL |
| `GROQ_API_KEY` | present but rejected by the API | extraction falls back, reports `EXTRACTION_FALLBACK` |
| `SERPAPI_KEY` | **absent** | `SearchService.available = false` |
| Tesseract binary | present, not on `PATH` | `resolve_tesseract_cmd()` |
| `pytesseract` / `Pillow` / PyMuPDF | not installed | deferred |
| `sentence-transformers` | not installed | deferred |
| Repository-root `.env` | **exists, gitignored** | holds real credentials; no longer breaks the suite |
| `ruff` / `mypy` / `pytest-cov` / `hypothesis` | **not installed, not configured** | this project has no linter, typechecker or coverage step; Phase 10 deliberately did not add them — see Known Limitations |

### Important Decisions

Full rationale lives in [`DECISIONS.md`](DECISIONS.md). The ones that most affect
the next phase:

| ID | Decision |
| --- | --- |
| D-006 | Absence of evidence is never an accusation |
| D-009 | Every external service degrades into a typed error code surfaced as a Limitation |
| D-020 | Verification reports a claim's factual status, never a verdict |
| D-023 | Evidence never re-decides; it cannot make a finding easier |
| D-025 | The risk score is a heuristic indicator sum, never a probability |
| D-030 | The graph is the orchestrator and owns no business logic |
| D-032 | A recorded error ends the run; a recorded limitation does not |
| D-034 | Determinism is asserted on the semantic view, not on the clock |
| D-036 | Phase 8 returns the whole investigation synchronously; no job handle |
| D-037 | Run status is derived from the stage timeline, never from the warning list |
| D-038 | The API embeds domain models rather than re-projecting them |
| D-039 | The API layer interprets; it never computes a score, threshold or verdict |
| D-040 | `public_id` is a content fingerprint, so a surrogate key owns identity |
| D-041 | Persistence returns an `InvestigationState`; the adapter shapes both paths |
| D-042 | Order is stored, never inferred from a value the database already stores |
| D-043 | Registry match is a tier-1, unanimous check |
| D-044 | The risk caveat is reproduced by the model, not stored in its own column |
| D-045 | Network isolation is a guard, not a convention; the guarantee is "nothing leaves the machine" |
| D-046 | The safety vocabulary is enforced at every boundary text crosses, not only in the engine |
| D-047 | Leak-safety is proven by planting the secret, never by reading the handler |
| D-048 | Query cost is asserted as flatness in the data, not as a magic number |
| D-049 | `completed_at` stays `null`; the pipeline will not invent a time it never measured |
| D-050 | Input-mode availability is read from the API, not hardcoded in the frontend |
| D-054 | URL analysis fetches with the stdlib, guards with an allowlist, and splits faults 422/502/503 by whose mistake they are |
| D-055 | Fetched page text is untrusted data, never trusted as text |

---

## Next Exact Task

**Phase 13 — Screenshot / OCR**, per `IMPLEMENTATION_PLAN.md`.

1. Image upload → OCR → text → the standard pipeline, reusing the
   Phase 12 input-node seam (`supports_url` generalises to any
   extracted-text input).
2. `OCR_UNAVAILABLE` when Tesseract is absent — the pipeline
   continues and records the limitation, exactly as URL fetching
   degrades.
3. File size / type validation, `multipart/form-data`, `413` on
   oversize, `422` on a disallowed type.
4. The frontend's Screenshot tab is already disclosed and disabled;
   enable it when `POST /api/investigations/upload` exists.

**Phase 12 left the following open,** in priority order:

- JavaScript-rendered pages yield little or no text
  (`PAGE_TEXT_NOT_RETRIEVED`); a headless-browser fetch is the
  eventual remedy and is out of scope for Phase 12.
- `GET /api/investigations/limits` still has no machine-readable
  OpenAPI schema (carried from Phase 10).
- `completed_at` is still always `NULL` (a Phase 7 gap, carried).

### Report generation

Not a numbered phase, and not started. It is `AI_PIPELINE.md` **Stage 11**, and it
remains later scope. The five requirements Phase 9's `CURRENT_STATE` recorded for it
still stand unchanged:

1. Build the report layer over a *stored* investigation. `repo.load()` returning an
   `InvestigationState` is the input it needs, and it is the first consumer that
   reads an investigation some time after the run that produced it.
2. A report renders evidence and indicators. It must not introduce a probability, a
   verdict or advice, and it must not restate `risk_score` as a likelihood (D-025).
   The `RiskAssessment` caveat has to survive into the rendered text.
3. Decide where reports live. `docs/DATABASE_SCHEMA.md` sketches a `reports` table;
   it was deliberately **not** created in Phase 9, so this is an open decision.
4. Rendering is multilingual in intent — `Language` accepts `en`/`hi`/`mr` and the
   value is already stored on the run — but a report should ship English only and
   record the requested language, as Phase 8 did.
5. A report that cites a risk factor must walk to the claim, red flag and source
   behind it. Phase 9 stored that trace, so this is a read, not a reconstruction.

> The safety vocabulary Phase 10 now enforces end to end is the constraint a report
> will have to satisfy. `tests/api/test_api_risk_safety.py` is the executable
> statement of it, and a report's rendered text should meet the same bar.

### Do not start before Phase 12 is green

- No authentication, no OCR/PDF ingestion, no report generation.

---

## Recovery Checklist

If you are reading this in a fresh session:

1. [x] Read `docs/CURRENT_STATE.md` (this file)
2. [ ] Read `docs/IMPLEMENTATION_PLAN.md`
3. [ ] Read `docs/ARCHITECTURE.md` (§2.3d for the graph, §2.3e for the API,
       §2.3f for persistence, §2.3g for the test architecture, §2.3h for
       URL ingestion) and `docs/DECISIONS.md` (D-030…D-055)
4. [ ] Read `docs/DATABASE_SCHEMA.md` and the last entry in `docs/DEVELOPMENT_LOG.md`
5. [ ] Run `git status` and `git log --oneline -5`
6. [ ] Run `cd backend && python -m pytest` — expect **2796 passed, 4 deselected,
       0 failed**
7. [ ] Execute **Next Exact Task**