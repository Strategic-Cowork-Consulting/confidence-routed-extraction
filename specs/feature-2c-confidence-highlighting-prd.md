# Feature: Confidence Highlighting (Per-Field Review Flags)

**Epic:** Epic 2 — Reliability Learning Loop (issue #2)
**Tracking issue:** #8
**Vision Brief:** specs/confidence-routed-extraction-vision.md

---

## Summary
At review time, annotate each drafted field with its reliability **routing** and a **flagged** signal — derived from Feature 2b — so the external review interface can highlight (e.g. red) exactly the fields the AI is unreliable on, closing the learning loop.

---

## Motivation
Feature 2b can already answer *"how should field X on a document of type Y be reviewed?"* via `GET /api/v1/reliability/route`. But a reviewer doesn't query the model field-by-field — they open a document and want the unreliable fields **already marked** on the draft in front of them. Nothing yet attaches that signal to the draft a reviewer actually fetches.

Feature 2c is that last link. When a draft is fetched for review, it annotates each field with the routing decision for that field's `(field_name, document_type)` bucket — `mandatory_review` / `light_review`, the basis, and a boolean `flagged`. The external UI reads those flags and renders the highlight. This realizes Vision Brief capability #6 — *"fields the AI consistently gets wrong are flagged red in future reviews"* — and completes the loop the Vision Brief describes: edits → reliability → **attention directed to the right fields**.

This project is an **API, not a UI** (per CLAUDE.md Scope Boundary), so 2c delivers the *flags*; rendering them red is the external interface's job.

---

## User Stories & Acceptance Criteria

### US-1 — Per-field flags on the review draft
**As a** review interface, **I want** each field on a fetched draft annotated with its reliability routing and a flagged signal, **so that** I can highlight the unreliable fields without extra round-trips.

**Acceptance Criteria:**
1. `AC-1.1` `[MUST]` Fetching a draft for review returns, for **every** field in the draft, a flag record containing `field_name`, `document_type`, `routing`, `basis`, `flagged`, and `samples`.
2. `AC-1.2` `[MUST]` Each field's flag is computed against **its own** `document_type` (derived from that field's `source_document` → the matching `DocumentResult.document_type`), not a single document-level type — so a multi-document portfolio flags each field by the right bucket.
3. `AC-1.3` `[MUST]` `flagged == (routing == "mandatory_review")` — the boolean "red" signal.
4. `AC-1.4` `[MUST]` Routing/basis come from Feature 2b (`reliability.route_for` over `score_buckets(db.get_bucket_counts(...))`); 2c does not re-implement scoring, thresholds, or the routing rule.
5. `AC-1.5` `[MUST]` The flags reflect the **current** reliability model at fetch time (derived on read — a new approval immediately affects subsequent fetches).

### US-2 — Safe-default flagging for unproven / unseen field-groups
**As a** reviewer, **I want** fields the system hasn't learned yet to be flagged for mandatory review, **so that** nothing unverified slips through with light review.

**Acceptance Criteria:**
1. `AC-2.1` `[MUST]` A field whose bucket has `samples < min_sample_threshold` is `flagged = true`, basis `unproven`.
2. `AC-2.2` `[MUST]` A field whose `(field_name, document_type)` bucket has **no observations** (unseen) is `flagged = true`, basis `unproven`, `samples = 0`.
3. `AC-2.3` `[MUST]` A field whose `document_type` resolves to `"unknown"` (unresolved source) is flagged via the `(field_name, "unknown")` bucket — never dropped or silently light-reviewed.

### US-3 — Flag summary for the reviewer
**As a** reviewer, **I want** a count of how many fields are flagged on a document, **so that** I know at a glance how much scrutiny it needs.

**Acceptance Criteria:**
1. `AC-3.1` `[SHOULD]` The review response includes a `flagged_count` and `total_fields` summary.

### Global Acceptance Criteria
1. `AC-G.1` `[MUST]` Flag computation reuses `core/reliability.py` (pure) and a pure `document_type`-per-field helper; the router adds no scoring/threshold logic and no `sqlite3`.
2. `AC-G.2` `[MUST]` No new storage — flags are derived on read from `db.get_bucket_counts`.
3. `AC-G.3` `[MUST]` The existing draft/approval behavior of the review endpoint is unchanged; flags are **additive** to the response.

---

## Scope

### In Scope
- Per-field flag annotation (`routing`, `basis`, `flagged`, `samples`, `document_type`) on the review-fetch response.
- A pure helper mapping each draft field to its `document_type` (extracted/shared from the 2a derivation), reused by both 2a and 2c.
- A flag summary (`flagged_count`, `total_fields`).
- Reuse of 2b's scoring/routing and the locked thresholds.

### Out of Scope
- **Any rendering / CSS / "red" styling** — the external review UI owns presentation. 2c returns flags only.
- Scoring, thresholds, or the routing rule — owned by Feature 2b; consumed as-is.
- Capturing observations (2a) or computing the model (2b).
- Changing the touch/approval flow, or re-flagging after approval.
- Persisting flags or a per-field highlight history.
- Reviewer-specific or document-specific threshold overrides on the review response (the model's defaults apply; experimentation stays on `GET /api/v1/reliability`).

---

## Approach
Flags are derived on read at review-fetch time:

1. **Shared `document_type`-per-field helper.** Extract the per-field `document_type` derivation currently inside `core/observations.build_observations` into a small pure helper (e.g. `observations.field_document_types(draft) -> dict[field_name, document_type]`, `"unknown"` fallback) and reuse it in both 2a and 2c — single source of truth, no duplication.
2. **Compute flags.** In the review-fetch path: `scores = reliability.score_buckets(db.get_bucket_counts(DEFAULT_GROUP_KEY))`; for each field, `route_for(field_name, field_document_type, scores)` → a flag record with `flagged = routing == "mandatory_review"`.
3. **Attach additively.** Add a `field_flags: dict[str, FieldFlag]` (keyed by `field_name`) plus a small summary to the review-fetch response. The existing draft + `approval_status` are unchanged (AC-G.3).
4. **Surface point — open question (below):** annotate the existing `GET /api/v1/review/{document_id}` response, or add a dedicated `GET /api/v1/review/{document_id}/flags`. *Recommend additive on the existing endpoint* so the UI gets draft + flags in one fetch.

No new tables, no change to 2a/2b logic.

---

## Data & Validation

**`FieldFlag` (Pydantic, response model — not persisted):**

| Field | Type | Required | Rules / limits |
|-------|------|----------|----------------|
| `field_name` | text | yes | Matches a draft field key |
| `document_type` | text | yes | The field's resolved type (`"unknown"` allowed) |
| `routing` | enum | yes | `mandatory_review` \| `light_review` (from 2b) |
| `basis` | enum | yes | `unproven` \| `high_change_rate` \| `low_change_rate` |
| `flagged` | bool | yes | `routing == "mandatory_review"` |
| `samples` | int | yes | ≥ 0 (0 = unseen bucket) |

**Review-fetch response additions:** `field_flags: dict[str, FieldFlag]` (one entry per draft field), `flagged_count: int`, `total_fields: int`.

**Cross-field & business rules:**
- `field_flags` keys == the draft's `fields` keys exactly (one flag per field).
- `flagged_count == sum(f.flagged)`, `total_fields == len(fields)`.

---

## Non-Functional Requirements
- **Performance:** `NFR-1` `[MUST]` Flag computation adds under 100ms to the review fetch (one `get_bucket_counts` read + in-memory mapping; no Claude calls).
- **Purity:** `NFR-2` `[MUST]` Flag logic lives in `core/` (reliability + the shared derivation helper); the router holds no scoring/threshold/`sqlite3` code.
- **Correctness:** `NFR-3` `[MUST]` For every field, `flagged == (routing == "mandatory_review")` and the routing matches 2b's truth table for that field's bucket — verified against seeded buckets.
- **Compatibility:** `NFR-4` `[MUST]` The review-fetch response remains backward-compatible — all pre-2c fields (draft, `approval_status`) are present and unchanged; flags are additive.

---

## Error States

| ID | Scenario | Expected Behavior | Priority |
|----|----------|-------------------|----------|
| `ERR-1` | `document_id` not found at review fetch | HTTP 404 `{"error": "draft_not_found"}` (unchanged 1c behavior) | `[MUST]` |
| `ERR-2` | No reliability data yet (empty observation store) | Every field `flagged = true`, basis `unproven` (cold-start → all mandatory) | `[MUST]` |
| `ERR-3` | A field's `document_type` is `"unknown"` (unresolved source) | Flagged via the `(field_name, "unknown")` bucket; never dropped | `[MUST]` |
| `ERR-4` | A field's bucket is below the sample threshold | `flagged = true`, basis `unproven` | `[MUST]` |

---

## Success Metrics & Instrumentation
- **Primary metric:** On a real review fetch, the field(s) the model knows are unreliable come back `flagged = true` and the reliably-extracted fields come back `flagged = false` (e.g. on the live-probe data: `gpa` flagged, `degree`/`name`/`graduation_date` not).
- **Secondary metrics:** Share of fields flagged per document (the reviewer-attention load); trend down as the model proves more buckets reliable.
- **Events to track:** `flags_served` (document_id, total_fields, flagged_count).
- **Evaluation timeline:** Manual verification on the live-probe store at first working build; revisit once an external UI consumes the flags.

---

## Dependencies & Prerequisites
- **Feature 2b (#7) — merged.** `reliability.score_buckets`, `route_for`, `db.get_bucket_counts`.
- **Feature 2a (#6) — merged.** The per-field `document_type` derivation to extract/share.
- **Feature 1c — merged.** The review-fetch endpoint this annotates.
- **#13 — merged.** Correct `document_type` labels, so per-field flags map to genuinely separated buckets.
- **Locked decisions (#9):** thresholds (5 / 0.20), grouping key, unproven/unseen → mandatory — all inherited from 2b, not re-decided here.

---

## Migration & Rollback
- **Migration plan:** Additive only — a new `field_flags` map + summary on the review-fetch response. No DB change, no URL change. Existing consumers that ignore the new fields are unaffected.
- **Rollback plan:** Remove the additive fields / revert the router change; 2a/2b and the review/approve flow are untouched.
- **Backwards compatibility:** All pre-2c response fields remain present and identical (NFR-4).

---

## Design Constraints
- **API only — no rendering.** 2c returns flags; the external UI styles them. No HTML/CSS/templates.
- **Reuse, don't re-implement.** Routing/scoring/thresholds come from `core/reliability.py`; the `document_type`-per-field mapping is shared with 2a, not copied.
- **`core/` stays pure; all `sqlite3` in `db.py`.**
- **Additive, backward-compatible** change to the review-fetch contract.
- **No new persistence** — derived on read.
- **Safe default is inviolable** — unproven/unseen/`unknown` fields are always flagged (locked principle #9).

---

## Verification

### Happy path (flags on a real review fetch)
1. With the live-probe store present (gpa change-rate ≈ 0.67 over 6 transcript samples), fetch a transcript draft for review → `field_flags["gpa"].flagged == true` (basis `high_change_rate`); `field_flags["degree"].flagged == false` (basis `low_change_rate`). _(AC-1.1, AC-1.3, AC-1.4, NFR-3)_
2. The response still contains the draft fields and `approval_status` unchanged. _(AC-G.3, NFR-4)_

### Per-field document_type (multi-doc)
3. Fetch a draft whose fields come from two document types → each field's flag uses its own `document_type` bucket. _(AC-1.2)_

### Safe defaults
4. With an **empty** observation store, fetch any draft → every field `flagged = true`, basis `unproven`. _(AC-2.1, ERR-2)_
5. A field with no observations for its bucket → flagged, basis `unproven`, `samples = 0`. _(AC-2.2)_
6. A field whose `source_document` is unresolved → flag uses `document_type = "unknown"`, still present. _(AC-2.3, ERR-3)_
7. A field whose bucket has `samples` below threshold → flagged, basis `unproven`. _(ERR-4)_

### Summary + not found
8. `flagged_count` equals the number of flagged fields; `total_fields` equals the field count. _(AC-3.1)_
9. Fetch with a non-existent `document_id` → HTTP 404 `draft_not_found`. _(ERR-1)_

### Purity
10. The flag-computation code imports no `sqlite3`/FastAPI in `core/`; repeated fetches yield identical flags for an unchanged store. _(AC-G.1, NFR-2)_

## Definition of Done
- [ ] All `[MUST]` acceptance criteria pass (US, Global, NFR, Error State)
- [ ] Every `[MUST]` criterion is covered by a verification step that has actually been run
- [ ] `pyright src/`, `ruff check`, and `pytest` pass (new unit + integration tests)
- [ ] NFR-1 latency and NFR-3 correctness measured, not assumed
- [ ] `flags_served` instrumentation fires and is observable
- [ ] CLAUDE.md updated if the review response shape changes
- [ ] 2a refactored to share the `document_type`-per-field helper (no duplicated derivation)
- [ ] No `sqlite3`/FastAPI in `core/`; response backward-compatible

## Open Questions
_All resolved 2026-06-25 at spec time._
1. **Surface point — RESOLVED: additive on the existing endpoint.** `field_flags` + summary are added to the `GET /api/v1/review/{document_id}` response, so the UI gets draft + flags in one fetch. No dedicated `/flags` endpoint.
2. **Threshold overrides on review fetch — RESOLVED: locked defaults only.** Review-fetch flags always use the locked POC defaults (5 / 0.20); threshold experimentation stays on `GET /api/v1/reliability`.

## Future Considerations
- A per-field reliability *number* (not just flag) for richer UI shading (e.g. heat scale) — the score is already computed.
- Reviewer-facing explanation strings ("corrected on 67% of past transcripts").
- Feeding reliability back into extraction prompting to reduce errors, not just flag them (Vision Brief).
- Persist a per-document snapshot of flags shown, for audit / model-drift analysis.

## Revision History
| Date | Change | Author |
|------|--------|--------|
| 2026-06-25 | Initial draft | Eric Rooney / Claude |
