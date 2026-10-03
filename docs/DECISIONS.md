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

---

## D-020 — Verification reports a claim's factual status, never a verdict

**Decision:** Verification answers exactly one question — *can external sources
independently establish this specific factual claim?* — with one of five
controlled statuses: `VERIFIED`, `UNVERIFIED`, `CONTRADICTED`,
`INSUFFICIENT_EVIDENCE`, `NOT_APPLICABLE`. No `SCAM`, `FRAUD`, `SAFE` or
`DANGEROUS` status exists and no field may be added that expresses one.
`confidence` is confidence *in the status assigned given the evidence found* —
never a probability of fraud, of loss, or of the claim's truth.

Verification stays separate from red-flag detection (Phase 1) and from risk
scoring (Phase 6). A claim can be perfectly `VERIFIED` — an authorised,
registered adviser made the statement — and still carry every red flag in the
content. Merging the two would produce a status that is confidently wrong about
the only thing that matters.

**Reason:** This is the product's central honesty constraint (D-006). A verdict
field would be read as "the system says this is a scam", which no amount of
downstream hedging could undo, and it would launder a heuristic into a finding.
Keeping verification to a checkable factual question means every status can be
traced to specific documents, and keeps risk, where the judgement belongs, in a
phase that is explicitly allowed to make one — and required to show its working.

**Date:** 2026-10-01

---

## D-021 — Only a direct authoritative conflict produces `CONTRADICTED`

**Decision:** `CONTRADICTED` is reachable from exactly one branch of the
decision table: an identity-matched record from a registry authority whose
records are relevant to that claim family, containing an explicit conflict cue
("registration cancelled", "not registered", …). Absence of evidence never
produces it. Search unavailable, search failed, zero results, identity
ambiguity, identity not found and "no relevant source" all resolve to
`UNVERIFIED` or `INSUFFICIENT_EVIDENCE`, and authority *tier* alone can never
settle a claim — a regulator page that says nothing about the claim is not
evidence against it (D-018).

Three gates must pass before any document may move a status: **identity**
(token-boundary match on the exact claimed entity, with longer names rejected as
ambiguous), **authority** (registry member, tier 1–3), and **relevance** (that
authority's records can speak to *this* claim family — SEBI can establish who is
registered, not what a scheme returns).

**Reason:** "We found nothing" and "we found a contradiction" lead to opposite
responses from a user, and only one of them is a statement about the world.
Collapsing them is how a search tool becomes an accusation engine. The three
gates also address the failure that matters most in identity: transferring one
entity's record to another. `ABC Capital` must not be confirmed by a page about
`ABC Capital Advisors`, so a prefix match is treated as ambiguous rather than as
a match, and relevance is scoped per claim family so the most authoritative
source on the web cannot rubber-stamp a promised return.

**Date:** 2026-10-01

---

## D-022 — Explanation wording is rendered from fixed templates, never generated

**Decision:** A verification result's human-readable `reason` is rendered from
`EXPLANATION_TEMPLATES`, a fixed string per `reason_code`, with placeholders
filled from a known context. No LLM writes, paraphrases or "improves" a
reason, a warning or a status. A guard test asserts that no template, warning or
produced result contains verdict vocabulary (`scam`, `fraud`, `safe`,
`dangerous`, `buy`, `sell`, `invest`, `legitimate`, …), matched on word
boundaries so ordinary words like *investigation* are unaffected.

**Reason:** The wording of a status is the part a user actually reads, and it is
where a pipeline drifts into accusation. A generated sentence can say "the
registration could not be verified, which suggests caution" one run and "the
registration could not be verified" the next — an accusation whose presence
depends on sampling. Fixed templates make the phrasing auditable line by line,
keep statuses reproducible (D-008), and mean a change in tone requires a
reviewed code change rather than a prompt edit.

**Date:** 2026-10-01

---

## D-023 — Evidence type, relationship and relevance are three separate facts

**Decision:** An evidence item carries three independent axes and they never
collapse into one:

| Axis | Values | Question it answers |
| --- | --- | --- |
| `evidence_type` | `REGULATORY_RECORD`, `GOVERNMENT_RECORD`, `EXCHANGE_RECORD`, `OFFICIAL_ENTITY_SOURCE`, `SEARCH_RESULT` | *What kind of record is this?* |
| `relationship` | `SUPPORTS`, `CONTRADICTS`, `IDENTITY_REFERENCE`, `CONTEXT`, `MENTIONS` | *How does it relate to the claim?* |
| `relevance` | `HIGH`, `MEDIUM`, `LOW` | *How directly does it bear on the claim?* |

`evidence_type` is derived from Phase 4's authority tier and nothing else.
`relationship` and `relevance` are derived from Phase 4's `AssessedSource` and
never from a fresh reading of the text. The longer taxonomy that was considered
for this phase — with members such as `CLAIM_SUPPORT`, `CLAIM_CONTRADICTION` and
`CONTEXTUAL` — is rejected: those encode exactly what the other two axes carry,
and two copies of one fact will eventually disagree. A report reading
"type: contradiction, relationship: supports" is worse than no type at all.

Alongside this, three structural rules make the no-fabrication guarantee
enforceable rather than aspirational:

1. **An excerpt is a verbatim slice of a `SearchResult` field** — `snippet`, or
   `title` when there is no snippet — and `excerpt_origin` records which. Title
   and snippet are never stitched together, never summarised, never paraphrased.
   `ALLOWED_EXCERPT_ORIGINS` admits nothing else, at construction time.
2. **No evidence without a retrieved document.** An empty evidence tuple is the
   correct answer when search was unavailable, failed, found nothing, or was
   never applicable; there is no placeholder item and no synthesised "no evidence
   found" document. `NOT_APPLICABLE` always yields nothing, because no record was
   ever looked up for it.
3. **Only documents Phase 4 recorded may be cited.** A supplied result whose
   `src_`/`res_` id is absent from the claim's `VerificationResult` is dropped,
   with a warning naming how many. Without this, a caller could attach a
   document the verification never saw and Phase 5 would have no way to know
   whether it was real.

**Reason:** Verification answers "can external sources establish this claim?",
and that answer is worthless alone — "UNVERIFIED, no matching registration
found" is a claim about the system's work, not about the world. Evidence
supplies the missing half, so the quote has to be checkable against the document
it claims to come from. A rewritten excerpt cannot be checked, which is why the
origin field is mandatory rather than merely convenient. The second and third
rules exist because a system that fabricates a plausible citation is worse than
one that reports nothing: the first is invisible to the reader, and the second is
visible and honest.

Keeping evidence a *separate* axis from risk also stops a quiet promotion. No
member of any of the three vocabularies states that a claim is true, false, safe
or fraudulent, and `EvidenceRelevance` is a label rather than a number
precisely so it cannot be summed into a score by a later phase without a
reviewed code change (D-007).

**Date:** 2026-10-01

---

## D-024 — Evidence explains verification; it can narrow it, never widen it

**Decision:** `relationship` is a function of Phase 4's `AssessedSource` and the
claim's `verification_status`/`reason_code`. Phase 5 re-reads no text, re-detects
no cue and re-classifies no publisher. It may *narrow* Phase 4 in exactly one
place, and never the other way: a document carrying a supporting cue that Phase 4
ignored — because it failed the identity gate, the authority gate, or because the
claim's own reason code was one that relies on no source — is presented as
`MENTIONS` or `CONTEXT`, never as `SUPPORTS`.

`verification_status` is copied onto every item and onto the bundle, and is never
recomputed. Where a claim's status and its showable evidence diverge — a
`VERIFIED` claim whose confirming record was not among the retrieved text, for
instance — the bundle keeps Phase 4's status and adds a warning naming the gap.
It does not quietly downgrade the verdict, and it does not upgrade it either.

`CONTEXT` additionally requires an authoritative publisher: a similarly named
party documented by a relevant authority is context a reader can act on, and the
same ambiguity on an arbitrary web page is only a `MENTION`.

**Reason:** An evidence layer that can reach its own conclusions is a second
verification engine, and it would be a worse one: it would have fewer gates,
no test coverage, and no reason code explaining why it disagreed. The failure
that matters most is the directional one — a claim marked `UNVERIFIED` whose
evidence set nonetheless contains a "supports" item reads as a contradiction
between the status and the detail, and users resolve that by believing whichever
is more alarming. Narrowing is safe because it can only remove a claim to proof;
widening is not.

Keeping the status rather than downgrading it is the symmetric requirement. The
whole point of the phase is to show the reader what was found; overriding Phase
4 would mean the visible evidence and the stated conclusion disagreed, which is
the same defect in the other direction.

**Date:** 2026-10-01

---

## D-025 — The risk score is a transparent heuristic indicator sum, never a probability

**Decision:** `RiskAssessment.risk_score` is `min(Σ factor.contribution,
ceiling)`, where each contribution is a declared integer weight from `Settings`
and no factor can contribute a negative amount. `risk_level` is the band that
capped score falls into: `0..20` LOW, `21..50` MEDIUM, `51..90` HIGH,
`91..100` CRITICAL, boundaries inclusive at the top of each band and
monotonically increasing.

The bands and weights are **product heuristics**. They are not empirically
validated, not derived from any dataset, and not calibrated. Every assessment
carries `SCORE_NOT_A_PROBABILITY` as its final warning, and
`RiskAssessment` appends that string during validation — so it cannot be omitted
by any caller, including a future Phase 7 rehydrating stored data. The caveat
names both misreadings a user could take: it is not a probability of fraud or
loss, and it is not a recommendation to invest or not invest.

`evidence_coverage` and `analysis_completeness` are bounded ratios in `[0, 1]`
and are documented in the schema as transparency measures, **not** confidence or
accuracy scores. Nothing in the schema is typed or named as a probability.

**Reason:** A number between 0 and 100 invites the reading "68% chance of
something", and a tool that invites it will be believed that way regardless of
what the documentation says. The structural responses are: bound the number,
make every component inspectable, state the band table explicitly, and attach
the caveat to the data rather than to the page around it. A confirmation by an
authoritative record produces no factor at all and nothing nets off, so a
`VERIFIED` claim can never lower a score either (D-020) — the phase accumulates
documented indicators and does not trade them against one another.

Keeping the cap as a ceiling rather than a rescaling is what makes one score
comparable with another: two documents far past the ceiling must score
identically, or the number means something different in every report.

**Date:** 2026-10-02

---

## D-026 — One signal scores once, however many stages noticed it

**Decision:** A guaranteed-return promise appears in the pipeline four times: a
Phase 1 red flag, a Phase 2 claim, a Phase 4 verification result, and Phase 5
evidence. Phase 6 emits **one scoring factor** for it. Where a verification
result describes a signal a red-flag factor has already counted, that result's
factor is kept in the bundle at `contribution = 0` with an `absorbed_into`
pointer to the factor that counted it, and the primary gains the claim's
verification status as supporting context.

Absorption requires a **scoring** primary, and never applies to a factor that
already weighs nothing. A duplicate `RedFlagCode` collapses to one; two
`VerificationResult`s for one `claim_id` collapse to one. Claims are linked to
red-flag factors by span overlap in the submitted text, or by a deliberately
narrow `CLAIM_TYPE_RED_FLAG_CODES` map for the cases where Phase 1 and Phase 2
segmented the same words differently. Evidence attaches `ev_`/`src_` ids and
contributes nothing.

`RiskFactor` validates `contribution <= weight`, so a factor can never
contribute more than its declared weight and never more than once.

**Reason:** Double counting is not a cosmetic defect here; it is how a
document's severity gets inflated in proportion to how thoroughly the tool
analysed it. A content item that trips three rules and is contradicted by an
authoritative record would otherwise score four times, and the reader would
conclude the product is more certain than it is. Keeping the absorbed factor
rather than dropping it is the other half of the requirement: dropping would
hide that a claim was contradicted, which is the most material fact in the
assessment.

The two exclusions exist for different reasons. A zero-weight primary must not
absorb, or a red flag configured to weigh nothing would silence the claim's own
finding entirely. A factor that already weighs nothing — in practice every
`INSUFFICIENT_EVIDENCE` factor — has nothing to de-duplicate, and pointing it at
a primary would imply that primary accounted for a verification it never
performed: the red flag counted a *pattern*, and said nothing about whether the
claim could be checked.

**Date:** 2026-10-02

---

## D-027 — Absence of evidence never raises the risk score

**Decision:** `INSUFFICIENT_EVIDENCE` carries a default weight of `0`.
`VERIFIED` and `NOT_APPLICABLE` produce no factor at all. An `UNVERIFIED`
result produces an `UNVERIFIED_CLAIM` factor **only** when its reason code says
a search actually completed — `NO_CONFIRMATION_FOUND`, `IDENTITY_NOT_FOUND` or
`IDENTITY_AMBIGUOUS`. Reason codes meaning the search never ran
(`SEARCH_UNAVAILABLE`, `SEARCH_FAILED`, `NO_QUERY_BUILT`,
`NO_CLAIM_RELEVANT_SOURCE`, `ZERO_RESULTS`) produce no contribution.

Uncertainty is reported instead of scored, through `RiskFactor.is_uncertainty`,
`RiskAssessment.analysis_completeness`, and a warning. `SEARCH_UNAVAILABLE` and
`ZERO_RESULTS` raise different warnings, because "we could not look" and "the
register was reached and held no entry" are different facts about the world.

Only claim families in `RISK_RELEVANT_CLAIM_TYPES` may produce a factor at all.
`COMPANY_CLAIM` is excluded: an unverifiable statement about opening hours is
not an investment risk indicator. `PAYMENT_INSTRUCTION` is included, because
where the money goes is squarely material.

**Reason:** The failure this prevents is specific and severe: a system whose
score rises when it loses connectivity. The tool would report the highest risk
precisely when it is least informed, and the number would be arithmetically
correct, well-explained, and entirely fictional. This is the reason the
reason-code gate in `factor_type_for` exists as a separate step: Phase 4's schema
permits `UNVERIFIED` alongside `SEARCH_UNAVAILABLE` even though the engine never
emits that pairing, so the combination is representable and had to be excluded at
the point of use rather than assumed away.

The mirror-image error is excluded by the same rule. `VERIFIED` means an
authoritative record established the claim — not that the party is legitimate or
the investment sound — and no `VerificationFactorType` member may encode a risk
reduction.

**Date:** 2026-10-02

---

## D-028 — One weight table, resolved through the stage that owns each weight

**Decision:** Red-flag weights are resolved through Phase 1's `RULES_BY_CODE`
and each rule's `weight_attr`, using the same fallback logic as
`RedFlagEngine.weight_for`. `RiskWeights.red_flag_weights` is keyed by the
`RedFlagCode` enum, not by a string. Phase 6 adds exactly four settings —
`risk_weight_contradicted_claim`, `risk_weight_unverified_claim`,
`risk_weight_insufficient_evidence` and `risk_score_ceiling` — and reads the
existing fifteen.

`RiskWeights` and `RiskThresholds` are snapshotted onto every assessment, so a
stored report shows the configuration that produced its own score and level
rather than today's configuration.

**Reason:** A weight table that can disagree with itself is worse than no
weight table, because the disagreement is invisible. This was not hypothetical:
the first implementation keyed the table by `code.value.lower()` and looked it
up with `code.value`, so every red flag silently resolved to weight `0`. The
assessment still displayed red-flag factors; it simply scored all of them as
nothing. Rebuilding setting names from code values is the same hazard in slower
motion, so weights are read through the rule table that already declares them.
Keying by the enum removes the casing class of bug entirely.

Snapshotting the configuration is what lets a report be audited months later,
when the weights have moved on (D-007).

**Date:** 2026-10-02

---

## D-029 — Generated output vocabulary is tested, not reviewed

**Decision:** `test_risk_safety.py` walks every assessment the engine can
produce — one red flag per rule, all five Phase 4 statuses, with and without
evidence, all four bands, and the empty investigation — and checks the
`label`, `description`, `reason` and `source` of every factor plus every
warning, against a ban list of judgement and advice words.

Matching is **negation-aware**. A banned word preceded by a negation within a
short window, with no sentence or clause boundary in between, is treated as a
denial and permitted. This is required by the product's own wording: the
standing caveat says the score "is not a probability of fraud", and Phase 1's
`UNVERIFIED_ADVISER` description says its detection is "not a finding that the
person is unverified or fraudulent". Both name the banned thing in order to rule
it out.

Two scoping decisions:

- The advice ban (`buy`, `sell`, `invest`, `recommend`, …) applies to text
  **Phase 6 authors**. For a red-flag factor, `label`, `description` and `reason`
  are Phase 1's rule catalogue reproduced verbatim, and a rule may legitimately
  name the activity it detects — `BORROW_TO_INVEST` is named "Borrowing to
  Invest". That is a description of what was found, not advice to the reader.
- The judgement ban applies to Phase 6's own text. Phase 1's catalogue is
  checked separately by `TestPhaseOneCatalogueTripwire`, which pins the exact
  known set rather than suppressing it. One occurrence is asserted rather than
  denied: `SUSPICIOUS_URL`'s "commonly seen in fraudulent campaigns". Changing
  it is a Phase 1 decision with its own tests, so it is recorded, not silently
  rewritten from here.

**Reason:** Documentation does not stop a future edit from introducing "you
should invest" into a warning string. A test does, provided it covers the output
space rather than a sample — which is why the scenarios are assembled from the
taxonomies instead of hand-listed.

The negation-awareness is the part worth arguing for. A blunt word-boundary ban
is unsatisfiable without suppressing the disclaimers that carry the product's
promise, and the obvious fix — an allowlist of approved strings — is a loophole
that grows one entry per failure. A negation check is narrow, has no list to
abuse, and is tested directly against mixed text so it cannot become a blanket
excuse.

**Date:** 2026-10-02

---

## D-030 — The graph is the orchestrator and owns no business logic

**Decision:** Every node in `app/graph/` does exactly one thing: read the state,
call one Phase 1-6 service, and return the partial state that service's output
implies. No rule, prompt, regex, threshold, weight or score is computed in a node.

**Alternatives:**

- *Put the pipeline logic in the graph and delete the services.* Rejected: it
  would make each stage untestable in isolation and force a rewrite of Phases 1-6
  to change the orchestration order.
- *Let nodes call helpers opportunistically.* Rejected: an orchestration layer
  that grows one "small" lookup is on a path to becoming a second implementation
  of a domain decision, with none of that domain's tests.

**Reason:** The phase boundary in this project is between *what was found* and
*what runs in what order*. Once those blur, the risk engine can no longer be
tested without a graph, and the graph can no longer be tested without real
engines. `tests/graph/test_graph_package.py` enforces the boundary mechanically:
it fails if any module under `app/graph/` imports a decision module
(`red_flag_rules`, `decision_engine`, `comparator`, `query_builder`,
`source_classifier`, `evidence_builder`, `risk_scoring`), reads a `risk_weight_*`
setting, or calls `.search(` outside the recorder.

**Date:** 2026-10-02

---

## D-031 — Phase 4's searches are recorded, not repeated

**Decision:** `RecordingSearchService` wraps the real `SearchService` and keeps
the responses Phase 4 retrieved. The graph reads that record and hands Phase 5
the per-claim grouping. The graph issues no search of its own.

**Context:** Phase 4's `VerificationService` performs the searches and discards
the `SearchResponse` objects, because nothing downstream asked for them. Phase 5's
`EvidenceService.build_all` needs them to know which query surfaced which
document. The two phases do not line up, and Phase 7 is where that becomes
visible.

**Alternatives:**

- *Have the graph run the searches itself and pass them to Phase 5.* Rejected on
  two counts. It duplicates every network call, and the second copy could rank
  differently from the first, which would put evidence in front of a
  verification that never saw it — the exact fabrication D-023 forbids. It would
  also make the graph a second search client, which the task explicitly rules out.
- *Change `VerificationService` to return its responses.* This is the cleanest
  long-term shape and would be the right change, but it edits a completed phase
  whose contract is pinned by its own tests. Phase 7 does not modify Phases 1-6.
  Recorded here as the preferred fix if Phase 4 is ever reopened.
- *Let Phase 5 run without results.* Rejected: evidence would be permanently
  empty, which is indistinguishable from "nothing was found".

**Reason:** Recording is a side effect of delegation rather than a separate step,
so there is no way to search through the recorder without the response being kept,
and no way for the graph to obtain a document that Phase 4 did not retrieve. The
mapping back to claims goes through `VerificationResult.queries`, which Phase 4
populates from the responses it actually received, so a query it never issued
cannot pull in a result. The only new inference in the module is that lookup.

**Date:** 2026-10-02

---

## D-032 — A recorded error ends the run; a recorded limitation does not

**Decision:** One predicate guards every stage: if any stage has recorded a
`GraphError`, the run stops. A `GraphWarning` never stops it.

**Context:** Phase 3-5 already convert real-world problems into *typed values* —
a missing key becomes `SEARCH_UNAVAILABLE`, a timeout becomes `SEARCH_FAILED`, an
uncheckable claim becomes `INSUFFICIENT_EVIDENCE`. Those are values, so they are
warnings and the pipeline continues. An exception escaping a service is different:
Phase 2 documents that `extract` never raises and Phase 4 documents that a
provider failure never propagates, so an escaping exception means a contract is
broken, not that the world is difficult.

**The bug this replaced:** the first wiring guarded only the input node and let
extraction, red-flag and verification failures be recorded and then walked past.
The observed result was the worst available behaviour: a run whose extraction had
raised still went on to produce a full `risk_assessment`, and a red-flag pass that
failed then reported "no patterns found". To any caller that reads as a complete
investigation. It understates risk, which is the one direction this product must
never err in.

**The one deliberate exception:** an evidence failure degrades rather than stops,
because Phase 6 established that evidence contributes no weight and only attaches
provenance ids. Losing it removes the explanation of an assessment without
changing the assessment. It is still recorded, still logged in full, and still
names the exception class. The asymmetry is documented at both the node and this
entry rather than left to be discovered.

**Reason:** The predicate tests for a *recorded error* rather than for known
failure codes, so a new failure mode added to a node is routed correctly by
default instead of being accidentally let through.

**Date:** 2026-10-02

---

## D-033 — State accumulates structurally; services are injected through a context

**Decision:** `InvestigationState` is a `TypedDict` whose `warnings`, `errors`
and `timeline` channels carry `operator.add` reducers. Services are not read from
globals: nodes receive a `GraphContext` through LangGraph's `context_schema`.

**Reason:** The reducers make appending a property of the schema rather than a
convention every future node must remember. Without them a node that returns one
warning silently discards the three already recorded — and a naive happy-path
test would not catch it, because the happy path never accumulates twice. The
injectable context is what keeps the state carrying investigation data only, never
a live service, which is what makes it serialisable and the nodes testable in
isolation. `build_investigation_graph()` compiles a fresh graph per call rather
than exposing a module-level instance, so one test's wiring cannot leak into
another's.

**Date:** 2026-10-02

---

## D-034 — Determinism is asserted on the semantic view, not on the clock

**Decision:** `semantic_view(state)` strips every field named in
`TIMESTAMP_FIELDS` — `started_at`, `completed_at`, `assessed_at`, `built_at`,
`retrieved_at`, `searched_at` — and reduces the timeline to stage/status/message
triples. `GraphContext.clock` is injectable.

**Context:** `RiskAssessment.assessed_at`, `EvidenceResponse.built_at` and
`SearchResult.retrieved_at` are stamped from real clocks by Phases 6, 5 and 3.
The graph cannot control them, so a whole-state comparison of two runs can never
be equal on a real pipeline. That is not the graph being non-deterministic; it is
timestamps being metadata.

**Alternatives:**

- *Freeze every phase's clock.* Rejected: it would mean editing Phases 3, 5 and 6,
  which this phase does not do.
- *Compare nothing involving time.* Rejected: the graph's own timestamps would go
  unchecked, and a clock that drifted between stages would go unnoticed.
- *Drop only the graph's timestamps.* Insufficient — the nested ones remain.

**Reason:** The timestamp names are listed rather than detected by type, so a
genuine finding that happens to be a datetime — a publication date carried on a
claim — is not silently discarded along with the metadata. With the clock frozen,
the graph's own stamps are asserted equal outright, and the nested ones are
compared through the semantic view. Both halves are tested.

**Date:** 2026-10-02

---

## D-035 — Only text is analysed in Phase 7; the other input types are refused

**Decision:** `InvestigationInputType` declares all four kinds the product
accepts. The input node analyses `TEXT` and refuses `URL`, `IMAGE` and `PDF` with
a typed `INPUT_TYPE_NOT_SUPPORTED` error and an `INPUT_TYPE_NOT_ANALYSED`
warning.

**Reason:** OCR, PDF and image ingestion are explicitly out of Phase 7's scope,
so there is no way to analyse those three honestly. The two failures available were
both bad: silently treating a file path or a URL as text would produce an
investigation of a string that is not the content, and an enum listing only `TEXT`
would quietly redefine the product's stated contract. Declaring all four and
refusing three states the real position. A test asserts that exactly one kind is
currently analysed, so starting to analyse another — without the ingestion work
that would require — fails there rather than producing an empty investigation that
reads like a clean one.

**Date:** 2026-10-02
---

## D-036 — Phase 8 returns the whole investigation synchronously; no job handle

**Decision:** `POST /api/investigations/text` and `POST /api/investigations` return
the complete `InvestigationResponse` with `200`. There is no `202`, no job handle,
and no `GET /api/investigations/{id}` in Phase 8.

**Context:** The original API spec sketched an asynchronous design: `202` with
`{investigation_id, status, created_at}`, then a `GET` to collect the result. That
design presupposes persistence, which is Phase 9. Implementing the `202` half
without the `GET` half would hand a client an id with nothing behind it and invite
a polling loop that can never succeed.

**Alternatives:**

- *Implement the `202` now, add the `GET` in Phase 9.* Rejected: it makes the
  interim API strictly less usable than the synchronous one, and a client built
  against it would have to be rewritten rather than extended.
- *Queue the work and return nothing.* Rejected: there is no worker yet, and
  inventing one here would scope-creep into Phase 11.
- *Return the state synchronously and add the `GET` alongside it later.* Chosen —
  adding retrieval is purely additive. A client that persists nothing still works
  unchanged when Phase 9 lands.

**Reason:** The response is already complete when the `200` is written, so calling
it a `202` would be a lie about work that has already finished. Keeping the
endpoint synchronous also keeps the honesty property testable in one place: a
degraded run is a `200` carrying `status: PARTIAL`, not a status code a client has
to decode.

**Date:** 2026-10-02

---

## D-037 — Run status is derived from the stage timeline, never from the warning list

**Decision:** `investigation_status()` returns `FAILED` if the state recorded any
`GraphError`, `PARTIAL` if any `TimelineEvent` has status `PARTIAL` or `SKIPPED`,
and `COMPLETED` otherwise. The accumulated `GraphWarning` list does not
participate.

**Context:** `NO_RED_FLAGS_DETECTED` is recorded whenever content matched no Phase 1
rule — which is most benign content. Deriving status from "are there warnings?"
would report every clean investigation as a degraded run, and a UI would then show
a limitation badge on an investigation that had checked everything it set out to
check. The inverse error is worse in a different way: a client that saw only
`COMPLETED` and an empty `limitations` array could not tell a complete run from a
run that skipped verification, which is the confusion D-006 exists to prevent.

**Alternatives:**

- *Status is `PARTIAL` whenever any warning exists.* Rejected: it conflates "we
  looked and found nothing" with "we could not look".
- *Filter the warnings to a hand-picked subset of codes.* Rejected: the API layer
  would be re-deciding what the graph already classified. Any future warning code
  would need a second edit here or it would be silently misreported.
- *Ask the graph to emit a status field.* Deferred: it would need a Phase 7 change,
  and the timeline already carries exactly the information required.

**Reason:** The graph already made the classification when it wrote each stage's
`TimelineStatus` — that is what the status was for (D-032's `PARTIAL` vs
`COMPLETED` split). The adapter reads it rather than re-deriving it, which keeps
every judgment about what counts as degraded inside the graph. `limitations` still
carries the full deduplicated code set, so a client that *wants* to badge a clean
result can read `NO_RED_FLAGS_DETECTED` there; the choice of what to surface is the
client's, and this layer does not make it silently.

**Date:** 2026-10-02

---

## D-038 — The API embeds domain models rather than re-projecting them

**Decision:** `InvestigationResponse` embeds `Claim`, `Entity`, `RedFlag`,
`VerificationResult`, `EvidenceResponse` and `RiskAssessment` directly. The API
layer adds only the fields with no domain equivalent: `status`, `limitations`,
`warnings`, `errors`, `current_stage`, `language` and the timestamps.

**Context:** A projection would let the API contract evolve independently of the
domain — which is the usual argument for it. The cost is a second definition of
"a claim" that can drift from Phase 2's, and every such drift is a silent wrong
answer rather than a type error, because both shapes would validate.

**Alternatives:**

- *Project every object into a flat response DTO.* Rejected: Phase 2 and Phase 4
  already own their field semantics and their validation. A projection would restate
  them, and a field added for a real reason would have to be added twice.
- *Return the raw `InvestigationState`.* Rejected on stability rather than taste: it
  exposes `extraction`, the internal search bookkeeping and other fields a client
  has no use for, and any change to Phase 2's model would become a breaking API
  change.
- *Embed the domain models, expose only what has no domain equivalent.* Chosen.

**Reason:** The split follows the existing evidence: Phase 10's report will need the
full `Claim` and `RiskAssessment` detail, so anything thinner would be discarded and
re-fetched in the same release that introduces it. `RiskAssessment` in particular is
forwarded untouched, caveat included — a heuristic indicator count must not acquire
a second, looser wording on its way out to a client (D-025).

**Date:** 2026-10-02

---

## D-039 — The API layer interprets; it never computes a score, threshold or verdict

**Decision:** Every judgment in the investigation — extraction quality, red-flag
presence, verification status, evidence provenance, risk score and level — is
passed through from the phase that owns it. The API layer derives exactly two
things: the top-level `status` (D-037) and the deduplicated `limitations` codes.
Neither is a finding about the content.

**Context:** The route layer sits directly above services that hold every rule in
the system, which makes it the most convenient place for a rule to creep in and the
least visible. A "helpful" `high_risk` boolean, a `summary` field or a
`why_flagged` string would each pass every existing test while quietly becoming a
verdict the product's vocabulary bans.

**Alternatives:**

- *Add a report-oriented convenience field and mark it as presentation.* Rejected:
  the field outlives the comment, and a client that renders it has no way to know it
  was never validated against the pipeline.
- *Let the API own a "not financial advice" banner.* Rejected: that is Phase 10's
  job, and duplicating it here would create two places to keep in step.

**Reason:** The ban is enforced, not merely intended. `tests/graph/test_graph_safety.py`
and `tests/test_risk_safety.py` still assert the vocabulary, and `tests/api/` adds
the same tripwire at the response boundary — the keys of a real
`InvestigationResponse` are checked against the Phase 6 assessment's own fields, so
a field that appears at one level and not the other fails the suite rather than
shipping.

**Date:** 2026-10-02

---

## D-040 — A surrogate key owns identity; the content digest is not unique

**Decision:** Every table is keyed on a surrogate autoincrement `id`. The Phase 7
`investigation_id` (an `inv_<16 hex>` content digest) is stored in an ordinary
indexed `public_id` column with no uniqueness constraint, and the same digest is
also stored in `content_hash`. `GET /api/investigations/{id}` returns the most
recently stored run under that id.

**Context:** `investigation_id_for` digests the input type and the submitted text,
so two runs of identical content produce the same id *by construction*. Phase 8
returned that id in every response and flagged the collision as Phase 9's problem.
Making it unique would mean the second run of a piece of content could not be
stored at all, and overwriting the first would erase the only evidence that the
investigation was ever run twice — which is precisely the information the history
endpoint exists to provide.

**Alternatives:**

- *Make `public_id` unique and reject the second run.* Rejected: it converts a
  normal, expected user action (checking the same suspicious text again) into an
  error, and the user learns nothing from it.
- *Upsert, overwriting the earlier run.* Rejected: same problem, silent rather than
  loud. The history would show one row for content that was checked twice, so
  "was this checked before?" becomes unanswerable in the one case where it matters.
- *Key on a per-run UUID and change the id the API returns.* Rejected as a
  breaking change: clients written against Phase 8 already store the digest.
- *Surrogate `id` primary key, digest preserved and non-unique, newest wins on
  retrieval.* Chosen.

**Reason:** The digest is genuinely useful — it is what makes two runs comparable
field by field, and it is what lets a client detect that it has seen this content
before. It is simply not a key. Keeping the value the client already holds while
giving the database its own identity satisfies both, and the history endpoint lists
every run so nothing is hidden by the choice.

**Consequence:** a client cannot ask "give me *that specific* second run" through
`GET /{id}`. It can see the run in the history. Recorded as a limitation rather
than solved with a second id space, because a second identifier the client must
also learn is a worse trade than a listing endpoint.

**Date:** 2026-10-02

---

## D-041 — The repository returns an `InvestigationState`; the adapter shapes both paths

**Decision:** `InvestigationRepository.load()` returns a reconstructed
`InvestigationState` — the same type `run_investigation` returns — not a bespoke
persistence result type. Both the `POST` and the `GET` route then hand it to the
same Phase 8 `serialize_investigation`. There is exactly one code path that shapes
an investigation for a client.

**Context:** The obvious alternative is a `StoredInvestigation` record with its own
to-response mapping. It would be a little more direct, and it would *appear* to
guarantee that `GET /api/investigations/{id}` and `POST /api/investigations`
agree. That guarantee is exactly the thing that erodes: the two mappers are
separate code, so a field added to one is not added to the other, and the drift is
invisible until a client compares a stored investigation against the live one and
finds a field missing.

**Alternatives:**

- *Return domain models, assembled by the repository.* This is the same choice one
  layer up: the repository's job ends at producing the Phase 1-6 objects, and the
  adapter's job begins at producing the HTTP body. Both are reused verbatim.
- *Return a persistence-specific record with its own adapter.* Rejected: two
  renderers for one concept.
- *Store the already-serialised response as JSON.* Rejected: it freezes the
  response shape at write time, so a schema fix would require re-running every
  stored investigation to take effect, and the database would hold a second,
  divergent copy of every judgement in the system.

**Reason:** The guarantee "a retrieved investigation is indistinguishable from the
live one" is the entire value of the retrieval endpoint, and the cheapest way to
have it is to have only one way to build a response. The cost is that the
repository must reconstruct the Phase 1-6 models faithfully, which is real work —
but it is work whose correctness the round-trip test measures directly
(`semantic_view(reloaded) == semantic_view(live)`), rather than work whose
correctness depends on two things agreeing.

**Date:** 2026-10-02

---

## D-042 — Order is stored, never inferred from a value the database already holds

**Decision:** Every collection the state carries as an ordered tuple has an
explicit `sequence` column. Evidence source order is additionally preserved by
`evidence_responses.source_refs_json`, an ordered list of `[source_id, result_id]`
pairs rather than a join.

**Context:** Phase 5's evidence ordering is a deterministic function of the
evidence's own fields, and Phase 7's state carries tuples throughout. A relational
store has no way to reproduce that order from the values it holds, and the two
obvious substitutes are both wrong. Ordering by timestamp fails outright, because
two stages can emit events in the same millisecond. Ordering by id fails more
quietly, because the ids are content digests with no relationship to the order
Phase 5 chose.

`EvidenceResponse.sources` is the sharper case. `distinct_sources` keeps the
*first* result seen for each document within a claim, while the `sources` table
holds every retrieval. Deriving the response's list from the table would change
which `result_id` a document is reported under — and since `ev_` ids hash both
ids, the stored evidence would then carry an id that no longer matches the
provenance shown beside it.

**Alternatives:**

- *Rely on `ORDER BY` over an indexed column.* Rejected: the order is not a
  function of any one column, so any single-column choice is arbitrary.
- *Recompute Phase 5's ordering on read.* Rejected: that is a second
  implementation of `order_evidence`, free to disagree with the first, and the
  disagreement would be invisible.
- *Store the order.* Chosen.

**Reason:** Order is data here, not presentation. A retrieved investigation that
lists the same findings in a different sequence than the live one would be correct
in every field and wrong in the way a reader notices first.

**Date:** 2026-10-02

---

## D-043 — No `users` table and no `reports` table until something needs them

**Decision:** Phase 9 creates neither a `users` table nor a `reports` table, both
of which the Phase 0 schema sketch described. The Phase 6 assessment data the
sketch put in `reports` is stored in `risk_assessments` and `risk_factors` instead.

**Context:** The sketch was written before the Phase 1-6 schemas existed, and
several of its tables describe a product that has not been designed yet. Building
to it would have meant inventing a second claim, a second entity and a second risk
assessment, each able to disagree with the model it was derived from.

An empty placeholder table is worse than no table, because it looks decided. A
`reports` table with the Phase 6 columns on it would also pre-empt Phase 10's real
question — whether a report is a rendered artefact, a stored snapshot, or both —
by answering it with a schema.

**Alternatives:**

- *Create `users` with nullable columns "for forward compatibility".* Rejected: no
  authentication exists, nothing would ever write a row, and a `user_id` column
  that is always `NULL` invites a query that filters on it and returns everything.
- *Create `reports` with the Phase 6 columns, 1:1 with an investigation.* Rejected:
  it is Phase 10's decision, and the trace data Phase 10 will need is already
  correctly stored in two tables.
- *Leave both out until they are needed.* Chosen.

**Reason:** A schema is a record of decisions that have been made. An empty table
records a decision nobody made, and the cost of removing one later is a migration
against data that does not exist yet — the cheapest migration there is, which is
precisely why it should be taken now.

**Date:** 2026-10-02

---

## D-044 — The risk caveat is reproduced by the model, not stored in its own column

**Decision:** `risk_assessments` has no `caveat` column. The caveat lives in
`warnings_json`, and the repository rebuilds `RiskAssessment` through the model,
which appends `SCORE_NOT_A_PROBABILITY` during validation.

**Context:** The caveat is the one string in the system that stops `risk_score`
being read as a probability (D-025). It is worth protecting carefully, which makes
a dedicated column look like the careful choice. It is the opposite: `SCORE_NOT_A_PROBABILITY`
is a module constant in `app/schemas/risk.py`, owned by the schema. A column would
be a second copy of that string, free to drift from the one the model emits, and
the drift would be invisible — the caveat would still *look* present.

Storing the model *decides* to append it, which is stronger than storing it. A
stored caveat could be wrong; a rebuilt assessment cannot produce a wrong one,
because it goes through the same validator that produced the original.

**Alternatives:**

- *A `caveat` column, read back verbatim.* Rejected: two copies, one owner.
- *A boolean `caveat_present` flag.* Rejected: worse — it records that a caveat was
  mentioned without recording what it says, which is the one thing a report needs.
- *Rebuild the assessment through the model and let the model re-add the caveat.*
  Chosen.

**Reason:** The strongest guarantee available is that the invariant is structural
rather than maintained. There is no code path that can store an assessment without
its caveat, and no code path that can read one out without it.

**Date:** 2026-10-02

## D-045 — Network isolation is enforced, and the guarantee is "nothing leaves the machine"

**Decision:** `tests/network_guard.py` wraps four socket entry points and is
installed by an autouse fixture in `tests/conftest.py`. Loopback literals are exempt.
Tests marked `@pytest.mark.integration` are exempt. The stated guarantee is that no
traffic leaves the machine.

**Context:** Until Phase 10 the suite was offline by convention: every settings
factory passed `_env_file=None` and every provider was a fixture. That is a
guaranteance that decays on its own. One new test constructs `Settings()` the
ordinary way, the developer has a real `GROQ_API_KEY` in `.env`, and the test makes
a live call — which will usually still *pass*, because a real provider returns
plausible data. That is the failure worth making impossible: a test silently
succeeding on someone else's API key.

Writing the guard exposed how weak the original claim was. `network_guard.py`
defined an `autouse` fixture and `conftest.py` imported the module, but an `autouse`
fixture is only collected from a conftest or a plugin, so **the guard had never once
blocked a connection.** The suite was green either way, and nothing in 2085 tests
noticed. A test now attempts a connection from inside an ordinary test and requires it
to be blocked, which is the only kind of test that can catch this.

**Alternatives:**

- *A warning instead of an exception.* Rejected: a warning is missed, and a
  ``NetworkAccessBlocked`` exception names the target so the offending test is
  fixable from its own failure output.
- *Blocking loopback too.* Rejected, and not on principle: `TestClient(app)` as a
  context manager starts an anyio blocking portal, and Windows has no
  `socketpair`, so CPython emulates it with a real TCP connection to `127.0.0.1`.
  Blocking it breaks every API test on Windows for a reason unrelated to isolation.
  Only *literals* are exempt, so nothing hides behind a name — `localhost` still has
  to resolve, and resolution is still blocked. A hostname beginning with `127.` is
  not exempt; a test pins that, because the obvious prefix check would allow
  `127.0.0.1.example.invalid`, which anyone can register.
- *Patching at the C level to catch `psycopg2`.* Rejected: libpq does its own DNS
  and TCP and never enters Python's `socket` module, so a Python-level guard cannot
  see it by construction. Recorded as a limit instead of a defect, and the reason the
  suite's poison DSN points at loopback port 1 rather than a public host.
- *Leaving it to integration marks alone.* Rejected: the default suite must be
  offline whether or not anyone remembered a marker.

**Reason:** "The tests do not hit the network" is a property of the harness, not of
the tests. Stating the guarantee as "nothing leaves the machine" is both true and
checkable, and it is narrower than "no sockets at all" in a way that matches what
the suite actually needs.

**Date:** 2026-10-02

## D-046 — The safety vocabulary is enforced at every boundary text crosses

**Decision:** The Phase 6 judgement-and-advice ban is re-asserted over the adapter,
the JSON response, the database, the `GET` response, the history entry, error bodies
and the OpenAPI document — not only over the risk engine's own output.

**Context:** `tests/test_risk_safety.py` was written in Phase 6 and proved the
*engine* cannot emit a verdict. Phase 10 added two layers that re-emit text it
produced: the Phase 8 adapter and the Phase 9 repository. Either could pass an
assessment through as a projection and lose `warnings`, which is where
`SCORE_NOT_A_PROBABILITY` lives — leaving a *retrieved* investigation without the
caveat while every engine test still passed. A guarantee that holds at one layer and
is unverified at the next is a guarantee waiting for the layer that quietly breaks
it.

The machinery moved from the test module to `tests/vocabulary.py` rather than being
copied, because two matchers free to disagree about what counts as a violation would
make the whole exercise decorative.

**Alternatives:**

- *Trusting the engine test to cover the boundaries.* Rejected: it cannot, by
  construction. It never constructs an adapter or a repository.
- *Copying the matcher into a new test module.* Rejected: two definitions of a
  violation, free to drift.
- *Scanning every response string for banned words.* Rejected as unsound: a
  scammer's own message contains the word "scam", and reproducing it is the product
  working. Quoted evidence and submitted content are excluded, and the exclusion is
  itself tested.

**Reason:** Text that leaves the engine passes through adapters, a serialiser, a
database and a response model. Each is a place a caveat can be dropped, and each
needs the check, not just the first one.

**Date:** 2026-10-02

## D-047 — Leak-safety is proven by planting the secret, not by reading the handler

**Decision:** Security tests inject a real-shaped secret into a genuine failure and
assert it does not reach a response. They do not assert on the source of a handler.

**Context:** Leak paths are not where they look. A DSN in a SQLAlchemy message does
not arrive through the handler that formats SQLAlchemy errors; it arrives because the
message is logged, the log record attaches to the exception, and a *different* handler
renders it. A submitted password does not arrive through the validation handler that
strips values; it arrives through the handler that builds the envelope and then falls
through to a generic path. Reading a handler proves what it does; it cannot prove what
it is not handed.

This is not theoretical here. The form-encoded 422 handler really did crash, and it
crashed *inside* the error path, with a credential in the payload. Only an executed
test found it.

**Alternatives:**

- *Assert that a handler's source contains no f-string of `exc`.* Rejected: proves
  nothing about the exception actually being raised.
- *Snapshot the full error envelope for one failure mode.* Rejected: the envelope was
  already covered; the risk is in what reaches it, not its shape.
- *Mock the framework.* Rejected: the form-body defect lived in the interaction
  between Starlette and Pydantic, which a mock would have removed along with the
  bug.

**Reason:** The question is not "does this code look careful" but "can this string
reach the client". Only the second is testable.

**Date:** 2026-10-02

## D-048 — Query cost is asserted as flatness in the data, not as a magic number

**Decision:** N+1 regressions are detected by comparing the statement count for one
stored run against the count for twenty, not by asserting a fixed number of
statements.

**Context:** Phase 9 denormalises seven counts onto the `investigations` row and
loads every relationship with `lazy="selectin"`, so a listing costs a fixed number of
statements regardless of history size. Asserting that number outright would encode
it, and the next legitimate query would break the test — at which point the
pressure is to raise the threshold, which is how an N+1 test quietly stops working.

Flatness catches the failure that matters, cost that *scales with the data*, and
nothing else. Merging the count and the page into a window function is an
improvement; flatness would not object, and only the budget test would, which is the
right friction.

**Alternatives:**

- *Assert an exact statement count.* Rejected: brittle, and invites the threshold
  ratchet described above.
- *Time the endpoint.* Rejected: a wall-clock assertion is flaky on shared CI and
  measures the machine rather than the query.
- *A benchmark suite.* Rejected: Phase 10 has no performance budget, and a benchmark
  with no budget is a number nobody acts on.

**Reason:** The property worth protecting is independence from data size. It is also
the only one that stays true as the schema grows, which a magic number does not.

**Date:** 2026-10-02

## D-049 — `completed_at` stays `null`; the pipeline will not invent a time it never measured

**Decision:** `completed_at` remains absent from `InvestigationState` and renders as
`null` in the API. Phase 10 records it as correct behaviour and corrects the
documentation rather than fixing the code.

**Context:** Phase 7 declared the field and no node has ever written it. Phase 9
persists it faithfully, so the column exists, the response exposes it, and the value
is always `null`. The project documentation had described it as populated, and
`CURRENT_STATE.md` separately claimed a retrieved investigation was "byte-identical"
to the live one. Both claims outran the code.

**Alternatives:**

- *Stamp `completed_at` in the last node to run.* Rejected: a run whose last stage
  failed never reaches a completion node, so the field would be populated exactly
  when it is least meaningful. A clock reading at storage time is worse still â€"
  it measures when the row was written, not when the work finished.
- *Derive it from `started_at` plus the last timeline entry.* Rejected: that is an
  inference presented as a measurement.
- *Remove the field from the schema.* Rejected for now: a client written against
  Phase 8 expects the key. Removing it is a breaking change to a documented contract,
  and the honest fix is to populate it when a node can measure it.

**Reason:** A completion time that the pipeline did not measure would be a fabricated
fact in the one field a client is most likely to use for reporting. `null` says
"unknown" and is correct; a number would say something the system does not know.

**Date:** 2026-10-02

## D-050 — Input-mode availability is read from the API, not hardcoded in the frontend

**Decision:** The Investigate page drives each input mode's enabled state from
`GET /api/investigations/limits` → `supported_input_types`, defaulting to Text only when
that call has not succeeded. The phase labels ("Coming in Phase 12/13/14") are the only
static mapping in the UI.

**Context:** The four input modes are a product roadmap, and Text is the only one the
backend processes. The obvious implementation is a hardcoded `TEXT` branch, which works
until the backend gains a mode and the UI keeps calling it unavailable — or gains none and
the UI still offers it. The failure is silent in both directions.

**Alternatives:**

- *Hardcode `mode === "TEXT"`.* Rejected: two sources of truth that drift, with no signal
  when they do.
- *Let the user select a mode and let the API refuse it.* Rejected: a visible control that
  reliably fails is worse than one that is honestly unavailable, and it teaches the user to
  expect errors.
- *Hide the unimplemented modes entirely.* Rejected: the roadmap is part of the product
  story and the task requires the surfaces to be present and clearly labelled.

**Reason:** The API already publishes its own accepted input types. Reading them makes the
UI incapable of claiming a mode works when the backend has stopped accepting it. The phase
labels stay static because they are a schedule, not a capability.

**Date:** 2026-10-02

## D-051 — No chart where the API provides no data; say so in the interface

**Decision:** The dashboard shows no risk-distribution chart. Recharts is used in exactly
one place — risk contribution by severity, computed from `risk_assessment.factors[]` on the
report page. Where the list API cannot support a chart, the UI states the reason in
prose instead of drawing an approximation.

**Context:** `InvestigationSummaryResponse` carries no risk level, so a dashboard risk
distribution would have to be invented, estimated, or aggregated from data the endpoint does
not return. All three were available and all three were rejected.

**Alternatives:**

- *Derive a distribution from per-record counts.* Rejected: `factor_count` and
  `red_flag_count` are not risk levels, and mapping them onto risk bands would be a
  fabricated statistic wearing a real one's name.
- *Drop the section silently.* Rejected: a reader would wonder whether the product simply
  lacks the feature, when the honest answer is that the list endpoint does not expose it.
- *Call the detail endpoint per record.* Rejected: N+1 requests over the whole history to
  reconstruct a view the contract deliberately keeps cheap.

**Reason:** A dashboard is where invented numbers do the most damage, because they are read
as measurements. Explaining the gap costs one sentence and preserves the claim that every
dynamic figure on screen came from the API.

**Date:** 2026-10-02

## D-052 — No backend change in Phase 11; the frontend adapts to the API

**Decision:** Phase 11 modifies no file under `backend/`. Every gap between the plan and the
API was closed on the frontend side, and `frontend/src/types/api.ts` was generated from the
running application rather than transcribed from documentation.

**Context:** The task anticipated a possible integration mismatch and set a decision order:
inspect the actual schema, prefer adapting the frontend, change the backend only if the
contract is objectively broken, and never silently change a contract. Investigating the live
application first — rather than reading `API_SPEC.md` — mattered, because the prose and the
real enums do not fully agree: `ClaimType` has 14 members and `EntityType` 19, several of
which the document's tables do not enumerate. Building types from the code avoided baking
those omissions into the UI.

**Alternatives:**

- *Write types from `API_SPEC.md`.* Rejected: verified to understate the enums, which would
  have produced a type that rejected valid server data.
- *Widen the OpenAPI enum to match observed values.* Rejected as out of scope: the enum is
  correct in code, the prose is merely incomplete, and a schema edit is a contract change
  that belongs to its own phase with its own decision record.
- *Add the missing endpoints the UI wanted.* Rejected: that is Phase 12–14, not Phase 11.

**Reason:** Keeping the phase boundary honest means the API contract has exactly one owner.
The frontend consumed it as written, and the documentation gap it exposed is recorded here
rather than patched silently.

**Date:** 2026-10-02

## D-053 — `strictPort` on the dev server, because CORS names one port

**Decision:** The Vite dev server is pinned to port 5173 with `strictPort: true`.

**Context:** The backend's `cors_origins` default names `http://localhost:5173` and
`http://127.0.0.1:5173` and nothing else. Vite's default behaviour when 5173 is busy is to
bind the next free port and keep running. The app then loads, every request fails, and the
browser reports it as an opaque CORS error — a failure that points at the wrong layer
entirely. This was observed directly: with the server on 5174, the preflight returned `400`
and no `access-control-allow-origin` header.

**Alternatives:**

- *Leave the default fallback.* Rejected: produces a confusing failure from a common,
  unremarkable cause.
- *Widen the backend's CORS allow-list.* Rejected: relaxing a security control to suit a
  dev-server default inverts the correct order of authority, and Phase 11 must not change
  backend behaviour.
- *Proxy `/api` through the dev server.* Rejected for now: it hides the base URL that
  `VITE_API_BASE_URL` is specified to configure, and adds a layer the task did not ask for.

**Reason:** A loud failure at startup is far cheaper than a silent one at runtime. The
developer is told immediately that the port they need is taken, instead of discovering later
that the browser is blocking requests for a reason the error deliberately hides.

**Date:** 2026-10-02

## D-054 — URL analysis fetches with the stdlib, guards with an allowlist, and splits faults 422/502/503 by whose mistake they are

**Decision:** `POST /api/investigations/url` is built on three
services — `UrlGuardService` (may we try), `UrlFetchService` (did it
work, stdlib `http.client` only), `WebsiteExtractor` (what did we
get) — and every failure is classified before it is answered: a
caller's bad destination is `422`, a site that failed is `502`, a
fetch capability switched off by configuration is `503`, and an
unreadable page is a `200 PARTIAL` with `PAGE_TEXT_NOT_RETRIEVED`.

**Context:** The endpoint dereferences a user-supplied URL from the
server, which is the one request path where the *caller* can point the
server's own connections at an address the caller chooses. The existing
fault philosophy (D-009, D-039) says the status code answers whose
fault it is — but a URL adds a fourth category the earlier phases never
had: a *site* that is down is neither the caller's mistake nor ours,
and answering it `500` would claim a defect that does not exist, while
answering it `200 PARTIAL` would claim an investigation that never
started. `502` names that category.

**Alternatives:**

- *`httpx` for the fetch.* Rejected: the dependency already exists for
  other services, but `http.client` needs no new import surface for a
  *security boundary* — fewer moving parts inside the SSRF path, and
  redirects can be handled one hop at a time with explicit
  re-validation between hops, which a redirect-following client hides.
- *A blocklist of dangerous ranges.* Rejected: a blocklist must be
  complete to be safe, and "complete" is a moving target (IPv6 unique
  local, carrier-grade NAT, future metadata addresses). The policy is
  therefore an allowlist — global unicast space only — so an unknown
  range is refused by default.
- *Refuse by hostname.* Rejected: hostnames are attacker-controlled
  strings; `localhost` is just a name, and `ip-127-0-0-1.sslip.io`
  resolves to loopback while looking like a public host. The guard
  resolves the name itself and applies the address predicate to every
  result, and again to every redirect target.
- *Fetch errors as `500`.* Rejected: a DNS failure is not our defect;
  dressing it as one would make every dead link look like a platform
  outage.

**Reason:** The taxonomy is what makes the endpoint honest. A user who
submits an internal address is told their submission was refused; a user
who submits a dead site is told the site failed; a deployment with
fetching disabled is told the capability is off; and a page that
fetched but contained no readable text still gets an investigation with
a named limitation. Each of those is a different truth, and collapsing
any two of them would mislead a client that branches on the status.

**Date:** 2026-10-03

## D-055 — Fetched page text is untrusted data, never trusted as text

**Decision:** Text extracted from a fetched page is treated exactly
like user-submitted text: it flows into the pipeline as *data*, the
extraction prompt (v2) frames it as untrusted content to describe, and
nothing in the pipeline interprets it as an instruction, a verdict on
the host, or a finding about legitimacy. `UrlSource` fields are
chosen so a value cannot be read as a judgement — `page_title` is
what the page called itself, `is_https` is a transport fact.

**Context:** Phase 12's whole output is "claims a page makes about
itself". A page can name its own title, describe its own registration
and publish any claim — including claims about InvestShield, or prompt
text aimed at an LLM that might summarise the page. The pipeline
already refuses to trust generated text (D-015) and never emits a
verdict (D-016, D-020); a fetched page is the same kind of untrusted
input as a submitted message, one step earlier.

**Alternatives:**

- *Score the domain.* Rejected: "is this host a scam" is exactly the
  judgement D-006 and D-020 forbid without authoritative evidence, and
  no authoritative source for domain reputation is wired in. The
  pipeline analyses claims *in* the page and says so.
- *Store the raw HTML.* Rejected: nothing downstream reads it, and
  keeping attacker-controlled bytes in the database would widen the
  stored-XSS surface for no reader. Visible text is extracted and the
  markup discarded.
- *Trust the page's self-description.* Rejected: `page_title` and
  `meta_description` are recorded as facts the page published, and
  nothing downstream treats them as verified.

**Reason:** The guarantee a client needs is that a hostile page cannot
do more than supply content to be analysed. Keeping the fetch record
descriptive and the extraction framing unchanged preserves that
guarantee by construction, and the vocabulary tests enforce it at the
API boundary like every other text the pipeline touches (D-046).

**Date:** 2026-10-03
