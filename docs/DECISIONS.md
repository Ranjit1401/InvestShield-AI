# DECISIONS — InvestShield AI

Architectural decision record. Each entry states the decision, the reason, and
the date. Do not change architecture without adding a new entry (or superseding
an old one explicitly).

---

## D-001 — SQLite locally, portable to PostgreSQL/Neon

**Decision:** Use SQLAlchemy 2.x with `DATABASE_URL=sqlite:///./data/investshield.db`
as the local default. Keep all column types portable (no PostgreSQL-only types
such as `JSONB`, `ARRAY`, or `UUID` server defaults). Neon PostgreSQL becomes a
connection-string swap.

**Reason:** The project must run end-to-end on a hackathon laptop with zero
infrastructure. Portability keeps a managed Postgres upgrade cheap.

**Date:** 2026-10-01

---

## D-002 — No authentication for the MVP

**Decision:** No login, signup, JWT, OAuth, Firebase, or OTP. Investigations are
anonymous; `user_id` is nullable throughout.

**Reason:** Out of scope for the hackathon demo. Auth adds surface area without
improving the core differentiator (claim → evidence → source).

**Date:** 2026-10-01

---

## D-003 — NumPy cosine similarity as the default vector backend, not FAISS

**Decision:** Implement a `VectorStore` interface with a NumPy
cosine-similarity implementation as the default. FAISS is an optional backend
that may be added later.

**Reason:** `faiss-cpu` has no CPython 3.14 wheel, and this machine runs Python
3.14.5. For the investigation volumes of an MVP (hundreds of evidence items),
exact NumPy cosine similarity is fast enough and removes a hard dependency.

**Date:** 2026-10-01

---

## D-004 — Groq as the primary LLM provider behind `LLMService`

**Decision:** All LLM interaction goes through
`backend/app/services/llm_service.py`, exposing `generate()` and
`structured_generate()`. Groq is the primary provider. A `NullProvider`
fallback returns a typed degraded result rather than raising, so the pipeline
survives with no API key.

**Reason:** No direct Groq calls scattered through the codebase; provider
swapping (Gemini later) must be a config change. The Null provider guarantees
the demo never blanks out.

**Date:** 2026-10-01

---

## D-005 — SerpAPI behind `SearchService`

**Decision:** Web search is accessed only through
`backend/app/services/search_service.py`. Results normalize to
`title`, `url`, `snippet`, `source`, `retrieved_at`, plus a computed
`credibility` and `tier`.

**Reason:** Search results themselves are **not** authoritative evidence. A
single interface lets us add a second provider and enforces the credibility
tiering in one place.

**Date:** 2026-10-01

---

## D-006 — Absence of evidence is never an accusation

**Decision:** `VerificationStatus.CONTRADICTED` may only be assigned when an
authoritative source directly disputes the claim. A failed lookup yields
`UNVERIFIED` or `INSUFFICIENT_EVIDENCE`. The verifier must not emit
adjudicative language about a named person or company.

**Reason:** This is the product's core safety invariant. Overclaiming destroys
credibility and could defame a legitimate business. Enforced in code and
covered by a dedicated guard test.

**Date:** 2026-10-01

---

## D-007 — Risk scores are transparent weighted heuristics, not probabilities

**Decision:** Risk is a sum of configurable per-indicator weights mapped to
`LOW` / `MEDIUM` / `HIGH` / `CRITICAL`, always accompanied by a
factor-by-factor breakdown. Output never states "X% probability of scam".

**Reason:** No statistically validated model exists for this domain. Presenting
a number as a probability would be a fabricated claim — exactly what the
product promises never to do.

**Date:** 2026-10-01

---

## D-008 — Deterministic red-flag engine, LLM-assisted extraction

**Decision:** `RedFlagEngine` is pure, testable, rule-based code. LLMs are used
for claim/entity extraction and report phrasing, never as the sole detector of
red flags.

**Reason:** Red flags are pattern-detectable and must be reproducible,
inspectable, and unit-testable. LLMs add nondeterminism exactly where
determinism is most valuable.

**Date:** 2026-10-01

---

## D-009 — Every external service degrades gracefully

**Decision:** `LLMService`, `SearchService`, `OCRService`, and `PDFService`
never raise into the investigation pipeline. Each returns a typed result
carrying a degraded/error code (`LLM_SERVICE_ERROR`, `SEARCH_SERVICE_ERROR`,
`OCR_UNAVAILABLE`, `PDF_EXTRACTION_FAILED`) that is surfaced in the report's
**Limitations** section.

**Reason:** A dead API key must degrade coverage, not crash the product. The
user must be told when verification was incomplete.

**Date:** 2026-10-01

---

## D-010 — API schemas are separate from database models

**Decision:** Pydantic request/response schemas in `app/schemas/`; SQLAlchemy
ORM models in `app/models/`. A dedicated mapping layer converts between them.

**Reason:** Prevents storage concerns from leaking into the public API contract
and makes the API_SPEC a stable contract.

**Date:** 2026-10-01

---

## D-011 — LangGraph orchestrates; services hold the logic

**Decision:** LangGraph nodes are thin coordinators. Detection, verification,
evidence ranking, and risk calculation live in service/agent classes that are
independently unit-testable without running the graph.

**Reason:** Keeps the graph readable and lets us test each phase without
spinning up orchestration.

**Date:** 2026-10-01

---

## D-012 — Environment configuration via pydantic-settings only

**Decision:** All configuration lives in `app/core/config.py`, populated from
`.env`. No secrets or endpoints are hard-coded anywhere. `.env` is gitignored;
`.env.example` ships empty placeholders.

**Reason:** Required by the security rules; also makes provider swapping a
config change.

**Date:** 2026-10-01
