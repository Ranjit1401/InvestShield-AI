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

---

## D-013 — One structured extraction call per input

**Decision:** Claims and entities are extracted together in a single
`LLMService.structured_generate()` call. The deterministic extractors always run
as well, and their output is merged with the model's.

**Reason:** A per-sentence or per-entity call loop multiplies latency and cost and
makes partial failure likely. Keeping a deterministic half guarantees the stage
works with no API key, which is what lets extraction degrade instead of fail
(D-009). Merging is also *safer* than trusting one source: the deterministic
spans are exactly known.

**Date:** 2026-10-01

---

## D-014 — Evidence spans index the original input

**Decision:** Normalisation (`normalize_text`) is reversible: it carries an
index map, and every final claim/entity span is re-sliced from the original text
via `make_span()`, which raises if the recorded text and the slice disagree.
Normalisation therefore never destroys offsets — newlines are preserved and
whitespace collapsing is mapped back exactly.

**Reason:** Normalising `"Join   our   group"` into `"Join our group"` shifts
every offset after it. A span computed against the normalised string would point
at the wrong characters in the original — a silent, invisible corruption of the
product's core promise, since highlighting the evidence is the whole point.
Reversibility makes the optimisation safe instead of lossy.

**Date:** 2026-10-01

---

## D-015 — Generated text is never trusted as text

**Decision:** Every claim and entity returned by the model must be located in the
submitted input before it is accepted. Anything that cannot be matched verbatim
is discarded and recorded in `processing_warnings`, and the reported
`extraction_mode` becomes `PARTIAL`.

**Reason:** A model asked to extract will happily paraphrase or invent. If we
echo its output, the report would quote content the user never wrote — the exact
failure mode the product brief forbids. Re-locating the text converts a
generation problem into a lookup problem, which can be verified.

**Date:** 2026-10-01

---

## D-016 — Extraction emits no verdict

**Decision:** `ExtractionResult` has no field for `VERIFIED`, `CONTRADICTED`,
`SCAM` or any fraud probability. `Claim.confidence` is documented and tested as
*extraction* confidence only.

**Reason:** Extends D-006 from verification to extraction. Once a claim is typed
as `GUARANTEE_CLAIM` with high confidence, the temptation to read that as "high
risk" is strong; separating the two vocabularies in the schema makes the mistake
impossible to express. Verification and risk belong to Stages 5, 6 and 9.

**Date:** 2026-10-01

---

## D-017 — Search infrastructure retrieves; it does not interpret

**Decision:** Phase 3 delivers retrieval and normalization only.
`SearchService` returns normalized documents with a publisher category and a
customary-authority rank. It produces **no** statement about whether a claim is
verified, contradicted, supported, or risky. `SearchResponse` has no verdict,
score or evidence-strength field, and no schema in this layer may gain one
without a deliberate decision.

Three states are modelled explicitly and must not be collapsed:

| State | Meaning | Phase 4 must not read it as |
| --- | --- | --- |
| `OK`, zero results | We looked; nothing matched | A contradiction |
| `UNAVAILABLE` | No credentials; we never asked | Evidence either way |
| `ERROR` | We asked and failed | Evidence either way |

**Reason:** The single most dangerous failure mode in this product is silently
turning "we could not check" into "we found nothing supporting it". An empty
result set and an absent service look identical unless the schema forces them
apart, and only one of them says anything about the world. `SOURCE_PRIORITY`
ranks *source categories* for a later stage to weigh; it is metadata about who
published a document, not a finding about the document's claim. Reading
`REGULATOR` as "verified" or `GENERAL_WEB` as "false" would violate D-006 from
the opposite direction.

**Date:** 2026-10-01

---

## D-018 — Authority is recognised by hostname identity, never by keyword

**Decision:** `SourceType` is derived by exact hostname and label-boundary
suffix matching against a small explicit registry
(`sebi.gov.in`, `rbi.org.in`, `nseindia.com`, …) plus government suffixes
(`*.gov.in`, `*.nic.in`). A host containing a suggestive word is never promoted:
`fake-sebi-example.com` classifies as `GENERAL_WEB`. Unrecognised hosts fall
back to `GENERAL_WEB`/`UNKNOWN`, never to a specific category.

**Reason:** An over-claiming classifier is worse than none. If a scammer's own
site could be labelled `REGULATOR`, a later stage would present it to the user
as authoritative confirmation — precisely the harm the product exists to
prevent. The registry is deliberately small and explicit; a false negative costs
one unchecked source, a false positive corrupts the investigation.

**Date:** 2026-10-01

---

## D-019 — Query normalization is deterministic and never rewrites terms

**Decision:** `normalize_query()` only strips control/invisible characters,
folds whitespace, trims, and truncates at a word boundary. It never paraphrases,
expands, translates or adds terms. No LLM is involved.

**Reason:** A search engine cannot tell that a paraphrased query came from a
claim the user actually made. Rewriting `acmefunds@okhdfcbank` or a registration
number would mean verifying a claim nobody wrote. Mechanical cleanup is auditable
and reproducible; "improving" a query is neither (project rule 4).

**Date:** 2026-10-01
