# DATABASE_SCHEMA — InvestShield AI

ORM: SQLAlchemy 2.x (declarative). Local default:
`sqlite:///./data/investshield.db`. Portable to PostgreSQL/Neon by changing
`DATABASE_URL` only (D-001).

Column types are deliberately portable — no `JSONB`, `ARRAY`, or server-side
`UUID` defaults. JSON-shaped payloads use SQLAlchemy `JSON`, which maps to
`JSONB` on Postgres and `TEXT` (JSON-encoded) on SQLite.

---

## Entity Relationship Summary

```
users (1) ──────< (0..N) investigations
                      │
                      ├──< claims ──────< evidence >────── sources
                      │       │               │
                      │       │               └── (evidence.source_id FK)
                      │       └────────────── (evidence.claim_id FK)
                      ├──< entities
                      ├──< red_flags
                      └──< reports (1:1)

users is optional/unused in the MVP (no auth); investigations.user_id is nullable.
```

Nothing in this schema is written yet — persistence is Phase 9. The `sources` and
`evidence` tables above are aligned with Phase 5's `EvidenceSource` and
`EvidenceItem` so that no field needs reshaping when they are built.

---

## users

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | Integer | PK, autoincrement | |
| `name` | String(120) | nullable | unused in MVP |
| `email` | String(255) | nullable, unique | unused in MVP |
| `created_at` | DateTime | not null, default utcnow | |

Retained for forward compatibility with future auth. No rows are created in the MVP.

---

## investigations

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | Integer | PK, autoincrement | internal |
| `public_id` | String(36) | not null, unique, indexed | UUID exposed via API |
| `user_id` | Integer | FK → `users.id`, nullable | no auth in MVP |
| `input_type` | String(16) | not null | `TEXT` \| `URL` \| `IMAGE` \| `PDF` |
| `input_text` | Text | nullable | original submitted text |
| `input_url` | String(2048) | nullable | for `URL` inputs |
| `file_name` | String(255) | nullable | original upload name |
| `extracted_text` | Text | nullable | text produced by OCR/PDF/fetch |
| `language` | String(8) | not null, default `en` | report language |
| `status` | String(16) | not null, indexed | `PENDING` \| `PROCESSING` \| `COMPLETED` \| `FAILED` |
| `risk_level` | String(16) | nullable, indexed | `LOW` \| `MEDIUM` \| `HIGH` \| `CRITICAL` |
| `risk_score` | Integer | nullable | Phase 6 bounded indicator sum, capped at `risk_score_ceiling`; never a probability |
| `red_flag_count` | Integer | not null, default 0 | denormalised for list queries |
| `claim_count` | Integer | not null, default 0 | denormalised for list queries |
| `entity_count` | Integer | not null, default 0 | denormalised for list queries |
| `source_count` | Integer | not null, default 0 | denormalised for list queries |
| `limitations` | JSON | nullable | list[str] |
| `timeline` | JSON | nullable | list[dict] |
| `duration_ms` | Integer | nullable | wall-clock pipeline time |
| `error_message` | Text | nullable | populated when `status = FAILED` |
| `created_at` | DateTime | not null, default utcnow, indexed | |
| `updated_at` | DateTime | not null, onupdate utcnow | |

Indexes: `ix_investigations_public_id` (unique), `ix_investigations_status`,
`ix_investigations_risk_level`, `ix_investigations_created_at`.

---

## claims

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | Integer | PK, autoincrement | |
| `investigation_id` | Integer | FK → `investigations.id`, not null, indexed | cascade delete |
| `claim_id` | String(32) | not null | per-investigation id, e.g. `c1` |
| `claim_text` | Text | not null | atomic claim, not the whole message |
| `claim_type` | String(32) | not null, indexed | `REGULATORY`, `RETURN`, … |
| `entities` | JSON | nullable | list[str] of related entity names |
| `confidence` | Float | nullable, 0..1 | extraction confidence, not a fraud probability |
| `verification_required` | Boolean | not null, default true | |
| `status` | String(32) | nullable, indexed | verification status enum |
| `verification_reason` | Text | nullable | plain-language explanation |
| `created_at` | DateTime | not null, default utcnow | |

Unique constraint: `(investigation_id, claim_id)`.

`status` and `verification_reason` are written from Phase 4's
`VerificationResult.status` and `.reason` (Phase 9). The permitted values are
exactly `VERIFIED`, `UNVERIFIED`, `CONTRADICTED`, `INSUFFICIENT_EVIDENCE`,
`NOT_APPLICABLE` — no verdict value is ever stored. `claims.confidence` stays
*extraction* confidence; the verification assessment's own confidence has no
column here, because storing a second number beside the first is how a fraud
probability eventually appears in a report (D-020).

---

## entities

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | Integer | PK, autoincrement | |
| `investigation_id` | Integer | FK → `investigations.id`, not null, indexed | cascade delete |
| `entity_id` | String(32) | not null | per-investigation id, e.g. `e1` |
| `name` | String(255) | not null, indexed | |
| `entity_type` | String(32) | not null, indexed | `PERSON`, `COMPANY`, … |
| `registration_number` | String(64) | nullable | extracted reg number, if any |
| `mentions` | Integer | not null, default 1 | |
| `confidence` | Float | nullable, 0..1 | |
| `verification_status` | String(32) | nullable | entity-level verification status |
| `context` | Text | nullable | surrounding text snippet |
| `created_at` | DateTime | not null, default utcnow | |

Unique constraint: `(investigation_id, entity_id)`.

---

## red_flags

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | Integer | PK, autoincrement | |
| `investigation_id` | Integer | FK → `investigations.id`, not null, indexed | cascade delete |
| `code` | String(48) | not null, indexed | e.g. `GUARANTEED_RETURN` |
| `name` | String(120) | not null | |
| `severity` | String(16) | not null | `LOW` \| `MEDIUM` \| `HIGH` \| `CRITICAL` |
| `weight` | Integer | not null | heuristic weight, configurable |
| `description` | Text | not null | plain-language explanation |
| `evidence` | Text | nullable | exact matched span from submitted content |
| `created_at` | DateTime | not null, default utcnow | |

Unique constraint: `(investigation_id, code)` — one row per distinct indicator.

---

## sources

Persisting a source row is **Phase 9** work. The columns below are the Phase 5
`EvidenceSource` fields as they will need to be stored; they are aligned with
`app/schemas/evidence.py` so that no field has to be reshaped on the way in.

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | Integer | PK, autoincrement | |
| `investigation_id` | Integer | FK → `investigations.id`, not null, indexed | cascade delete |
| `source_id` | String(32) | not null | Phase 4 `src_` id, `sha256(canonical_url)[:12]` |
| `result_id` | String(32) | not null | Phase 4 `res_` id for the individual result |
| `canonical_url` | String(2048) | not null, indexed | Phase 3 canonical form; the de-duplication key |
| `url` | String(2048) | not null | exactly as the provider returned it |
| `domain` | String(255) | not null, indexed | Phase 3 `extract_domain()` |
| `title` | String(512) | not null | result title as published |
| `source_type` | String(24) | not null, indexed | Phase 3 `SourceType`: `REGULATOR` \| `GOVERNMENT` \| `EXCHANGE` \| `OFFICIAL_ENTITY` \| `TRUSTED_SECONDARY` \| `GENERAL_WEB` \| `UNKNOWN` |
| `source_tier` | String(32) | not null, indexed | Phase 4 `SourceTier`, e.g. `TIER_1_PRIMARY_REGULATOR` |
| `position` | Integer | not null, default 1 | provider rank; a stable tie-breaker |
| `retrieved_at` | DateTime | not null | the provider's retrieval time, never re-stamped |
| `created_at` | DateTime | not null, default utcnow | |

Unique constraint: `(investigation_id, source_id)`.

Note the change from the earlier sketch: there is **no** `credibility` column.
Phase 3's `source_type` and Phase 4's `source_tier` already express how
authoritative a publisher is, and a third credibility column would be a second
answer to the same question that could disagree with both (D-012, D-018). There
is also no `snippet` column: the verbatim text lives on the evidence row that
quotes it, so one document can be cited by several items without its text being
duplicated.

---

## evidence

Persisting an evidence row is **Phase 9** work. The columns below mirror Phase 5's
`EvidenceItem`.

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | Integer | PK, autoincrement | |
| `investigation_id` | Integer | FK → `investigations.id`, not null, indexed | cascade delete |
| `evidence_id` | String(32) | not null | Phase 5 `ev_` id, derived by digest |
| `claim_id` | String(32) | not null, indexed | FK → `claims.claim_id`; evidence is meaningless without its claim |
| `source_id` | String(32) | not null, FK → `sources.source_id` | the traceable origin |
| `excerpt` | Text | not null | verbatim `SearchResult.snippet`, else `.title` |
| `excerpt_origin` | String(8) | not null | `snippet` \| `title` — which field `excerpt` was copied from |
| `evidence_type` | String(32) | not null, indexed | `REGULATORY_RECORD` \| `GOVERNMENT_RECORD` \| `EXCHANGE_RECORD` \| `OFFICIAL_ENTITY_SOURCE` \| `SEARCH_RESULT` |
| `relationship` | String(24) | not null, indexed | `SUPPORTS` \| `CONTRADICTS` \| `IDENTITY_REFERENCE` \| `CONTEXT` \| `MENTIONS` |
| `relevance` | String(8) | not null, indexed | `HIGH` \| `MEDIUM` \| `LOW` |
| `verification_status` | String(24) | not null | Phase 4's status, copied so the row explains its own decision |
| `matched_cue` | String(64) | nullable | Phase 4's cue, for auditing *why* the relationship was derived |
| `provider_query` | Text | nullable | the Phase 3 query that surfaced the document |
| `created_at` | DateTime | not null, default utcnow | |

Index: `ix_evidence_claim_id`, `ix_evidence_relationship`,
`ix_evidence_evidence_type`.

Notes carried forward:

- **`relevance` is a `String(8)`, not a `Float`.** The original sketch had
  `relevance Float 0..1`. That was dropped in Phase 5 deliberately: a numeric
  relevance becomes an undeclared input to the Phase 6 risk score, and D-007
  requires transparent, declared weighting rather than a number that silently
  multiplies something. **Phase 6 honours this**: it reads evidence rows only to
  attach their `ev_`/`src_` ids to the factors bearing on the same claim, and
  sums no evidence column into the score. It reports `evidence_coverage` as a
  *count* of claims with at least one proof-grade document, which is a
  transparency measure and not a weighted input.
- **`evidence_type`, `relationship` and `relevance` are three separate columns.**
  Collapsing them into one taxonomy encodes the same fact twice, and two copies
  eventually disagree (D-023).
- **`excerpt_origin` is not optional.** Without it, a stored excerpt is an
  unattributable string, and an unattributable quote cannot be checked against the
  page it claims to come from.
- **`claim_id` is not nullable.** A claim-free evidence row cannot be attributed
  to a statement, and this product's red-flag spans are already stored on
  `red_flags.evidence`.

**Invariant:** `source_id` must reference a real row in `sources`. Evidence may
never be created without a traceable source — that is what makes the product
trustworthy (D-006, D-023). In Phase 5 the same rule is enforced in memory: an
`EvidenceResponse` is rejected if any item cites a source absent from its own
`sources` list, and `EvidenceService` drops any supplied document the claim's
Phase 4 `VerificationResult` never recorded.

---

## reports

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | Integer | PK, autoincrement | |
| `investigation_id` | Integer | FK → `investigations.id`, not null, unique | 1:1 with investigation |
| `risk_level` | String(16) | not null, indexed | `LOW` \| `MEDIUM` \| `HIGH` \| `CRITICAL` |
| `risk_score` | Integer | not null | bounded by `risk_score_ceiling`; never a probability |
| `raw_score` | Integer | not null | uncapped sum, retained so a clamped score can be shown as clamped |
| `risk_factors` | JSON | nullable | Phase 6 `RiskFactor[]`, serialized as-is: `{id, origin, factor_type, label, description, reason, source, severity, weight, contribution, absorbed_into, claim_ids, red_flag_ids, evidence_ids, source_ids, verification_statuses, is_uncertainty}` |
| `weights_snapshot` | JSON | nullable | the `RiskWeights` in force, so a stored report stays auditable after the configuration moves (D-007, D-028) |
| `thresholds_snapshot` | JSON | nullable | the `RiskThresholds` in force: `{medium_max, high_max, critical_max, ceiling}` |
| `evidence_coverage` | Float | nullable | `0..1` transparency measure, **not** a confidence or accuracy score |
| `analysis_completeness` | Float | nullable | `0..1` measure of the per-claim analyses actually performed |
| `summary` | Text | nullable | investigation summary |
| `red_flags` | JSON | nullable | detected indicators snapshot |
| `why_flagged` | JSON | nullable | list of `{title, explanation}` |
| `safety_guidance` | JSON | nullable | list[str] |
| `limitations` | JSON | nullable | list[str] |
| `language` | String(8) | not null, default `en` | |
| `created_at` | DateTime | not null, default utcnow | |

Notes carried forward from Phase 6:

- **`risk_factors` stores the whole `RiskFactor`, not a trimmed
  `{code, label, weight, reason}`.** The trace is the product: a reader must be
  able to walk from any factor to the red flag, the claim and the document behind
  it, and a de-duplicated factor must be storable with its `absorbed_into`
  pointer intact so the breakdown still explains itself (D-026).
- **De-duplicated factors are persisted, not filtered out.** They contribute `0`
  and name the factor that counted them. Dropping them at write time would
  conceal that a claim was contradicted.
- **`weights_snapshot` and `thresholds_snapshot` exist because the configuration
  is expected to change.** A report that recorded only a score would be
  uninterpretable once the weights moved; a report that recorded the weights
  that produced it is not (D-007).
- **`risk_score` and `raw_score` are both stored.** When a document trips enough
  indicators to exceed the ceiling, the two differ, and showing only the capped
  value would hide that the cap was applied.
- **Neither ratio is a confidence.** `evidence_coverage` and
  `analysis_completeness` describe how much of the investigation had real
  material behind it, not how likely anything is (D-025).

---

## Deletion Behaviour

Deleting an investigation cascades to `claims`, `entities`, `red_flags`,
`sources`, `evidence`, and `reports`.

---

## Portability Notes

- `JSON` columns hold lists/objects; SQLite stores JSON-encoded TEXT, Postgres
  maps to `JSONB`. Queries that filter inside JSON are avoided.
- Timestamps are stored as naive UTC (`datetime.utcnow()`); the API serialises
  them with an explicit `Z` suffix.
- No Postgres-only features are used, so `DATABASE_URL` is the only change
  required to move to Neon.
