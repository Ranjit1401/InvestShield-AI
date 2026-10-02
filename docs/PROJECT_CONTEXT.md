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
7. How many documented risk indicators are present, and which ones?
8. What should the user verify independently before acting?

Note what question 7 is and is not. It asks *how many documented indicators were
found and what they were* — not *is this a scam*, and not *should I invest*. The
score is a transparent, bounded, explainable indicator count, and it is the only
summary judgement the product produces.

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

### What the risk score means

The investigation produces a **risk score**, and its meaning is fixed by the
product rather than by whoever reads it:

```
RAW SCORE  = Σ contribution of each distinct documented indicator
RISK SCORE = min(raw score, 100)      a ceiling, never a rescaling
RISK LEVEL = LOW (0–20) | MEDIUM (21–50) | HIGH (51–90) | CRITICAL (91–100)
```

Each contribution is a **declared integer weight** from configuration, and no
factor can contribute a negative amount. Every factor is returned with its
weight, its actual contribution, the red flag, claim, evidence and source ids
behind it, and a plain-language reason.

Four rules follow from the hard safety rule above, and are enforced in code:

- **The score is not a probability.** Not of fraud, not of loss, not of the
  investment failing. It is a transparent count of documented indicators, and
  every assessment carries that statement in its own output. The weights and
  bands are declared product judgements, not calibrated measurements.
- **Absence of evidence never raises the score.** A claim that could not be
  checked contributes **zero**, lowers a reported *completeness* figure, and is
  stated in the output. A tool that cannot reach the internet must never report
  its own blindness as somebody else's risk. A search that completed and found
  nothing is different — that is a real gap in the public record, and it is
  scored.
- **One signal scores once.** The same behaviour noticed by several stages is
  counted a single time. The other views are still shown, marked as already
  counted, so the reader sees that a claim was contradicted without the
  severity being inflated in proportion to how hard we looked.
- **No finding lowers the score.** A claim confirmed by an authoritative record
  is not a risk indicator, and no outcome cancels another. This is an
  accumulation, not a net.

Required phrasing: *"Based on 5 contributing risk factors, this content scored
68 (MEDIUM). The score is an indicator of documented risk factors, not a
probability of fraud or loss."*
Forbidden phrasing: *"68% probability of being a scam."* / *"This is a safe
investment."* / *"You should avoid this investment."*

The same vocabulary rules apply to the score's own component names: no verdict
words, no absolution words, no advice. They are checked by test against every
field and warning the engine can produce, not by review.

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

## 6.2 How the investigation runs

The stages are connected by a **LangGraph** graph. The graph is the
orchestrator and nothing else: it decides the order, passes each stage's output
to the next, and records what happened. Every judgement in the system is made by
the stage that owns it — the pattern engine detects patterns, the verification
service decides a claim's status, the evidence service assembles provenance, and
the risk service produces the score. The graph never makes a finding of its own.

```
START ─► input ─┬─(error)─► END
                └─(ok)────► extraction ─► red_flags ─► verification ─► evidence ─► risk ─► END
```

Two properties are deliberate and enforced by tests rather than by convention:

- **A limitation never stops an investigation.** Search being unavailable, a
  claim that cannot be checked, an extraction that fell back to patterns: each is
  recorded as a structured warning with a stable code, and the investigation
  continues. A user with no network still gets everything the offline stages can
  establish, plus an honest statement of what could not be checked.
- **A failure always stops it.** If a stage breaks in a way its contract says is
  impossible, the run ends with a structured error and **no risk score at all**.
  A stopped investigation must never be mistakable for a finished one that found
  little — which is the failure mode that would matter most, because it makes the
  tool's silence look like reassurance.

The graph makes no network call of its own. External access stays inside the
search service, and the searches performed during verification are recorded so
the evidence stage can cite them rather than repeating them.

### The default test suite is offline, and that is enforced

An autouse fixture installs a guard that blocks `socket.socket.connect`,
`connect_ex`, `socket.create_connection` and `socket.getaddrinfo`, so a test that
reaches the network fails loudly instead of quietly succeeding on whatever
`GROQ_API_KEY` or `SERPAPI_KEY` the developer's `.env` supplies. A blocked call
raises `NetworkAccessBlocked` naming the destination, so the offending test is
fixable from its own output.

Two exemptions, both deliberate and both pinned by tests: **loopback literals**,
because `TestClient(app)` as a context manager needs one on Windows; and tests marked
`@pytest.mark.integration`, which are deselected by default anyway. The guarantee is
that **no traffic leaves the machine** — loopback never does.

One limit worth knowing: `psycopg2` connects through libpq, which never enters
Python's `socket` module, so the guard cannot see a database connection. No test
points a database at a routable address (D-045).

---

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
11. Absence of evidence is never a finding. Uncertainty is reported, not scored.
12. One underlying signal scores once, however many stages noticed it.
13. A limitation never stops an investigation; a failure always does. A run that
    stopped must never be mistakable for a run that finished and found little.
14. **The default test suite must not reach the network.** Not as a convention — as a
    guard. A test that reaches a provider usually still *passes*, which is what makes
    an accidental live call invisible (D-045).
15. **A guarantee is asserted at every layer text or data crosses**, not only where it
    originates. A caveat that survives the engine and is dropped by an adapter is
    indistinguishable from one that was never added (D-046).
16. **Security properties are proven by planting the secret**, not by reading the
    handler that would have to filter it. Leak paths are rarely where they look
    (D-047).
17. **Do not restate a claim as a stronger one.** A semantic guarantee is not a byte
    guarantee; a stored value is not a measured one. If a documented property is
    stronger than the code, correct the documentation — and add the test that says
    which one the code provides.

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
