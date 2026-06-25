# Feature: Pipeline Configuration (Analyst Persona + Reference Documents)

**Epic:** Epic 1 — Document Processing Pipeline (issue #1)
**Tracking issue:** #5
**Vision Brief:** specs/confidence-routed-extraction-vision.md

---

## Summary
Let an operator configure the extraction pipeline without code changes — set a persisted **analyst persona** and manage **reference documents** the AI draws on — so the system is domain-agnostic and tunable at runtime.

---

## Motivation
The pipeline is meant to be generic: *"the system is told what kind of analyst it is, making it domain-agnostic without code changes"* (Vision Brief capability #1). Today the analyst persona is a hard-coded `DEFAULT_PERSONA` in `pipeline.py`, overridable only per-request — there's no way to set "this deployment extracts legal documents as a paralegal" once and have every draft use it. And there's no way to give the model **reference material** to draw on (capability #2) — the operator can't save example documents or domain notes that improve extraction.

Feature 1a closes both gaps with a small configuration layer: a persisted persona and a managed set of reference documents, both injected into extraction. It's the last MVP feature of Epic 1 — deferred until the riskier extraction/review/reliability path was proven, now added to make the pipeline genuinely reusable across domains.

---

## User Stories & Acceptance Criteria

### US-1 — Configure the analyst persona
**As an** operator, **I want** to set and read a persisted analyst persona, **so that** every extraction uses my domain's analyst identity without code changes or per-request repetition.

**Acceptance Criteria:**
1. `AC-1.1` `[MUST]` `PUT /api/v1/config/persona` with `{"persona": "..."}` persists the analyst persona string.
2. `AC-1.2` `[MUST]` `GET /api/v1/config/persona` returns the currently configured persona, or the built-in default when none is set.
3. `AC-1.3` `[MUST]` When no persona is configured and none is passed per-request, extraction uses the existing `DEFAULT_PERSONA` (unchanged behavior).
4. `AC-1.4` `[MUST]` Precedence: a per-request `analyst_persona` (Feature 1b form field) overrides the persisted persona, which overrides the built-in default.
5. `AC-1.5` `[MUST]` An empty/whitespace-only persona is rejected with HTTP 422.

### US-2 — Manage reference documents
**As an** operator, **I want** to save, list, view, and delete reference documents, **so that** I curate the material the AI draws on when extracting.

**Acceptance Criteria:**
1. `AC-2.1` `[MUST]` `POST /api/v1/reference-docs` with `{"name": "...", "content": "..."}` saves a reference document and returns its `id`.
2. `AC-2.2` `[MUST]` `GET /api/v1/reference-docs` lists saved reference documents (id, name, content length or content).
3. `AC-2.3` `[MUST]` `GET /api/v1/reference-docs/{id}` returns one reference document; unknown id → HTTP 404.
4. `AC-2.4` `[MUST]` `DELETE /api/v1/reference-docs/{id}` removes one; unknown id → HTTP 404.
5. `AC-2.5` `[MUST]` `name` is required and non-empty; `content` is required; oversized content (> 50 KB) → HTTP 422.

### US-3 — Reference documents inform extraction
**As an** operator, **I want** saved reference documents included as context during extraction, **so that** drafts are more accurate for my domain.

**Acceptance Criteria:**
1. `AC-3.1` `[MUST]` When one or more reference documents are saved, their content is included in the extraction context (appended to the effective system/persona prompt) for every draft.
2. `AC-3.2` `[MUST]` When no reference documents are saved, extraction behaves exactly as before (no empty/“reference” scaffolding that changes output).
3. `AC-3.3` `[MUST]` Composing the effective persona + reference context is a pure function in `core/` (no FastAPI, no `sqlite3`).

### Global Acceptance Criteria
1. `AC-G.1` `[MUST]` All new `sqlite3` access lives in `db.py`; persona/reference composition lives in `core/` and is import-pure.
2. `AC-G.2` `[MUST]` Feature 1b's `run_extraction` signature/behavior is unchanged — the router resolves the effective persona and passes it in.

---

## Scope

### In Scope
- Persisted analyst-persona config (get/set) with default fallback and per-request precedence.
- Reference-document CRUD (create, list, view, delete) with validation.
- Inclusion of saved reference documents in the extraction context via a pure composition helper.
- New SQLite tables for config and reference documents.

### Out of Scope
- **Vector-DB / semantic retrieval of reference material** — Vision Brief "Later"; 1a includes *all* saved refs verbatim, no retrieval/ranking.
- Multiple named personas / profiles — a single active persona for the POC.
- Per-document-type or per-reviewer persona selection.
- Uploading binary reference files (PDF/images) — reference docs are text for this POC.
- Editing a reference document in place (delete + re-add for the POC).
- Auth / multi-tenant config isolation.

---

## Approach
A small config layer in front of the existing extraction path:

1. **Tables (`db.py`):**
   - `config` — `key TEXT PRIMARY KEY, value TEXT` (stores the persona under `analyst_persona`).
   - `reference_documents` — `id TEXT PRIMARY KEY, name TEXT, content TEXT, created_at TEXT`.
2. **`db.py` functions:** `get_config(key)`, `set_config(key, value)`; `add_reference_doc`, `list_reference_docs`, `get_reference_doc`, `delete_reference_doc`.
3. **`core/config.py` (pure):** `compose_persona(persona: str | None, reference_docs: list[ReferenceDoc]) -> str | None` — returns the persona with reference material appended (clearly delimited), or `None` when there's nothing to add (so `run_extraction` falls through to `DEFAULT_PERSONA`).
4. **New router `api/routers/config.py`:** the persona + reference-doc endpoints.
5. **Wire into extraction:** in the `create_draft` router, resolve `effective_persona = per_request or db.get_config("analyst_persona")`, fetch reference docs, `compose_persona(...)`, and pass the result to the unchanged `run_extraction(..., analyst_persona=...)`.

`run_extraction` is untouched — the router composes the effective persona.

---

## Data & Validation

**`ReferenceDoc` (Pydantic):**

| Field | Type | Required | Rules / limits |
|-------|------|----------|----------------|
| `id` | text | yes (out) | Server-generated UUID |
| `name` | text | yes (in) | Non-empty; ≤ 200 chars |
| `content` | text | yes (in) | Non-empty; ≤ 50 KB |
| `created_at` | datetime | yes (out) | ISO 8601 |

**`PersonaConfig` (Pydantic):** `persona: str` — non-empty after strip.

**`config` table:** `key` PK, `value`. **`reference_documents` table:** `id` PK, `name`, `content`, `created_at`.

**Cross-field & business rules:**
- Persona precedence: per-request > persisted > `DEFAULT_PERSONA`.
- Reference docs are included in extraction order-by `created_at` ascending.

---

## Non-Functional Requirements
- **Performance:** `NFR-1` `[MUST]` Config and reference-doc endpoints complete in under 200ms (local DB only, no Claude calls).
- **Purity:** `NFR-2` `[MUST]` `core/config.py` imports neither FastAPI nor `sqlite3`.
- **Compatibility:** `NFR-3` `[MUST]` With no persona configured and no reference docs saved, extraction output and the `run_extraction` contract are unchanged from pre-1a (backward-compatible default).
- **Security:** `NFR-4` `[MUST]` Reference-doc `content` is validated server-side (size cap) before persistence.

---

## Error States

| ID | Scenario | Expected Behavior | Priority |
|----|----------|-------------------|----------|
| `ERR-1` | `PUT /config/persona` with empty/whitespace persona | HTTP 422 `{"error": "invalid_persona"}` | `[MUST]` |
| `ERR-2` | `POST /reference-docs` missing name or content | HTTP 422 `{"error": "invalid_reference_doc"}` | `[MUST]` |
| `ERR-3` | `POST /reference-docs` content > 50 KB | HTTP 422 `{"error": "reference_doc_too_large"}` | `[MUST]` |
| `ERR-4` | `GET`/`DELETE /reference-docs/{id}` unknown id | HTTP 404 `{"error": "reference_doc_not_found"}` | `[MUST]` |

---

## Success Metrics & Instrumentation
- **Primary metric:** An operator can set a persona + add a reference doc, then a subsequent draft uses both (verifiable: the composed system prompt contains the persona and the reference content).
- **Secondary metrics:** Count of saved reference docs; whether a persona is configured.
- **Events to track:** `persona_updated`, `reference_doc_added` (id, name, content_length), `reference_doc_deleted` (id).
- **Evaluation timeline:** Manual verification at first working build.

---

## Dependencies & Prerequisites
- **Feature 1b — merged.** `run_extraction` + `DEFAULT_PERSONA` + the `create_draft` router this wires into.
- `db.py` for the new tables/functions.

---

## Migration & Rollback
- **Migration plan:** Additive — two new tables created by `init_db()` on startup (POC pattern, no migration tooling). No change to existing tables.
- **Rollback plan:** Remove the config router + composition wiring; `run_extraction` and existing tables are untouched.
- **Backwards compatibility:** With no config/refs, behavior is identical to pre-1a (NFR-3).

---

## Design Constraints
- **`core/` stays pure; all `sqlite3` in `db.py`.**
- **`run_extraction` unchanged** — the router composes the effective persona and passes it in.
- **Reference docs are text-only**, included verbatim (no retrieval) for the POC.
- **Single active persona** — no named profiles.
- **Additive, backward-compatible** — zero behavior change when unconfigured.

---

## Verification

### Persona config
1. `GET /api/v1/config/persona` before setting → returns the built-in default. _(AC-1.2, AC-1.3)_
2. `PUT /api/v1/config/persona` with a custom persona → 200; `GET` now returns it. _(AC-1.1)_
3. `PUT` with `"   "` → HTTP 422 `invalid_persona`. _(AC-1.5, ERR-1)_

### Reference docs
4. `POST /api/v1/reference-docs` {name, content} → 200 with `id`; `GET /reference-docs` lists it; `GET /reference-docs/{id}` returns it. _(AC-2.1, AC-2.2, AC-2.3)_
5. `DELETE /reference-docs/{id}` → 200; subsequent `GET /{id}` → 404. _(AC-2.4, ERR-4)_
6. `POST` missing content → 422; content > 50 KB → 422. _(AC-2.5, ERR-2, ERR-3)_

### Extraction composition (pure)
7. `compose_persona("You are a paralegal.", [ref])` returns a string containing the persona and the reference content, clearly delimited. _(AC-3.1, AC-3.3)_
8. `compose_persona(None, [])` returns `None` (extraction falls back to `DEFAULT_PERSONA`). _(AC-3.2, NFR-3)_
9. With a configured persona + one reference doc, a draft request builds an effective system prompt containing both (verified at the router/compose boundary without a live Claude call). _(AC-1.4, AC-G.2)_

### Purity
10. `core/config.py` imports no FastAPI/sqlite3. _(AC-G.1, NFR-2)_

## Definition of Done
- [ ] All `[MUST]` acceptance criteria pass (US, Global, NFR, Error State)
- [ ] Every `[MUST]` criterion is covered by a verification step that has actually been run
- [ ] `pyright src/`, `ruff check`, and `pytest` pass (new unit + integration tests)
- [ ] NFR-3 backward-compatibility verified (unconfigured = unchanged)
- [ ] Instrumentation events fire and are observable
- [ ] CLAUDE.md repo-structure updated (`core/config.py`, new router, new tables)
- [ ] No `sqlite3`/FastAPI in `core/`; `run_extraction` untouched

## Open Questions
_All resolved 2026-06-25 at spec time._
1. **Persona scope — RESOLVED: single active persona.** One configured persona for the deployment; per-request override > persisted > built-in default. Named profiles are a future consideration.
2. **Reference-doc inclusion — RESOLVED: all saved docs, verbatim.** Every saved reference doc is appended to the extraction context (no retrieval/ranking). Vector-DB selection stays "Later".

## Future Considerations
- Vector-DB / semantic retrieval to select only the most relevant reference docs (Vision Brief "Later").
- Multiple named analyst profiles, switchable per request or per document type.
- Binary/file reference documents (PDF, images) with extraction.
- Editing reference docs in place; versioning.

## Revision History
| Date | Change | Author |
|------|--------|--------|
| 2026-06-25 | Initial draft | Eric Rooney / Claude |
