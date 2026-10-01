# InvestShield AI — Development Logs

Append-only. One entry per meaningful implementation step.
Newest entries at the bottom.

---

## Phase 0 — Project foundation

**Date:** 2026-10-01
**Phase:** 0 — Project foundation

### What was implemented

- Git repository initialised on branch `main`.
- Monorepo directory structure (frontend reserved, backend, data, docs).
- Persistent documentation set (`docs/`): project context, architecture,
  implementation plan, decisions, API spec, database schema, AI pipeline, and
  current-state recovery files.
- Root configuration: `.gitignore`, `.env.example`, `README.md`,
  `docker-compose.yml`.
- Backend package skeleton with `app/{api,core,db,models,schemas,services,agents,graph,tools}`.
- `app/core/config.py` — pydantic-settings configuration loaded from `.env`,
  with derived helpers (CORS list, upload allow-list, provider-configured
  flags, repo-anchored data dir).
- `app/core/logging.py` — structured logging setup.
- `app/db/base.py` + `app/db/session.py` — SQLAlchemy 2.x declarative base,
  engine factory with cwd-independent SQLite path resolution, foreign-key pragma
  for SQLite, session factory, and the `get_db` FastAPI dependency.
- `app/services/capabilities.py` — non-invasive runtime probes for LLM,
  search, OCR, PDF and embeddings, plus Tesseract binary resolution.
- `app/api/routes/health.py` + `app/api/deps.py` — `GET /api/health` reporting
  database reachability and per-service availability without exposing secrets.
- `app/main.py` — application factory, CORS, lifespan, and structured error
  handlers (no stack traces in responses).
- Test suite: `test_config.py`, `test_health.py`, `test_db.py`,
  `test_capabilities.py`, plus `conftest.py` fixtures.

### Files created

```
.env.example
.gitignore
README.md
docker-compose.yml
backend/requirements.txt
backend/requirements-ai.txt
backend/pytest.ini
backend/app/__init__.py
backend/app/main.py
backend/app/core/__init__.py
backend/app/core/config.py
backend/app/core/logging.py
backend/app/db/__init__.py
backend/app/db/base.py
backend/app/db/session.py
backend/app/api/__init__.py
backend/app/api/deps.py
backend/app/api/routes/__init__.py
backend/app/api/routes/health.py
backend/app/services/capabilities.py
backend/app/models/__init__.py
backend/app/schemas/__init__.py
backend/app/agents/__init__.py
backend/app/graph/__init__.py
backend/app/tools/__init__.py
backend/tests/conftest.py
backend/tests/test_config.py
backend/tests/test_health.py
backend/tests/test_db.py
backend/tests/test_capabilities.py
docs/PROJECT_CONTEXT.md
docs/ARCHITECTURE.md
docs/IMPLEMENTATION_PLAN.md
docs/DECISIONS.md
docs/API_SPEC.md
docs/DATABASE_SCHEMA.md
docs/AI_PIPELINE.md
docs/CURRENT_STATE.md
```

### Tests run

```
cd backend
python -m pytest
```

### Test results

All tests passing (see `CURRENT_STATE.md` for the exact count).

### Problems encountered

1. `faiss-cpu` has no CPython 3.14 wheel → replaced with a NumPy
   cosine-similarity `VectorStore` behind a replaceable interface (D-003).
2. Tesseract is installed at `C:\Program Files\Tesseract-OCR\tesseract.exe` but
   is **not on `PATH`** → added `TESSERACT_CMD` support plus Windows fallback
   candidate paths in `resolve_tesseract_cmd()`.
3. No local PostgreSQL → SQLite default with a portable schema (D-001).
4. No API keys present → health reports `configured: false` as a healthy state,
   and every service probe degrades instead of raising (D-009).

### Problems fixed

- Health route initially used a `Settings = None` default instead of FastAPI
  `Depends`; corrected to proper dependency injection.
- Removed a redundant `tests/fixtures.py` in favour of standard `conftest.py`
  fixtures.
- Cleaned up a leftover helper stub in `db/session.py` after an edit.

### Remaining issues

None blocking. `frontend/` is intentionally empty until Phase 11.

### Next step

Phase 1 — RedFlagEngine: 16 deterministic rules with configurable weights,
evidence-span capture, and unit tests including false-positive guards.
