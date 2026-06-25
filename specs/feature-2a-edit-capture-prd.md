# Feature: Edit Capture (Per-Field Touch Observations)

**Epic:** Epic 2 — Reliability Learning Loop (issue #2)
**Tracking issue:** #6
**Vision Brief:** specs/confidence-routed-extraction-vision.md

---

## Summary
At approval time, emit one queryable **touch observation** per field — tagged with the configurable grouping dimensions (POC default `field × document_type`) — so the reliability model (Feature 2b) has normalized, groupable training data instead of per-document blobs.

---

## Motivation
Feature 1c already computes a per-field touched/untouched record and stores it as a per-document `touch_json` blob in the `approvals` table, keyed by `document_id`. That blob is enough to answer "what changed on *this* document," but it is the wrong shape for learning: there is no way to ask "across all documents, how often is the `gpa` field on a `transcript` touched?" without scanning and re-parsing every approval row and re-deriving each field's document type.

The reliability model (2b) needs exactly that cross-document, per-bucket view. Feature 2a is the capture layer that produces it: it flattens each approval into one observation row per field, attaches the grouping dimensions the model will aggregate over, and persists them in a table built to be `GROUP BY`-ed. Without 2a, 2b has correct raw signal trapped in an unqueryable form.

This directly serves the Vision Brief's core loop ("every approved document's diff is recorded; change rates aggregate into a per-field reliability score") and its stated biggest risk (overfitting / cold-start): by tagging every observation with `document_type`, unseen buckets become identifiable, which is what lets downstream routing default new combinations to mandatory review rather than inheriting another bucket's score.

---

## User Stories & Acceptance Criteria

### US-1 — Capture touch observations at approval
**As a** reliability pipeline, **I want** each approval to emit one touch observation per field, tagged with its grouping dimensions, **so that** the reliability model has groupable per-field training data.

**Acceptance Criteria:**
1. `AC-1.1` `[MUST]` When an approval is recorded, the system writes exactly one observation row per field present in the original draft's `fields` map.
2. `AC-1.2` `[MUST]` Each observation carries: `document_id`, `field_name`, `document_type`, `touched` (boolean), and `approved_at`.
3. `AC-1.3` `[MUST]` `touched` for each field equals the value already computed by `core/diff.py: compute_touches` for that approval — 2a does not recompute or change the touch definition, it only persists per-field rows. (Counts all non-whitespace touches; whitespace already normalized in 1c.)
4. `AC-1.4` `[MUST]` `document_type` is derived by mapping the field's `source_document` to the matching `DocumentResult.filename` in the draft and reading that document's `document_type`.
5. `AC-1.5` `[MUST]` If a field's `source_document` matches no `DocumentResult` (or is empty), `document_type` is recorded as the literal `"unknown"` — the observation is still written, never dropped.
6. `AC-1.6` `[MUST]` Observation construction lives in `core/` as a pure function — no FastAPI, no `sqlite3` — taking the draft + computed touch record and returning a list of observation objects.
7. `AC-1.7` `[MUST]` Capture and the approval write happen in **one transaction** — a single `db.save_approval(...)` call persists the approval row and all its observations together, so an approval can never exist without its matching observations (and vice versa). **Deliberate consequence:** if the observation write fails, the approval write rolls back with it and the approve request fails (HTTP 500); the reviewer's approval is **not** persisted and must be resubmitted. Accepted POC trade-off — integrity of the training signal over approval durability.

### US-2 — Backfill observations from existing approvals
**As a** reliability pipeline, **I want** to derive observations from approvals that were recorded before 2a existed, **so that** already-approved documents contribute to the model from day one.

**Acceptance Criteria:**
1. `AC-2.1` `[MUST]` A backfill routine reads existing `approvals` rows, reconstructs per-field observations from the stored draft + `touch_json`, and writes any that are missing.
2. `AC-2.2` `[MUST]` Backfill is idempotent — running it more than once never creates duplicate observations for the same `(document_id, field_name)`.
3. `AC-2.3` `[SHOULD]` Backfill reports how many approvals it processed and how many observation rows it wrote.

### US-3 — Read observations for aggregation
**As a** reliability model (Feature 2b) **and** a reviewer of POC progress, **I want** to read stored observations, optionally filtered by grouping dimensions, **so that** I can aggregate touch rates and see which buckets are approaching the sample threshold.

**Acceptance Criteria:**
1. `AC-3.1` `[MUST]` `db.py` exposes a read that returns observations, with optional filtering by `field_name` and/or `document_type`.
2. `AC-3.2` `[MUST]` A read grouped by the **configurable grouping key** (POC default `["field_name", "document_type"]`) returns, per bucket, the sample count and touched count.
3. `AC-3.3` `[MUST]` The grouping key is read from a single configurable location — no hardcoded `field × document_type` assumption buried in query logic. (The value is *consumed* mainly by 2b; 2a stores enough dimensions to support it and must not foreclose it.)

### Global Acceptance Criteria
1. `AC-G.1` `[MUST]` All `sqlite3` access for observations lives in `db.py`; `core/` observation logic is import-pure (no FastAPI, no `sqlite3`).
2. `AC-G.2` `[MUST]` Observations are immutable once written — never updated or deleted — preserving training-signal integrity (consistent with immutable approvals).

---

## Scope

### In Scope
- A `field_observations` table (one row per field per approved document).
- A pure `core/` function that turns `(DocumentDraft, touch_record)` into observation objects, including `document_type` derivation and the `"unknown"` fallback.
- Hooking capture into the approval flow so every new approval emits observations.
- An idempotent backfill over existing approvals.
- `db.py` reads for observations, including a grouped count read keyed by the configurable grouping key.
- A single configurable definition of the grouping key (default `["field_name", "document_type"]`).

### Out of Scope
- Computing reliability scores / change rates into a model — **Feature 2b**.
- Routing decisions or confidence highlighting — **Feature 2c**.
- The minimum-sample-threshold *decision logic* (the threshold value is owned by 2b/2c); 2a only stores the per-bucket counts that make the threshold checkable.
- Semantic filtering of cosmetic edits (LLM-as-judge) — deferred per locked design decision #2.
- Changing the touch definition or whitespace rules — owned by `core/diff.py` (Feature 1c).
- New grouping dimensions beyond `field × document_type` (e.g. `country`, `language`) — the store must not foreclose them, but adding them is a future iteration.
- Reviewer identity, auth, UI.

---

## Approach
2a sits immediately after 1c's `save_approval`. Feature 1c's approve flow already produces, in one place, both the original `DocumentDraft` and the computed `dict[str, bool]` touch record. 2a adds:

1. **`core/observations.py` (pure):** `build_observations(draft, touches, approved_at) -> list[FieldObservation]`. For each field in `draft.fields`, resolve `document_type` by matching `field.source_document` against `draft.documents[*].filename`; fall back to `"unknown"`. Pair it with the field's `touched` flag and the approval timestamp.
2. **`field_observations` table** with a composite primary key `(document_id, field_name)` — this is what makes both capture and backfill idempotent for free.
3. **Combined write (AC-1.7):** `db.save_approval(...)` is extended to persist the approval row **and** its observation rows inside a **single transaction** (one `_connect()` context). The approve handler builds the observations via `core.build_observations` and passes them to `save_approval` — so the two writes commit or fail together. No separate `save_observations` call in the request path.
4. **`db.get_observations(...)`** and **`db.get_bucket_counts(group_key)`** reads for 2b and verification.
5. **Backfill** lives in a dedicated orchestration module **`src/extraction/backfill.py`** (`run_backfill()` + `python -m extraction.backfill`) — it iterates existing approvals via a `db` read, rebuilds observations with the same pure `core.build_observations`, and `INSERT OR IGNORE`s them. Orchestration sits outside `db.py` (which stays SQL-only) and outside `core/` (which stays I/O-free). Backfill uses a dedicated `db.save_observations(rows)` insert (separate from the combined approval write, since there is no new approval to write).

The grouping key is defined once as a module-level `DEFAULT_GROUP_KEY = ("field_name", "document_type")` and passed into the grouped read — never inlined as literal column names in business logic.

---

## Data & Validation

**`FieldObservation` (new Pydantic model in `schemas/`):**

| Field | Type | Required | Rules / limits |
|-------|------|----------|----------------|
| `document_id` | text | yes | Must reference an existing `approvals.document_id` |
| `field_name` | text | yes | Field key from the draft's `fields` map |
| `document_type` | text | yes | Derived from source document; literal `"unknown"` when unresolved |
| `touched` | boolean | yes | Copied from `compute_touches`, not recomputed |
| `approved_at` | datetime (ISO 8601) | yes | The approval timestamp |

**`field_observations` table:**
```sql
CREATE TABLE IF NOT EXISTS field_observations (
    document_id   TEXT NOT NULL,
    field_name    TEXT NOT NULL,
    document_type TEXT NOT NULL,   -- "unknown" when source doc unresolved
    touched       INTEGER NOT NULL,-- 0 / 1
    approved_at   TEXT NOT NULL,
    PRIMARY KEY (document_id, field_name),
    FOREIGN KEY (document_id) REFERENCES approvals(document_id)
)
```
*Extension path for finer granularity later: add nullable `country` / `language` columns; the grouping-key config selects which columns 2b groups by. Storing dimensions as columns (not a JSON blob) keeps the grouped count query a plain `GROUP BY`.*

**Cross-field & business rules:**
- Exactly one observation per `(document_id, field_name)`; re-deriving the same approval is a no-op.
- The set of `field_name`s for a document equals the key set of the draft's `fields` map (same set 1c validates the approval payload against).

---

## Non-Functional Requirements
- **Performance:** `NFR-1` `[MUST]` Capture adds under 50ms to the approval request for a typical draft (≤ ~50 fields) — pure local DB writes, no Claude calls.
- **Correctness:** `NFR-2` `[MUST]` For any approval, the multiset of `(field_name, touched)` in `field_observations` exactly matches the approval's stored `touch_json`. Verified by comparing the two for a sample approval.
- **Idempotency:** `NFR-3` `[MUST]` Running capture or backfill twice over the same approval yields the same rows and the same row count (no duplicates, no errors).
- **Reliability:** `NFR-4` `[MUST]` An observation-write failure rolls back the **entire** approval transaction (approval row + observations) and surfaces as HTTP 500 — no partial state is persisted, and the reviewer resubmits. Deliberate (see AC-1.7): for this POC we prefer losing an approval over recording one whose training signal didn't persist.

---

## Error States

| ID | Scenario | Expected Behavior | Priority |
|----|----------|-------------------|----------|
| `ERR-1` | A field's `source_document` matches no `DocumentResult` (or is empty) | Observation written with `document_type = "unknown"`; nothing dropped | `[MUST]` |
| `ERR-2` | Capture runs for a `document_id` that already has observations | No-op / no duplicates (idempotent insert); no error raised | `[MUST]` |
| `ERR-3` | Backfill encounters an approval whose draft can't be loaded | Skip that approval, record it in the backfill report, continue; do not abort the whole run | `[MUST]` |
| `ERR-4` | Observation / DB write fails during an approval | **Atomic rollback** — approval row and observations both roll back; nothing persisted; request returns HTTP 500; reviewer resubmits | `[MUST]` |

---

## Success Metrics & Instrumentation
- **Primary metric:** After approving N test documents, `field_observations` contains one row per field per document, correctly bucketed by `document_type`.
- **Secondary metrics:** Number of distinct `(field_name, document_type)` buckets, and how many have reached ≥ 5 samples (the POC threshold owned by 2b) — i.e. how close the demo is to the routing feature activating.
- **Events to track:** `observations_written` (`document_id`, `field_count`, `touched_count`, distinct `document_type` count); `backfill_run` (`approvals_processed`, `rows_written`, `skipped`).
- **Evaluation timeline:** Manual verification at first working build over 3–5 test documents spanning at least two `document_type`s.

---

## Dependencies & Prerequisites
- Feature 1c merged — `approvals` table, `compute_touches`, and the approve flow that holds both the draft and the touch record.
- `DocumentDraft`, `FieldValue`, `DocumentResult` schemas (Feature 1b) importable.
- Design decisions #9 locked (grouping key configurable; count all non-whitespace touches; threshold semantics) — done 2026-06-25.

---

## Design Constraints
- **`core/` stays pure** — observation construction and `document_type` derivation take Pydantic/dicts in and return objects out; no `sqlite3`, no FastAPI.
- **All `sqlite3` in `db.py`** — `save_observations`, `get_observations`, `get_bucket_counts` and nothing elsewhere.
- **Do not re-implement touch logic** — 2a consumes `compute_touches` output verbatim; the touch definition stays solely in `core/diff.py`.
- **No baked-in granularity** — grouping key defined once and passed in; query logic must not hardcode `field × document_type`.
- **Immutable observations** — append-only; never updated or deleted.
- **Atomic capture** — `db.save_approval` is extended to write the approval row and its observations in one transaction (AC-1.7); the approve handler change is limited to building observations and passing them in, not restructuring the endpoint's validation/flow.
- **Backfill isolated** — backfill orchestration lives in `src/extraction/backfill.py`, composing `db` (SQL) + `core` (pure derivation); it does not put SQL in `core/` or composition logic in `db.py`.

---

## Verification

### Happy path (capture at approval)
1. `POST /draft` with two source files whose detected `document_type`s differ (e.g. a `transcript` and a `diploma`) and a schema covering fields sourced from each → note `document_id`. _(setup)_
2. `POST /review/{document_id}/approve` changing one field and leaving another identical. _(setup)_
3. Read `field_observations` for that `document_id` → one row per draft field, each with the correct `document_type` and a `touched` flag matching the approval's `touch_summary`. _(AC-1.1, AC-1.2, AC-1.3, AC-1.4, NFR-2)_

### Unknown document type
1. Approve a draft containing a field whose `source_document` matches no `DocumentResult`.
2. Its observation row has `document_type = "unknown"` and is present (not dropped). _(AC-1.5, ERR-1)_

### Idempotent capture / backfill
1. Run backfill over a DB with existing approvals → observation rows appear; report lists counts. _(AC-2.1, AC-2.3)_
2. Run backfill again → row count unchanged, no error. _(AC-2.2, NFR-3, ERR-2)_

### Grouped read
1. After approving several documents across ≥ 2 document types, call the grouped count read with the default key. _(AC-3.2)_
2. Each `(field_name, document_type)` bucket returns sample count and touched count consistent with the raw rows. _(AC-3.1, AC-3.3)_

### Backfill resilience
1. Point backfill at a set of approvals where one draft is unloadable → that one is skipped and reported; the rest are written. _(ERR-3)_

---

## Definition of Done
- [ ] All `[MUST]` acceptance criteria pass (US, Global, NFR, Error State)
- [ ] Every `[MUST]` criterion is covered by a verification step that has actually been run
- [ ] `pyright src/`, `ruff check`, and `pytest tests/unit/` pass
- [ ] NFR-1 latency and NFR-2/NFR-3 correctness/idempotency measured, not assumed
- [ ] `observations_written` / `backfill_run` instrumentation fires and is observable
- [ ] CLAUDE.md repo-structure note updated if `core/observations.py` is added
- [ ] No `sqlite3` outside `db.py`; `core/` import-pure (verified)

---

## Open Questions
_All resolved 2026-06-25 at spec time._
1. **Capture timing — RESOLVED: table + backfill.** Write observations at approval time into `field_observations`, plus an idempotent backfill over existing approvals. (Rejected: derive-on-read — couples 2b to raw blob parsing and re-derives `document_type` on every aggregation.)
2. **Config ownership — RESOLVED.** 2a stores all available dimensions and owns `DEFAULT_GROUP_KEY = ("field_name", "document_type")`; 2b owns *consumption* (which key to group by) and the sample threshold. `count_cosmetic_edits` is effectively `true` already via 1c — exposed as a documented constant in 2a, promoted to full config in 2b.
3. **`approved_at` source — RESOLVED.** Use `approvals.approved_at` (ties the observation to the approval event), not the draft's `created_at`.

---

## Future Considerations
- Finer grouping dimensions (`country`, `language`) once data volume supports them — add columns, extend the key.
- Per-reviewer attribution on observations (management accuracy-dashboard use case from the Vision Brief).
- Semantic cosmetic-edit filtering to distinguish real errors from rewordings (deferred design decision #2).
- A vector-DB-backed context signal feeding extraction quality (Vision Brief "Later").

---

## Revision History
| Date | Change | Author |
|------|--------|--------|
| 2026-06-25 | Initial draft | Eric Rooney / Claude |
| 2026-06-25 | Fold in planning decisions: AC-1.7 → MUST atomic (combined `save_approval` transaction); backfill pinned to `src/extraction/backfill.py` | Eric Rooney / Claude |
| 2026-06-25 | Document atomic-rollback behavior explicitly (AC-1.7 / NFR-4 / ERR-4): observation-write failure rolls back the approval and returns HTTP 500. **Process note:** sequential→atomic was a deviation from the approved (sequential) plan, flagged late; reviewed and accepted by Eric, kept for cleanliness. | Eric Rooney / Claude |
