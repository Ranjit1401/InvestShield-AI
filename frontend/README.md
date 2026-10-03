# InvestShield AI — Frontend

React + TypeScript frontend for the InvestShield AI investor-safety investigation
platform.

> **Investigate Before You Invest.**
>
> InvestShield does not just detect suspicious investment content. It investigates the claims
> behind it and shows the evidence.

This app is a read-and-submit client for the Phase 0–15 FastAPI backend. It renders what the
API returns and never invents findings, counts, sources or evidence.

## Requirements

- Node.js 20 or newer
- The backend running and reachable (see **Configuration**)

## Getting started

```bash
npm install
cp .env.example .env.local     # optional; the default already points at localhost
npm run dev
```

The dev server runs on **port 5173**. That port is named explicitly in the backend's CORS
allow-list, so the browser can call the API cross-origin. `strictPort` is on: if 5173 is
occupied the server fails with a clear error instead of silently binding another port, which
would leave every API call blocked by CORS.

## Configuration

| Variable            | Purpose                                  | Default                 |
| ------------------- | ---------------------------------------- | ----------------------- |
| `VITE_API_BASE_URL` | Base URL of the InvestShield API         | `http://127.0.0.1:8000` |

`VITE_API_BASE_URL` is the only environment variable the app reads. Every `VITE_*` value is
inlined into the public JavaScript bundle, so **no secret may ever be placed in a frontend
environment file**. The frontend never receives `GROQ_API_KEY`, `SERPAPI_KEY`, `DATABASE_URL`
or any other backend credential.

## Routes

| Route                 | Page                      | Data source                          |
| --------------------- | ------------------------- | ------------------------------------ |
| `/`                   | Landing                   | none (static content only)           |
| `/dashboard`          | Dashboard                 | `GET /api/health`, `GET /api/investigations` |
| `/investigate`        | New investigation         | `GET /api/investigations/limits`, `POST /api/investigations/text`, `POST /api/investigations/url`, `POST /api/investigations/image`, `POST /api/investigations/pdf` |
| `/investigation/:id`  | Investigation report      | `GET /api/investigations/{id}`       |
| `/history`            | History                   | `GET /api/investigations`            |

Any other path renders a not-found page.

## Input support

The input-mode selector on `/investigate` shows all four planned
modes, and the availability of each is driven by
`GET /api/investigations/limits` rather than hardcoded:

| Mode       | Status     | Backend phase |
| ---------- | ---------- | ------------- |
| Text       | Available  | —             |
| URL        | Available  | 12            |
| Screenshot | Available  | 13            |
| PDF        | Available  | 14            |

The URL mode submits through `createUrlInvestigation`
(`POST /api/investigations/url`), the Screenshot mode through
`createImageInvestigation` and the PDF mode through
`createPdfInvestigation` — both `multipart/form-data` uploads to
`POST /api/investigations/image` and `POST /api/investigations/pdf` —
and each mode holds an in-flight guard against double submission like the
text mode, and surfaces the API's error envelope verbatim
(`URL_ADDRESS_BLOCKED`, `OCR_IMAGE_UNREADABLE`, `PDF_UNREADABLE`, …).

## Architecture

```
src/
├── components/
│   ├── ui/             design-system primitives (button, card, badge, alert, skeleton…)
│   ├── layout/         app shell, navigation, page container
│   ├── common/         cross-cutting blocks: status badges, loading/error/empty states
│   ├── investigation/  header, claims, entities, red flags, timeline, limitations, list
│   ├── evidence/       claim → evidence → source panels
│   └── risk/           risk score, factor breakdown chart, factor list
├── hooks/              use-async-resource, use-investigations, use-text-investigation,
│                       use-url-investigation, use-image-investigation,
│                       use-pdf-investigation
├── lib/                class-name helper, display formatting, colour intent
├── pages/              one file per route
├── services/           api-client.ts — the only module that calls fetch
├── types/api.ts        TypeScript mirror of the backend contract
└── index.css           Tailwind v4 theme
```

### Data flow

Components never call `fetch`. `src/services/api-client.ts` is the single boundary and owns:

- the base URL, read only from `VITE_API_BASE_URL`
- the documented `{ "error": { code, message, detail } }` envelope
- typed failures via `ApiError`, with `kind` distinguishing `http`, `network`, `timeout` and
  `malformed`
- request timeouts and abort handling

No `any` appears anywhere in `src/`. The types in `src/types/api.ts` were taken from the
running FastAPI application (`app.openapi()` and the enums in `app/schemas/*`), not from
prose. If a backend enum changes, change it there.

## Product rules the UI enforces

These are invariants, not preferences, and the components are written to keep them:

- **An unverified claim is not a fraudulent claim.** `UNVERIFIED` and `INSUFFICIENT_EVIDENCE`
  are rendered in neutral/warning tones with plain wording.
- **Absence of evidence is not a contradiction.** A claim with no evidence says so explicitly.
- **The risk caveat is fixed copy.** "Risk score is a transparent heuristic indicator and is not
  a probability of fraud or financial loss." is shown verbatim on every risk panel and is never
  reworded into a stronger claim.
- **Limitations are never hidden.** Recorded warnings, their stage and any stage failure are
  surfaced above the findings, not collapsed behind a disclosure.
- **Dynamic data comes from the API.** Counters, risk levels, evidence, sources and charts are
  rendered only from response fields. Where the API does not provide something the page says so
  explicitly instead of approximating it.
- **External links are hardened.** Evidence links open with `rel="noopener noreferrer"` and
  `referrerPolicy="no-referrer"`, and a non-`http(s)` URL is rendered as inert text.

## Accessibility

Semantic landmarks and headings, a skip-to-content link as the first focusable element, a
visible focus ring on every interactive element, `aria-label` on icon-only buttons, labelled
form controls, live regions for submission status, and `role="meter"`/`role="status"` where
appropriate. Colour is never the sole carrier of meaning: every status also carries text.
Animation is disabled under `prefers-reduced-motion`.

## Scripts

| Command                | What it does                                       |
| ---------------------- | -------------------------------------------------- |
| `npm run dev`          | Dev server on port 5173                            |
| `npm run build`        | Typecheck, then production build to `dist/`        |
| `npm run preview`      | Serve the production build                         |
| `npm run typecheck`    | `tsc -b --noEmit`                                  |
| `npm run lint`         | ESLint (`no-explicit-any` is an error)             |
| `npm run verify:api`   | Exercise the API client against a running backend  |
| `npm run verify:flow`  | Full live TEXT investigation round-trip            |

`verify:api` and `verify:flow` need the backend running on `127.0.0.1:8000`. `verify:flow`
performs a real investigation, which costs LLM and search calls, so it is opt-in:

```bash
RUN_LIVE=1 npm run verify:flow
```

`scripts/verify-render.mjs` server-renders the report components against a captured
investigation payload to prove the populated and empty states both render.

## Scope

Authentication, portfolios, trading, recommendations and payment flows are deliberately absent.
Phases 13–14 (screenshot/OCR, PDF) are implemented: all four input modes are operational.
