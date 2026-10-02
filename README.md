# InvestShield AI

> **Investigate Before You Invest.**

InvestShield AI is an **AI-powered investment-claim investigation and
investor-safety platform**. Submit an investment message, a suspicious URL, a
screenshot, or a PDF — the system does not merely label it suspicious. It
**investigates the claims inside it and shows the evidence.**

> InvestShield does not simply detect suspicious investment content. It
> investigates the claims behind the content and shows the evidence that
> supports or fails to support those claims.

---

## What it does

```
WHAT WAS CLAIMED?
        ↓
WHO IS INVOLVED?
        ↓
WHAT RED FLAGS EXIST?
        ↓
CAN THE CLAIM BE VERIFIED?
        ↓
WHAT DOES THE EVIDENCE SAY?
        ↓
WHICH SOURCES SUPPORT IT?
        ↓
WHY WAS IT FLAGGED?
        ↓
WHAT SHOULD I VERIFY BEFORE ACTING?
```

Every result is traceable:

```
CLAIM  →  EVIDENCE  →  SOURCE
```

## What it is NOT

- Not a stock predictor, trading bot, or portfolio optimizer.
- Not a buy/sell/hold recommendation engine, and not financial advice.
- Not a "SCAM: 92%" classifier — it produces evidence-backed risk indicators.
- Not a system that calls a named person or company a fraudster. When a
  registration cannot be found, InvestShield says *"could not independently
  verify"* — never *"this is a scammer"*.

## Status

**Phases 0–8 are complete.** The full investigation pipeline runs and is exposed
over HTTP:

| Phase | Scope | Status |
| --- | --- | --- |
| 0–2 | Foundation, red flags, claim/entity extraction | complete |
| 3–5 | Search infrastructure, verification, evidence | complete (search only) |
| 6 | Risk engine | complete |
| 7 | LangGraph orchestration | complete |
| 8 | FastAPI investigation API | complete |
| 9+ | Persistence, report layer, frontend | not started |

Live endpoints:

```
GET  /api/health
GET  /api/investigations/limits
POST /api/investigations
POST /api/investigations/text
```

`POST /api/investigations/text` returns the complete investigation synchronously —
claims, entities, red flags, verification results, evidence with sources, the risk
assessment, a stage timeline, and a `limitations` array of machine-readable codes.

A **partial** run is a `200`, not an error. If search or the LLM is unavailable the
response carries `status: PARTIAL` and names exactly which check did not happen.
Only a malformed submission or a broken internal contract produces a `4xx`/`5xx`.

Only `TEXT` input is analysed. `URL`, image and PDF endpoints are not implemented
yet — those input types are recognised and refused with a `422` that lists what
this version does analyse.

See [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) for the full
roadmap and [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md) for exactly where
work stopped.

---

## Repository layout

```
investshield-ai/
├── backend/            # FastAPI service (Python 3.14)
│   ├── app/
│   │   ├── api/        # HTTP routes + request/response adapters + error mapping
│   │   ├── core/       # config, logging
│   │   ├── db/         # SQLAlchemy engine/session/base
│   │   ├── models/     # ORM models
│   │   ├── schemas/    # Pydantic API contracts
│   │   ├── services/   # domain + external services
│   │   ├── agents/     # ClaimEntity, ScamIntelligence, Verification,
│   │   │               # Evidence, Report agents
│   │   ├── graph/      # LangGraph state + nodes + builder
│   │   ├── tools/      # deterministic helpers
│   │   └── main.py
│   ├── tests/
│   ├── requirements.txt
│   └── requirements-ai.txt
├── frontend/           # React + Vite + TypeScript (Phase 11)
├── data/               # local SQLite database
└── docs/               # persistent project memory
```

---

## Getting started

### 1. Environment

Python 3.14.5 and Node 20 are expected on this machine. Tesseract is expected
at `C:\Program Files\Tesseract-OCR\tesseract.exe`.

```powershell
cd "C:\Users\Ranjit\Desktop\InvestShield AI"

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r backend\requirements.txt
```

Optional AI / OCR / PDF dependencies (LangGraph, Groq client,
sentence-transformers, pytesseract, PyMuPDF) — every one is optional and the app
runs without them:

```powershell
pip install -r backend\requirements-ai.txt
```

### 2. Configuration

```powershell
Copy-Item .env.example .env
```

Then fill in whatever you have. **Never commit `.env`.**

| Variable | Purpose | Required |
| --- | --- | --- |
| `DATABASE_URL` | SQLite path, or a Neon/Postgres URL | no (defaults to SQLite) |
| `GROQ_API_KEY` | LLM provider for claim/entity extraction | no (falls back to deterministic rules) |
| `SERPAPI_KEY` | Web search for claim verification | no (verification reported as unavailable) |
| `TESSERACT_CMD` | Full path to the Tesseract binary | no (auto-detected on Windows) |
| `RISK_WEIGHT_*` | Tunable heuristic indicator weights | no |
| `RISK_BAND_*` | Risk band thresholds | no |

InvestShield degrades rather than fails: a missing key reduces investigation
coverage and is stated in the report's **Limitations** section.

### 3. Run the backend

```powershell
cd backend
python -m uvicorn app.main:app --reload
```

- API: <http://localhost:8000>
- Interactive docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/api/health>

Try an investigation:

```powershell
curl.exe -X POST http://localhost:8000/api/investigations/text `
  -H "Content-Type: application/json" `
  -d '{\"text\": \"Guaranteed 30% monthly returns with no risk. Pay the 50000 INR activation fee today only.\"}'
```

### 4. Run the tests

```powershell
cd backend
python -m pytest
```

Expected: `1997 passed, 4 deselected`. The suite is fully offline — it clears the
API keys and the `DATABASE_URL` environment variable, so it never reaches a
network service even when a real `.env` is present.

### Optional: run the whole stack with Docker

```powershell
docker compose up --build
```

---

## Documentation (read these first)

| File | Contents |
| --- | --- |
| [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) | Product purpose, boundaries, stack, safety rules |
| [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md) | **Recovery file** — exactly where the project stands |
| [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) | Phase-by-phase roadmap with status markers |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Layers, LangGraph graph, data flow, security |
| [`docs/AI_PIPELINE.md`](docs/AI_PIPELINE.md) | Every investigation stage, in detail |
| [`docs/API_SPEC.md`](docs/API_SPEC.md) | Endpoints, schemas, status codes, examples |
| [`docs/DATABASE_SCHEMA.md`](docs/DATABASE_SCHEMA.md) | Tables, columns, relationships, indexes |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Architectural decisions with reasons |
| [`docs/DEVELOPMENT_LOG.md`](docs/DEVELOPMENT_LOG.md) | Chronological implementation log |

### Session recovery

If you are an AI agent or a new developer picking this project up cold:

1. Read `docs/CURRENT_STATE.md`, then `docs/IMPLEMENTATION_PLAN.md`, then
   `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/DEVELOPMENT_LOG.md`.
2. Run `git status` and inspect the tree.
3. Run `cd backend && python -m pytest`.
4. Continue from **Next Exact Task** in `docs/CURRENT_STATE.md`.

The repository documentation is the source of truth. Do not ask for the
architecture to be re-explained — it is already written down.

---

## Demo input (synthetic test content only)

> 🚨 Exclusive AI Trading Opportunity 🚨
> Our SEBI-approved expert team guarantees 35% monthly returns.
> Join our private Telegram group today.
> Minimum investment ₹25,000.
> Pay directly to our account to activate your trading account.

Expected investigation: split into 6 atomic claims, 8 red flags
(guaranteed return, unrealistic return, urgency, unverified regulatory claim,
Telegram group, direct payment, activation fee, …), targeted regulatory
verification, claim → evidence → source links, and an explainable
HIGH/CRITICAL risk breakdown.

---

## Safety notice

InvestShield AI provides **information, evidence signals, and verification
guidance**. It is **not financial advice**, and a low risk level does not
guarantee that an entity is legitimate. Always verify an intermediary's
registration independently — through the regulator's own website — before
transferring any money.
