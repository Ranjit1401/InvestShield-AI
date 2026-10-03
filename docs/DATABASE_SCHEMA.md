# DATABASE_SCHEMA — InvestShield AI

ORM: SQLAlchemy 2.x (declarative), models in `backend/app/models/`, mapping in
`backend/app/repositories/`. Local default: `sqlite:///./data/investshield.db`.
Portable to PostgreSQL/Neon by changing `DATABASE_URL` only (D-001, D-028).

This document describes the schema **as implemented in Phase 9**. It was
rewritten because the earlier sketch described fields the pipeline does not have
and a table for a phase that has not been built; every discrepancy is recorded in
[Corrections to the earlier sketch](#corrections-to-the-earlier-sketch) rather
than quietly dropped.

Column types are deliberately portable — no `JSONB`, `ARRAY`, `UUID`, or
PostgreSQL-only defaults. JSON-shaped payloads use SQLAlchemy `JSON`. Timestamps
use the `UtcDateTime` decorator, which is the one non-core type in the schema.

---

## Design Rules

Four rules decide every table below. They are worth stating first, because most
of the schema's less obvious choices follow from one of them.

**Normalise what is referenced; JSON what is only read.** Claims, entities, red
flags, verification results, sources, evidence, factors and timeline entries are
rows: they are counted, filtered, joined to, and a reader must be able to walk
from a risk factor to the claim and document behind it (D-026). Free-form bags
like `Claim.metadata`, `RiskWeights` and a factor's id lists are JSON, where a
table would add joins without adding meaning.

**Store the object, not a paraphrase of it.** Round-tripping a run must
reproduce the Phase 1-6 models exactly, including their computed fields. That is
why `red_flags` stores `span_start`/`span_end`/`matched_text`: Phase 6 mints its
`rf_` id from precisely those three values, so dropping any of them would change
the ids a reader could follow.

**Never re-derive an id.** `inv_`, `claim_`, `entity_`, `src_`, `res_`, `ev_` and
`rf_` are all content digests minted by Phases 4-7. They are written and read
back unchanged. Re-running any of those derivations at storage time would be a
second implementation that could disagree with the first, and a disagreement would
mean an id in a stored investigation no longer matches the id a reader recomputes.

**Never persist anything a user might read that could carry a secret.** Every
string here comes from a schema field or fixed wording. There is no exception
message column on `investigation_warnings`, no stack-trace column, and no DSN
column anywhere — the safest place to enforce that is not having the column
(D-024, and the same principle as Phase 8's `probe_database` fix).

---

## Entity Relationship Summary

```
investigations (1) ──┬──< claims ──────< evidence >────── sources
                      │       │             │
                      │       │             └── (source_id, result_id)
                      │       └───────────── (claim_id)
                      ├──< entities
                      ├──< claim_entity_links >── claims, entities
                      ├──< red_flags
                      ├──< verification_results
                      ├──< evidence_responses ──< evidence
                      ├──< risk_factors
                      ├──(0..1) risk_assessments
                      ├──< timeline_events
                      ├──< investigation_warnings
                      └──< investigation_errors
```

`investigations.id` is the only unique identity in the schema. Every other
uniqueness rule is scoped to it, which is what makes investigating the same
content twice a second event rather than a constraint violation.

There is **no `users` table and no `reports` table** — see
[Corrections](#corrections-to-the-earlier-sketch).

---

## investigations

One row per run.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | Integer | not null | **PK**, autoincrement. Never exposed over the API. |
| `public_id` | String(64) | not null | Phase 7 `inv_<16 hex>`, indexed. **Deliberately not unique** — see below. |
| `content_hash` | String(64) | not null | The same digest, isolated, indexed. |
| `input_type` | String(16) | not null | `TEXT` \| `URL` \| `IMAGE` \| `PDF` |
| `raw_input` | Text | not null | Exactly what the caller submitted. Red-flag spans index this string. |
| `extracted_text` | Text | not null | The text Phase 2 worked from. |
| `language` | String(8) | not null | Report language; recorded, not yet honoured. |
| `status` | String(16) | not null | `COMPLETED` \| `PARTIAL` \| `FAILED`, indexed. |
| `current_stage` | String(32) | not null | Last stage that ran. |
| `extraction_mode` | String(16) | nullable | `LLM` \| `FALLBACK` \| `PARTIAL` |
| `prompt_version` | String(64) | nullable | Phase 2's prompt version. |
| `model_name` | String(128) | nullable | Phase 2's model. |
| `extraction_source_text` | Text | not null | Phase 2's `source_text`. |
| `extraction_normalized_text` | Text | not null | Phase 2's `normalized_text`. |
| `extraction_warnings` | JSON | not null | Phase 2's `processing_warnings`. |
| `verification_warnings` | JSON | not null | Phase 4's batch-level `VerifiedResponse.warnings`. |
| `source_metadata` | JSON | not null | Phase 12 `UrlSource.as_metadata()` for URL runs; `{}` for text runs. |
| `claim_count` … `factor_count` | Integer | not null | Seven denormalised counts, default 0. |
| `started_at` | DateTime | not null | Run start. |
| `completed_at` | DateTime | nullable | Run finish, if recorded. |
| `created_at` | DateTime | not null | When the row was written. Indexed. |

Indexes: `ix_investigations_public_id`, `ix_investigations_content_hash`,
`ix_investigations_status`, `ix_investigations_created_at`,
`ix_investigations_status_created` (`status`, `created_at`).

### `public_id` is not unique, on purpose

`investigation_id_for` digests the *submitted content*, so two runs of the same
text produce the same `public_id` by construction. Making it unique would mean:

- the second run of a piece of content could not be stored at all, and
- overwriting the first would destroy the only evidence that the investigation
  was ever run twice.

So `id` is the primary key and `public_id` is an ordinary indexed column.
`GET /api/investigations/{id}` returns the **most recently stored** run under
that id, so a client that posts and then retrieves always sees the run it just
caused. The full history is available from `GET /api/investigations`, which lists
every run.

### The denormalised counts

Seven counts are stored on the run rather than aggregated on read, so the history
endpoint can list a hundred runs with one indexed query instead of a thousand
joins. A count that disagrees with its children is worse than no count, so the
agreement is pinned by a test in `backend/tests/db/test_round_trip.py`, and the
agreement is also checked from the outside — that the number on a history row equals
the length of the collection in the retrieved run — by
`test_the_counts_on_a_list_row_agree_with_the_retrieved_run` in
`backend/tests/api/test_query_efficiency.py`.

Phase 10 also pinned the *cost* of the listing, which is the reason the counts are
denormalised at all. A count query, a page query, and one per child collection
(every relationship is `lazy="selectin"`) — a total independent of how many runs are
stored. `backend/tests/db/test_query_efficiency.py` asserts that flatness by
comparing one stored run against twenty rather than by asserting a fixed statement
count, so the next legitimate query does not break the test and the next *per-row*
query is caught immediately (D-048).

### `completed_at` is currently always `NULL`

Phase 7 declares `completed_at` on the state and the Phase 8 response exposes it,
but **no graph node writes it**. The key is *absent* from `InvestigationState` — not
present-and-null — and the adapter renders it as `null`. This is a Phase 7 gap, not a
Phase 9 or Phase 10 one, and it is recorded here rather than papered over: storage
preserves whatever the state carries, including the absence, so the round trip stays
exact. Inventing a finish time at storage time would be a claim the pipeline never
made (D-049).

Pinned by `test_completed_at_is_still_null_after_a_round_trip` in
`backend/tests/api/test_retrieval_contract.py`, which also asserts that storage does
not default the column to "now" on write — that would make a retrieved run look
finished when it was not, in the one field a client is most likely to report on.

### `source_metadata` holds URL provenance, in one column

Phase 12 stores the `UrlSource` record — submitted/normalized/final URL,
hostname, resolved addresses, redirect facts, status, content type, TLS,
charset, byte size, page title, meta description, fetch time — as a single
JSON blob rather than one nullable column per field. It is descriptive
metadata about one input modality: a text investigation has none of it, and
fifteen nullable columns for a single modality would spread URL concerns
across the core schema. Text runs store `{}`.

The column is added by an additive `ALTER TABLE … ADD COLUMN` in
`app/db/session.py`, executed against a live engine where the table already
exists. `create_all()` does not alter existing tables, so a database created
before Phase 12 gains the column at startup rather than on next recreation.
This is the same "additive DDL, tolerate failure" posture the schema itself
uses; it is **not** a migration framework (see Known Limitations — no
migrations).

---

## claims

Phase 2's `Claim`, stored as the object rather than a projection of it.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | Integer | not null | PK, autoincrement |
| `investigation_id` | Integer | not null | FK → `investigations.id`, cascade delete, indexed |
| `sequence` | Integer | not null | Position within the run |
| `claim_id` | String(64) | not null | `claim_…`, indexed |
| `claim_text` | Text | not null | The atomic claim, not the whole message |
| `claim_type` | String(40) | not null | Indexed |
| `confidence` | Float | not null | **Extraction** confidence, not a fraud probability |
| `span_start` / `span_end` | Integer | not null | Offsets into `investigations.raw_input` |
| `span_text` | Text | not null | The verbatim substring |
| `entity_ids` | JSON | not null | Stored verbatim — see note below |
| `is_complete_sentence` | Boolean | not null | |
| `signals` | JSON | not null | |
| `metadata` | JSON | not null | `str \| int \| float \| bool` values |

Unique: `(investigation_id, claim_id)` — `uq_claims_investigation_claim`.
Indexes: `ix_claims_investigation_sequence`, plus single-column indexes on
`investigation_id`, `claim_id`, `claim_type`.

`metadata` is a JSON column because its values are heterogeneous by construction,
so a column per key would be an unbounded schema driven by extraction rather than
by meaning.

**`entity_ids` and `claim_entity_links` both exist, and both are stored.** They
agree today and the link table is the queryable one, but keeping the claim's own
field means a run round-trips exactly rather than being silently reconciled on
read.

---

## entities

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | Integer | not null | PK, autoincrement |
| `investigation_id` | Integer | not null | FK, cascade delete, indexed |
| `sequence` | Integer | not null | Position within the run |
| `entity_id` | String(64) | not null | `entity_…`, indexed |
| `name` | String(512) | not null | Indexed |
| `entity_type` | String(40) | not null | Indexed |
| `normalized_name` | String(512) | not null | Phase 2's `normalize_entity_name` output |
| `confidence` | Float | not null | |
| `span_start` / `span_end` | Integer | not null | |
| `span_text` | Text | not null | |
| `metadata` | JSON | not null | |

Unique: `(investigation_id, entity_id)` — `uq_entities_investigation_entity`.

Entity references throughout the schema are **ids, not names**. A claim that says
"Acme Capital Advisors" and a report line that says "Acme Capital Advisors" are
the same fact; storing the name twice makes them able to disagree, and a
normalisation change would require rewriting history.

---

## claim_entity_links

Phase 2's `ClaimEntityLink`, as a real relationship rather than strings.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | Integer | not null | PK, autoincrement |
| `investigation_id` | Integer | not null | FK, cascade delete, indexed |
| `sequence` | Integer | not null | Position within the run |
| `claim_id` | String(64) | not null | Indexed |
| `entity_id` | String(64) | not null | Indexed |
| `relationship_type` | String(32) | not null | `MENTIONS` and the other `RelationshipType` values |

Unique: `(investigation_id, claim_id, entity_id, relationship_type)`.

A table and not a comma-joined list because the first question a reader asks of
an investigation is "which claims name this entity?", and answering it from a
string would mean scanning every claim row in Python. The rows come from
`ExtractionResult.relationships` verbatim — never reconstructed from
`Claim.entity_ids`, which would invent a `MENTIONS` relationship for every pair
the extractor happened to record with a different verb.

---

## red_flags

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | Integer | not null | PK, autoincrement |
| `investigation_id` | Integer | not null | FK, cascade delete, indexed |
| `sequence` | Integer | not null | Engine output order |
| `code` | String(40) | not null | Indexed |
| `name` | String(200) | not null | |
| `description` | Text | not null | |
| `severity` | String(16) | not null | Indexed |
| `weight` | Integer | not null | Heuristic weight |
| `matched_text` | Text | not null | Verbatim matched substring |
| `span_start` / `span_end` | Integer | not null | Offsets into `raw_input` |
| `span_text` | Text | not null | |
| `rule_reason` | Text | not null | Plain-language reason |
| `additional_spans` | JSON | not null | Other occurrences of the same rule |

Unique: `(investigation_id, code, span_start, span_end)` — `uq_red_flags_span`.

### Not unique on `(investigation_id, code)`

The earlier sketch said "one row per distinct indicator". That is wrong, and the
span in the key is the fix. Phase 1 emits a separate `RedFlag` for **each span** a
rule fires on, each with its own `rf_` id, and Phase 6 cites those ids
individually in `RiskFactor.red_flag_ids`. Collapsing them onto one row would
destroy findings the risk trace points at.

`matched_text`, `span_start` and `span_end` are stored together because
`red_flag_id_for` hashes exactly those three values. A flag row missing any of
them could not be assigned the id Phase 6 already cited.

---

## verification_results

Phase 4's `VerificationResult`. **The decision is stored, never re-derived.**

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | Integer | not null | PK, autoincrement |
| `investigation_id` | Integer | not null | FK, cascade delete, indexed |
| `sequence` | Integer | not null | Batch order, which `RiskService` consumes positionally |
| `claim_id` | String(64) | not null | Indexed |
| `claim_type` | String(40) | not null | |
| `status` | String(40) | not null | Indexed. `VERIFIED` \| `UNVERIFIED` \| `CONTRADICTED` \| `INSUFFICIENT_EVIDENCE` \| `NOT_APPLICABLE` |
| `reason` | Text | not null | Plain-language explanation |
| `reason_code` | String(40) | not null | Structured code |
| `confidence` | Float | not null | Phase 4's own confidence, distinct from `claims.confidence` |
| `source_ids` | JSON | not null | The `src_` ids relied on |
| `matched_result_ids` | JSON | not null | The `res_` ids examined |
| `queries` | JSON | not null | Queries issued |
| `warnings` | JSON | not null | Per-claim limitations |

Unique: `(investigation_id, claim_id)` — `uq_verification_claim`.
Indexes: `ix_verification_status` (`investigation_id`, `status`),
`ix_verification_investigation_sequence`.

Recomputing `status` from the stored documents would be a second implementation of
Phase 4's decision, and it could disagree. A disagreement would read as "the
database says this claim was verified" (D-020).

The **batch-level** `VerificationResponse.warnings` live on
`investigations.verification_warnings`: they are limitations that applied to the
batch as a whole, which no single result carries.

---

## sources

One Phase 5 `EvidenceSource` — a document **as one search returned it**.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | Integer | not null | PK, autoincrement |
| `investigation_id` | Integer | not null | FK, cascade delete, indexed |
| `source_id` | String(64) | not null | Phase 4 `src_` id, `sha256(canonical_url)[:12]`, indexed |
| `result_id` | String(64) | not null | Phase 4 `res_` id for the individual result, indexed |
| `url` | Text | not null | Exactly as the provider returned it |
| `canonical_url` | Text | not null | Phase 3's canonical form, indexed |
| `domain` | String(255) | not null | Indexed |
| `title` | Text | not null | As published |
| `source_type` | String(24) | not null | Phase 3 `classify_domain()`, indexed |
| `source_tier` | String(32) | not null | Phase 4 registry result, indexed |
| `position` | Integer | not null | Provider rank; a stable tie-breaker |
| `retrieved_at` | DateTime | not null | The provider's retrieval time, never re-stamped |

Unique: `(investigation_id, source_id, result_id)` — `uq_sources_investigation_result`.

### Why the key is the *pair*

`source_id` names the document and is stable everywhere; `result_id` names the
individual retrieval that addressed one claim. A provider that returns the same
document for two claims produces two results and therefore **two rows**.

Phase 5 derives `ev_` ids from **both** ids, so keying on `source_id` alone would
force one retrieval to borrow another's `result_id` — and every evidence item
citing it would then round-trip to provenance that was never retrieved.

`canonical_url` is stored exactly as Phase 3/5 computed it and is never
re-derived at storage time. A second canonicalisation implementation would fork
the `src_` ids Phase 4 derived from the first normalisation (D-023).

There is no `credibility` column: `source_type` and `source_tier` already express
how authoritative a publisher is, and a third column would be a second answer
that could disagree with both (D-012, D-018). There is no `snippet` column either:
verbatim text lives on the evidence row that quotes it.

---

## evidence_responses

The per-claim envelope. A row of its own because `EvidenceResponse` carries fields
belonging to no single item.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | Integer | not null | PK, autoincrement |
| `investigation_id` | Integer | not null | FK, cascade delete, indexed |
| `sequence` | Integer | not null | Position among the run's responses |
| `claim_id` | String(64) | not null | Indexed |
| `verification_status` | String(40) | not null | Carried so the evidence can be read beside the decision it explains |
| `verification_reason_code` | String(40) | not null | |
| `source_refs_json` | JSON | not null | The ordered `sources` tuple as `[source_id, result_id]` pairs |
| `warnings` | JSON | not null | Per-claim evidence limitations |
| `built_at` | DateTime | not null | |

Unique: `(investigation_id, claim_id)` — `uq_evidence_responses_claim`.

`source_refs_json` is a **reference, not a copy**, and it cannot be replaced by a
join. `distinct_sources` keeps the *first* result seen for each document within a
claim, while `sources` holds every result. Deriving the response's list from the
table would change which `result_id` a retrieved document is reported under.

---

## evidence

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | Integer | not null | PK, autoincrement |
| `investigation_id` | Integer | not null | FK, cascade delete, indexed |
| `evidence_response_id` | Integer | not null | FK → `evidence_responses.id`, cascade delete, indexed |
| `sequence` | Integer | not null | Position **within its response** |
| `evidence_id` | String(64) | not null | Phase 5 `ev_` id, indexed |
| `claim_id` | String(64) | not null | Indexed. Not nullable — see below. |
| `source_id` | String(64) | not null | Indexed |
| `result_id` | String(64) | not null | Indexed. Together with `source_id` identifies the source row. |
| `claim_type` | String(40) | not null | |
| `verification_status` | String(40) | not null | Phase 4's status for this claim |
| `evidence_type` | String(32) | not null | Indexed |
| `relationship` | String(24) | not null | Indexed. `SUPPORTS` \| `CONTRADICTS` \| `IDENTITY_REFERENCE` \| `CONTEXT` \| `MENTIONS` |
| `relevance` | String(8) | not null | Indexed. `HIGH` \| `MEDIUM` \| `LOW` |
| `excerpt` | Text | not null | Verbatim from the result. Never summarised. |
| `excerpt_origin` | String(8) | not null | `snippet` \| `title` |
| `matched_cue` | String(255) | nullable | Why the relationship was derived |
| `provider_query` | Text | nullable | The query that surfaced the document |

Unique: `(investigation_id, evidence_id)` — `uq_evidence_investigation_item`.
Indexes: `ix_evidence_investigation_claim`, `ix_evidence_investigation_source`,
plus single-column indexes on `investigation_id`, `evidence_id`, `claim_id`,
`source_id`, `result_id`, `evidence_type`, `relationship`, `relevance`,
`evidence_response_id`.

**The two foreign keys are both real.** An item belongs to its run *and* to the
response that groups it. The run cascade guarantees cleanup even when the
response was never loaded; the response relationship is what lets the response be
rehydrated with its items already ordered.

`claim_id` and the source pair are **not nullable**. Evidence that cannot be
attributed to a statement, or that has no retrievable source, is not evidence —
and a schema that permitted either would quietly accept rows the in-memory
`EvidenceResponse` would have rejected (D-006, D-023).

Notes carried forward from Phase 5:

- **`relevance` is a `String(8)`, not a `Float`.** A numeric relevance becomes an
  undeclared input to the Phase 6 score, and D-007 requires declared weighting
  rather than a number that silently multiplies something.
- **`evidence_type`, `relationship` and `relevance` are three separate columns.**
  Collapsing them into one taxonomy encodes the same fact twice, and two copies
  eventually disagree (D-023).
- **`excerpt_origin` is not optional.** Without it a stored excerpt is an
  unattributable string, and an unattributable quote cannot be checked against the
  page it claims to come from.

---

## risk_assessments

Phase 6's `RiskAssessment`, at most one per run.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | Integer | not null | PK, autoincrement |
| `investigation_id` | Integer | not null | FK, cascade delete, **unique** |
| `risk_score` | Integer | not null | Bounded indicator sum. **Never a probability.** |
| `raw_score` | Integer | not null | Uncapped sum, so a clamp stays visible |
| `risk_level` | String(16) | not null | Indexed |
| `relevant_claims` | Integer | not null | |
| `assessed_claims` | Integer | not null | |
| `evidence_coverage` | Float | not null | Transparency measure, **not** a confidence |
| `analysis_completeness` | Float | not null | Transparency measure, **not** a confidence |
| `weights_json` | JSON | not null | The `RiskWeights` in force (D-007) |
| `thresholds_json` | JSON | not null | The `RiskThresholds` in force |
| `warnings_json` | JSON | not null | Includes `SCORE_NOT_A_PROBABILITY` |
| `assessed_at` | DateTime | not null | |

Separate from `investigations` because it does not exist on every run — a stopped
run must report **no score at all**, not a zero — and because its shape is the
richest in the system.

`weights_json` and `thresholds_json` exist because the configuration is expected
to change. A stored assessment that recorded only a score would be uninterpretable
once the weights moved (D-007).

### There is no `caveat` column

The caveat that stops `risk_score` being read as a probability is
`SCORE_NOT_A_PROBABILITY`, and `RiskAssessment` appends it to `warnings` itself
during validation. A `caveat` column would be a second copy of a string the
schema already owns, free to drift from the one the model emits. Keeping it only
in `warnings_json` means rehydrating the assessment **through the model**
reproduces the caveat by construction rather than by discipline (D-022, D-025).

---

## risk_factors

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | Integer | not null | PK, autoincrement |
| `investigation_id` | Integer | not null | FK, cascade delete, indexed |
| `factor_id` | String(64) | not null | Phase 6 `rsk_` id, indexed |
| `sequence` | Integer | not null | Position within the run |
| `origin` | String(24) | not null | Indexed |
| `factor_type` | String(40) | not null | A `RedFlagCode` or a `VerificationFactorType` |
| `label` | String(255) | not null | |
| `description` | Text | not null | |
| `reason` | Text | not null | |
| `source` | Text | not null | |
| `severity` | String(16) | not null | |
| `weight` | Integer | not null | |
| `contribution` | Integer | not null | |
| `absorbed_into` | String(64) | nullable | The factor this one was folded into |
| `claim_ids` | JSON | not null | The trace outward |
| `red_flag_ids` | JSON | not null | The trace outward |
| `evidence_ids` | JSON | not null | The trace outward |
| `source_ids` | JSON | not null | The trace outward |
| `verification_statuses` | JSON | not null | The statuses behind the factor |
| `is_uncertainty` | Boolean | not null | |

Unique: `(investigation_id, factor_id)` — `uq_risk_factors_factor`.
Index: `ix_risk_factors_investigation_sequence`.

**De-duplicated factors are stored, not filtered.** They contribute zero and name
the factor that absorbed them; dropping them at write time would conceal that a
claim was contradicted, which is the specific failure D-026 forbids.

The five id lists are **JSON lists, not association tables**. They are the trace
*outward* from a factor — never queried inward from a claim or a flag — so a join
table per id space would be five tables serving no query.

---

## timeline_events

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | Integer | not null | PK, autoincrement |
| `investigation_id` | Integer | not null | FK, cascade delete, indexed |
| `sequence` | Integer | not null | Unique within the run |
| `stage` | String(32) | not null | Indexed |
| `status` | String(16) | not null | `STARTED` \| `COMPLETED` \| `PARTIAL` \| `FAILED` \| `SKIPPED` |
| `message` | Text | not null | Fixed wording describing the mechanical outcome |
| `at` | DateTime | not null | |

Unique: `(investigation_id, sequence)` — `uq_timeline_sequence`.

`sequence` is required because two stages can emit events in the same millisecond,
and "which of these came first" is what a UI needs to render a strip rather than
an unordered list. Ordering by timestamp alone would be a tie broken arbitrarily
by the database.

---

## investigation_warnings

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | Integer | not null | PK, autoincrement |
| `investigation_id` | Integer | not null | FK, cascade delete, indexed |
| `sequence` | Integer | not null | |
| `code` | String(64) | not null | Indexed. Branch on this, not on text. |
| `stage` | String(32) | not null | Indexed |
| `message` | Text | not null | Fixed, factual wording |

Index: `ix_warnings_investigation_sequence`.

**`error_type` is deliberately absent.** A warning is something a user may read
through the API, and an exception class name in front of one is a diagnostic that
belongs in the log. Phase 8 already established that boundary by dropping the
field on the way out; storing it would only put it one round trip away from being
reintroduced.

---

## investigation_errors

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | Integer | not null | PK, autoincrement |
| `investigation_id` | Integer | not null | FK, cascade delete, indexed |
| `sequence` | Integer | not null | |
| `code` | String(64) | not null | Indexed |
| `stage` | String(32) | not null | Indexed |
| `message` | Text | not null | Fixed wording safe to surface |
| `error_type` | String(128) | nullable | **Exception class name only** |

Index: `ix_errors_investigation_sequence`.

`error_type` is stored because an error diagnoses **our** defect for an operator,
and an exception class name is not a secret. What is never stored is the message
from the exception: driver text routinely embeds a DSN, so `str(exc)` has no place
in a row a future API may render. The same rule Phase 8 applied to
`probe_database`, applied again where a column would otherwise make the leak
trivially easy to reintroduce.

---

## Ordering

Every collection the API returns in order carries an explicit `sequence` column.
The state carries *tuples*, and Phase 5's evidence ordering is a deterministic
function the database cannot reproduce from the values it stores. Inferring order
from a timestamp or an id would make a retrieved investigation list its findings
in a different order than the live one, with nothing to detect it.

---

## Deletion Behaviour

Deleting an investigation cascades to every child table. The cascade is declared
in the schema (`ON DELETE CASCADE` on every foreign key) *and* expressed as ORM
`cascade="all, delete-orphan"`, so a run is removed completely whether or not the
ORM happened to load the collections first. A child that outlived its parent would
be an orphan that no endpoint can reach and no query filters by — invisible, but
also undeletable without knowing it exists.

There is no `DELETE` endpoint in Phase 9. The repository exposes `delete()`, which
removes **every** run under a public id rather than the newest: removing "the"
investigation for a piece of content while leaving its re-runs behind would make
the id retrievable again with no way to ask for that.

---

## Portability Notes

The schema is verified against both backends in `backend/tests/db/test_schema.py`,
which compiles every table and index against the PostgreSQL dialect. A construct
SQLite tolerates and PostgreSQL rejects would pass every test here and fail on the
day `DATABASE_URL` changed.

- **No `JSONB`, `ARRAY`, `UUID` or server-side defaults.** JSON-shaped payloads use
  SQLAlchemy `JSON`, which maps to `JSONB` on PostgreSQL and JSON-encoded `TEXT` on
  SQLite. Queries that filter *inside* JSON are avoided; the JSON columns hold
  opaque bags and ordered id lists, and everything filterable has a real column.
- **Timestamps go through `UtcDateTime`.** Values are written as naive UTC and read
  back as aware UTC. SQLite has no timezone type, so a bare `DateTime` would return
  naive values there and aware ones on PostgreSQL — an asymmetry that does not fail
  here but fails much later, as `can't subtract offset-naive and offset-aware
  datetimes`, in code that only runs on the hosted database. `compare_values` also
  normalises before comparing, so a `WHERE created_at = :ts` filter matches on both.
- **Naive values are assumed UTC, never localised.** Every naive value this layer
  stores was converted from aware UTC on the way in, so interpreting it in the
  server's local zone would move timestamps by the machine's offset.
- **Enums are stored as `String`, not as database enum types.** PostgreSQL enum
  types make adding a value a schema migration; the Phase 1-6 enums are owned by
  Pydantic models and validated on the way in and out.
- **Foreign keys are enforced on SQLite too.** `PRAGMA foreign_keys=ON` is set per
  connection, since SQLite leaves it off by default and would otherwise accept
  orphans that PostgreSQL rejects.

---

## Corrections to the earlier sketch

The Phase 0 document described a schema that the Phase 1-7 models do not support.
Building to it would have meant inventing a second claim, a second entity and a
second risk assessment, each able to disagree with the model it was derived from.
These are the substantive changes:

| Earlier sketch | Now | Why |
| --- | --- | --- |
| `users` table, `investigations.user_id` | **Not created** | There is no authentication in this version. An empty placeholder table looks decided; nothing has been asked to design a user model. |
| `reports` table, 1:1 with an investigation | **Not created** | No phase assigned. The Phase 6 assessment data it was going to hold is now in `risk_assessments` + `risk_factors`, where the trace survives. A report is the first thing that would want this table, so it should be designed when a report is, not before. |
| `investigations.public_id` unique UUID | `public_id` = `inv_<16 hex>`, **not unique** | The id is a content digest, so a unique constraint would make re-running content an error. `id` is the primary key. |
| `status` ∈ `PENDING \| PROCESSING \| COMPLETED \| FAILED` | `COMPLETED \| PARTIAL \| FAILED` | The graph is synchronous (D-036), so there is no queued or running state. `PARTIAL` is what the sketch lacked, and it matters: it is how a degraded run stays distinguishable from a clean one (D-006). |
| `input_text` / `input_url` / `file_name` | `raw_input`, `extracted_text` | One column for the submitted content. `URL`, `IMAGE` and `PDF` are recognised and refused in this version, so a per-kind column set would describe ingestion that does not exist yet. |
| `error_message`, `limitations`, `timeline`, `duration_ms` as JSON on the run | `investigation_errors`, `investigation_warnings`, `timeline_events` as tables | Each entry is independently addressable and the run history needs to filter on them. `limitations` in particular must be branchable by code (D-009). |
| `claims.entities` (names), `claims.verification_required`, `claims.status` | `claims.entity_ids` + `claim_entity_links`; `verification_results` as a table | Names can disagree with the id they refer to. The verification decision belongs in the table that records the whole decision, not summarised onto the claim. |
| `entities.mentions`, `.registration_number`, `.verification_status`, `.context` | Not stored | No Phase 1-6 field produces them. |
| `red_flags` unique on `(investigation_id, code)` | Unique on `(investigation_id, code, span_start, span_end)` | Phase 1 emits one flag per span with its own `rf_` id; Phase 6 cites those ids. Collapsing them destroys findings the risk trace points at. |
| `evidence.source_id` FK → `sources.source_id` | `source_id` + `result_id`, both indexed, no cross-table FK | `sources` is keyed on the *pair*. A FK to `source_id` alone would be ambiguous across investigations, and `source_id` is not globally unique. |
| No `evidence_responses` table | Added | `EvidenceResponse` carries the claim's status, reason code, build time and the **ordered** source list — fields belonging to no single item. |
| Risk data in a `reports` row | `risk_assessments` + `risk_factors` | The trace has to survive: a reader must be able to walk from any factor to the red flag, claim and document behind it (D-026). |
| No `claim_entity_links`, `verification_results`, `timeline_events`, `investigation_warnings`, `investigation_errors` | Added | All five are required by the Phase 7 state and the Phase 8 response. |

---

## Phase 9 Notes

### The public id problem, resolved

Phase 8 flagged this as an open question: `investigation_id` is a content
fingerprint, so two runs of identical content share it, and clients may already be
storing it. The safer option was chosen — **the value is preserved** and `id` was
added as the primary key, with `investigation_id_for`'s digest also stored in
`content_hash` for re-run detection. No client holding a Phase 8 id can be broken
by Phase 9.

### The module-level engine is gone from the request path

Phase 8 left `engine` and `SessionLocal` as module-level globals in
`app/db/session.py`, resolved from whatever `DATABASE_URL` was set at import
time, and noted that Phase 9 should decide whether they survive. **They do not
participate in the request path.** `get_db(request)` reads the session factory
from `app.state.database`, so every request uses the connection the application
was actually built with. The globals remain only as a compatibility surface for
code that has not yet moved; nothing in `app/api/` reaches them.

### Schema creation happens at startup

`lifespan` calls `database.create_all()` once, wrapped in a `try/except` that logs
and continues. A database that is reachable but not writable must not stop the
read-only endpoints from starting — the health check reports the failure, and
investigation is a degraded service rather than none. `create_all` is idempotent,
so this is safe on every boot.

This is deliberately **not** a migration system. The schema is created from the
models, so a column added in a later phase will be added by `create_all` and an
existing table will *not* gain it. Alembic is the right tool the first time a
deployed database has to be migrated in place rather than recreated, and nothing
here is pretending otherwise.

### What Phase 9 did not do

- No authentication, so no ownership check on retrieval: anyone holding an id can
  read that investigation. That is acceptable only because there is nothing to
  authenticate against yet, and it will need revisiting before there is.
- No `DELETE` endpoint, though the repository supports it.
- No pagination cursor — `limit`/`offset` only, which is fine at this scale and
  would need replacing for a table large enough for offset paging to hurt.
- No indexes tuned for any specific query beyond the ones listed above. They exist
  for the queries Phase 9 actually issues, not for a workload that has not been
  measured.

## Phase 12 Notes

- **One new column, additive.** `investigations.source_metadata`
  (JSON, not null, `{}` for text runs) holds the Phase 12 `UrlSource`
  record, as described above. No table was created, renamed or
  restructured; the fourteen-table design is unchanged. The column is
  added to an existing table by an additive `ALTER TABLE … ADD COLUMN`
  in `app/db/session.py`, because `create_all()` will not alter a
  table that already exists.
- **The public id digest covers the URL, not the page.** Two
  investigations of the same URL share a `public_id` — the digest is of
  the submitted input, and the URL *is* the submitted input — so a
  re-run returns the newest stored run, the same semantics as re-running
  the same text.
- **No page content is stored beyond the extracted text.**
  `extracted_text` holds the normalised visible text exactly as the
  text path does; the raw HTML is never persisted. `WebsiteDocument` —
  the extraction result — is not stored at all, because its `text` is
  `extracted_text` and its `source` is `source_metadata`.
- **Portability.** `as_metadata()` renders `resolved_addresses` as a
  list precisely so the blob is JSON-safe on both SQLite and PostgreSQL,
  and the column compiles under both dialects like every other JSON
  column in the schema.
