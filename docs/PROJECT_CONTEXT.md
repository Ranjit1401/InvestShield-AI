# PROJECT_CONTEXT — InvestShield AI

> Tagline: **Investigate Before You Invest.**

This file is the canonical statement of *what* InvestShield AI is.
See `ARCHITECTURE.md` for *how*, `CURRENT_STATE.md` for *where we are*.

---

## 1. Product Name

**InvestShield AI**

## 2. Product Purpose

InvestShield AI is an **AI-powered investment-claim investigation and
investor-safety platform**.

A user submits suspicious investment material — text/messages, a URL, a
screenshot, or a PDF — and the system **investigates it** rather than merely
labelling it.

The investigation pipeline answers:

1. What was claimed?
2. Who is involved?
3. What red flags exist?
4. Can each claim be independently verified?
5. What does the available evidence say?
6. Which sources support or contradict each claim?
7. Why was this flagged?
8. What should the user verify independently before acting?

## 3. Target Users

- **Retail investors** who receive investment offers through WhatsApp,
  Telegram, Instagram, YouTube, emails, PDFs, or influencer content.
- **First-time investors** who cannot evaluate intermediary claims.
- **Family members / advisers** helping someone assess an offer.

Users are assumed to be **non-technical**. Every explanation must be written in
plain, understandable language.

## 4. Core Differentiator

InvestShield is **not** a scam classifier.

> InvestShield does not simply detect suspicious investment content. It
> **investigates the claims behind the content and shows the evidence** that
> supports or fails to support those claims.

Every result is traceable:

```
CLAIM  →  EVIDENCE  →  SOURCE
```

## 5. Product Boundaries

### InvestShield IS

- An evidence-backed claim verification engine
- An investor-safety and fraud-indicator tool
- An explainability-first analysis system
- A source-traceable research assistant for investment content

### InvestShield MUST NOT become

- A stock predictor or price forecaster
- A trading bot or automated trading system
- A portfolio optimizer
- A buy / sell / hold recommendation engine
- A financial adviser
- A tool that says "invest ₹50,000 in X"
- A generic chatbot that outputs free-form prose
- A system that labels a named person or company "a scammer"
- A source of fabricated evidence, fake data, or fake confidence percentages

### Hard safety rule

The system must distinguish between:

```
VERIFIED  →  independently confirmed by authoritative sources
UNVERIFIED →  no confirmation found; NOT an accusation
CONTRADICTED →  authoritative evidence directly disputes the claim
INSUFFICIENT_EVIDENCE →  verification could not be completed
NOT_APPLICABLE →  claim is not externally verifiable
```

**Absence of a search result must never produce `CONTRADICTED`, and must never
produce an accusation of fraud.**

Required phrasing: *"Could not independently verify the claimed registration."*
Forbidden phrasing: *"This person is a fraudster."*

### What "evidence" means here

An evidence item is **retrieved text plus its provenance**, and nothing else:

```
CLAIM ──► EVIDENCE ──► SOURCE (url, title, publisher, tier, retrieved_at)
          └─ verbatim snippet (or title), never summarised
             └─ SUPPORTS | CONTRADICTS | IDENTITY_REFERENCE | CONTEXT | MENTIONS
```

Three rules follow from rule 10 above and are enforced in code, not by convention:

- **An excerpt is copied, never written.** It comes from the provider's snippet,
  or from the result title when there is no snippet, and the item records which.
  A rewritten quote cannot be checked against the page it claims to come from.
- **No evidence without a document.** When search is unavailable, fails, finds
  nothing, or the claim was never externally verifiable, the answer is an empty
  evidence set plus a factual statement of why — never a placeholder, never an
  invented "no authoritative evidence found" citation.
- **Evidence never re-decides.** Relationship and relevance are read off the
  verification stage's own assessment. The evidence layer may make a finding
  *harder* to treat as proof; it can never make one easier.

`SUPPORTS` means an authoritative record was read as supporting the claim. It is
not a statement that the claim is true, and it is not a statement that the
investment is safe.

## 6. Technology Stack

### Frontend

| Concern | Choice |
| --- | --- |
| Framework | React 18 + Vite |
| Language | TypeScript |
| Styling | Tailwind CSS + shadcn/ui |
| Icons | Lucide React |
| Charts | Recharts |

### Backend

| Concern | Choice |
| --- | --- |
| Language | Python 3.14.5 |
| API | FastAPI + Uvicorn |
| Validation | Pydantic v2 |
| ORM | SQLAlchemy 2.x (declarative, 2.0 style) |
| Database | SQLite locally, portable to PostgreSQL/Neon |
| Config | pydantic-settings (`.env`) |
| Testing | pytest + pytest-asyncio + httpx |

### AI / Agents

| Concern | Choice |
| --- | --- |
| Orchestration | LangGraph |
| LLM | Groq (primary), abstracted behind `LLMService` |
| Search | SerpAPI, abstracted behind `SearchService` |
| Embeddings | sentence-transformers `all-MiniLM-L6-v2` (optional) |
| Vector store | NumPy cosine similarity by default, replaceable |
| OCR | pytesseract + Tesseract binary |
| PDF | PyMuPDF (`fitz`) |

## 7. Important Environment Details

Verified on this machine:

| Item | Value |
| --- | --- |
| OS | Windows (`win32`) |
| Python | 3.14.5 at `C:\Python314\python.exe` |
| Node.js | v20.20.2 |
| npm | 10.8.2 |
| Git | `C:\Program Files\Git\cmd\git.exe` |
| Tesseract | `C:\Program Files\Tesseract-OCR\tesseract.exe` (**not on PATH**) |
| Working directory | `C:\Users\Ranjit\Desktop\InvestShield AI` |

Python 3.14 constraints:

- `faiss-cpu` has no CPython 3.14 wheel → **NumPy cosine similarity is the
  default vector backend.** FAISS is optional, never mandatory.
- Do not add dependencies that lack 3.14 support without verifying first.

Already installed in the system interpreter: `fastapi`, `pydantic`,
`pydantic-settings`, `SQLAlchemy`, `psycopg`, `psycopg2-binary`, `uvicorn`,
`httpx`, `torch`.

## 8. Authentication Status

**No authentication for the MVP.** No login, signup, JWT, OAuth, Firebase, or
OTP. Investigations are anonymous and `user_id` is nullable.

## 9. Supported Input Types

```
TEXT | URL | IMAGE | PDF
```

Every input type normalizes into one common investigation representation so the
downstream pipeline is input-agnostic and language-independent.

## 10. Current Architecture (summary)

```
React SPA  ──HTTP──>  FastAPI API layer
                          │
                     Service layer (InvestigationService)
                          │
                     Investigation engine (LangGraph)
                          │
             Agents + deterministic tools
                          │
        DB / Search / OCR / PDF / LLM / Embeddings
```

Full detail in `ARCHITECTURE.md`.

## 11. Non-negotiable Engineering Rules

1. Working software first.
2. Evidence over assumptions.
3. Deterministic code wherever deterministic logic suffices.
4. LLM only where reasoning or language understanding is genuinely required.
5. Every investigation result must be traceable to a source.
6. Every source must be identifiable (URL, title, type, retrieval time).
7. Every risk factor must carry a reason.
8. Uncertainty must be explicitly communicated, never hidden.
9. Modular architecture; no giant files, no duplicated logic.
10. Never invent evidence, sources, or probabilities.

## 12. Session Recovery Rule

At the start of every fresh session, read in this order:

```
docs/CURRENT_STATE.md
docs/IMPLEMENTATION_PLAN.md
docs/ARCHITECTURE.md
docs/DECISIONS.md
docs/DEVELOPMENT_LOG.md
```

Then inspect `git status` and the actual source tree before doing any work.
**The repository documentation is the source of truth.** Do not ask the user to
re-explain the architecture if it is already documented here.
