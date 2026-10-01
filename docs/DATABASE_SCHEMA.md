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
                      │       │
                      │       └────────── (evidence.claim_id FK)
                      ├──< entities
                      ├──< red_flags
                      └──< reports (1:1)

users is optional/unused in the MVP (no auth); investigations.user_id is nullable.
```

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
| `risk_score` | Integer | nullable | sum of indicator weights |
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

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | Integer | PK, autoincrement | |
| `investigation_id` | Integer | FK → `investigations.id`, not null, indexed | cascade delete |
| `source_id` | String(32) | not null | per-investigation id, e.g. `s1` |
| `title` | String(512) | nullable | |
| `url` | String(2048) | nullable | may be null for user-provided content |
| `snippet` | Text | nullable | search snippet / extracted passage |
| `source_type` | String(24) | not null, indexed | `OFFICIAL` \| `TRUSTED` \| `GENERAL_WEB` \| `USER_PROVIDED` |
| `tier` | Integer | not null, default 4 | 1 = most authoritative |
| `credibility` | String(24) | not null | same enum as `source_type` |
| `retrieved_at` | DateTime | not null, default utcnow | |
| `created_at` | DateTime | not null, default utcnow | |

Unique constraint: `(investigation_id, source_id)`.
Index: `ix_sources_credibility`.

---

## evidence

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | Integer | PK, autoincrement | |
| `investigation_id` | Integer | FK → `investigations.id`, not null, indexed | cascade delete |
| `claim_id` | String(32) | nullable | FK-style link to `claims.claim_id`; null for red-flag-only evidence |
| `entity_id` | String(32) | nullable | link to `entities.entity_id` when entity-scoped |
| `source_id` | String(32) | not null, FK → `sources.source_id` | the traceable origin |
| `evidence_text` | Text | not null | what the source actually says |
| `relevance` | Float | nullable, 0..1 | similarity/relevance score |
| `relationship` | String(16) | not null, indexed | `SUPPORTS` \| `CONTRADICTS` \| `CONTEXT` |
| `created_at` | DateTime | not null, default utcnow | |

Index: `ix_evidence_claim_id`, `ix_evidence_relationship`.

**Invariant:** `source_id` must reference a real row in `sources`. Evidence may
never be created without a traceable source — that is what makes the product
trustworthy (D-006, evidence rule in the product brief).

---

## reports

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | Integer | PK, autoincrement | |
| `investigation_id` | Integer | FK → `investigations.id`, not null, unique | 1:1 with investigation |
| `risk_level` | String(16) | not null, indexed | |
| `risk_score` | Integer | not null | |
| `risk_factors` | JSON | nullable | list of `{code, label, weight, reason}` |
| `summary` | Text | nullable | investigation summary |
| `red_flags` | JSON | nullable | detected indicators snapshot |
| `why_flagged` | JSON | nullable | list of `{title, explanation}` |
| `safety_guidance` | JSON | nullable | list[str] |
| `limitations` | JSON | nullable | list[str] |
| `language` | String(8) | not null, default `en` | |
| `created_at` | DateTime | not null, default utcnow | |

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
