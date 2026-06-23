# Feature: Review and Approval

**Epic:** Epic 1 — Document Processing Pipeline
**Vision Brief:** specs/confidence-routed-extraction-vision.md

---

## Summary

Expose two endpoints — one to fetch a stored draft by `document_id`, one to submit the reviewer's approved field values — and at approval time compute and persist a per-field touch record that serves as the training signal for the reliability model (Epic 2).

---

## Motivation

Feature 1b generates a draft and persists it. But a draft sitting in the database produces no value and no learning signal until a human reviews it and submits corrections. Feature 1c closes that loop: it gives the review interface a way to retrieve the draft, and gives the reviewer a way to hand back their corrected values. At that moment — and only at that moment — the system can compute which fields the AI got right versus which ones the human had to fix. That per-field touch record is the ground-truth label for the reliability model. Without Feature 1c, Epic 2 has nothing to learn from.

The Vision Brief describes the current state: reviewers edit inline and mark a document done. Feature 1c is the API layer behind that "mark done" action — it captures what changed.

---

## User Stories & Acceptance Criteria

### Story 1: Fetch a draft for review

**As a** review interface (or pipeline operator), **I want to** retrieve the original AI draft by `document_id` **so that** I can render the fields for the reviewer to inspect and correct.

**Acceptance Criteria:**
1. `[MUST]` `GET /api/v1/review/{document_id}` returns the full `DocumentDraft` JSON that was stored by Feature 1b
2. `[MUST]` If `document_id` does not exist, response is HTTP 404 with `{"error": "draft_not_found"}`
3. `[MUST]` If the draft has already been approved, the response still returns the draft — the GET endpoint is read-only and idempotent
4. `[SHOULD]` Response includes an `approval_status` field: `"pending"` or `"approved"`

---

### Story 2: Submit approved field values and record touches

**As a** reviewer, **I want to** submit my corrected field values for a draft **so that** the system records my approval and captures which fields the AI got right.

**Acceptance Criteria:**
1. `[MUST]` `POST /api/v1/review/{document_id}/approve` accepts a JSON body: `{"fields": {"field_name": "corrected value or null", ...}}`
2. `[MUST]` Every field name present in the original draft's schema must be present in the approval payload — no fields may be silently omitted
3. `[MUST]` The system computes a per-field touch record: a field is `touched = true` if the approved value differs from the original draft value after whitespace normalization; `touched = false` if identical
4. `[MUST]` Deletions count as touched: a field the AI populated that the reviewer clears to `null` is `touched = true`
5. `[MUST]` Additions count as touched: a field the AI returned as `null` / `not_found` that the reviewer fills in is `touched = true`
6. `[MUST]` Whitespace normalization before comparison: strip leading/trailing whitespace AND collapse all internal runs of whitespace to a single space. `"Deep  Learning"` vs `"Deep Learning"` → NOT touched.
7. `[MUST]` The approval — approved field values + per-field touch record + timestamp — is persisted to the `approvals` table keyed by `document_id`
8. `[MUST]` Response includes a `touch_summary`: total fields, count of touched fields, list of touched field names, list of untouched field names
9. `[MUST]` If `document_id` does not exist, response is HTTP 404 with `{"error": "draft_not_found"}`
10. `[MUST]` If the draft has already been approved, response is HTTP 409 with `{"error": "already_approved"}` — approvals are immutable for this POC
11. `[SHOULD]` Response includes `touch_rate` (touched / total) as a float for quick inspection

---

### Global Acceptance Criteria

1. `[MUST]` Touch detection logic lives in `src/extraction/core/diff.py` with no FastAPI imports — it is a pure function: `compute_touches(original_fields, approved_fields) -> dict[str, bool]`
2. `[MUST]` All `sqlite3` calls for reading/writing approvals live in `db.py` — no cursor usage in routers or core
3. `[MUST]` Both endpoints validate against Pydantic schemas before touching the database

---

## Scope

### In Scope
- `GET /api/v1/review/{document_id}` — fetch stored draft + approval status
- `POST /api/v1/review/{document_id}/approve` — submit corrected values, compute touches, persist approval
- `core/diff.py` — pure touch detection function
- `approvals` table in SQLite
- Error handling: draft not found, already approved, missing fields in payload

### Out of Scope
- UI of any kind — this is a backend API
- Confidence highlighting (Feature 2c)
- Reliability score computation (Feature 2b)
- Partial approval (reviewer approves some fields, flags others) — all-or-nothing for POC
- Re-approval / amendment — once approved, immutable
- Authentication and authorization
- Reviewer identity / audit trail
- Fetching a list of pending approvals

---

## Approach

Two endpoints added to a new router `api/routers/review.py`:

**GET** `/api/v1/review/{document_id}`
1. Look up `document_id` in `drafts` table via `db.get_draft()`
2. Check `approvals` table for existing approval
3. Return draft JSON + `approval_status`

**POST** `/api/v1/review/{document_id}/approve`

Payload — flat dict of field name → corrected value (string or null). No field-object wrapper needed; the reviewer only states the correct value:
```json
{"fields": {"title": "Corrected Title", "authors": "Smith et al.", "abstract": null}}
```

1. Fetch original draft from `drafts` table
2. Return 409 if an approval already exists for this `document_id`
3. Validate that all fields in the draft schema are present in the request payload
4. Call `core/diff.py: compute_touches(original_fields, approved_values)` — pure function, no side effects
5. Persist approval to `approvals` table via `db.save_approval()`
6. Return `ApprovalResult` with touch summary

**`core/diff.py` — touch detection:**
```python
def compute_touches(
    original_fields: dict[str, FieldValue],
    approved_values: dict[str, str | None],
) -> dict[str, bool]:
    ...
```
Comparison logic: strip whitespace from both sides; treat `None` / `not_found=True` as empty string for comparison purposes.

**New SQLite table:**
```sql
CREATE TABLE IF NOT EXISTS approvals (
    document_id   TEXT PRIMARY KEY,
    approved_json TEXT NOT NULL,   -- approved field values (dict[str, str | null])
    touch_json    TEXT NOT NULL,   -- per-field touch record (dict[str, bool])
    approved_at   TEXT NOT NULL,
    FOREIGN KEY (document_id) REFERENCES drafts(id)
)
```

---

## Non-Functional Requirements

- **Performance:** Both endpoints complete in under 500ms (no Claude API calls — pure DB reads/writes)
- **Security:** No documents or raw file content ever stored — only field values (strings) from the draft and approval
- **Reliability:** DB errors return 500 with a structured error body — server does not crash
- **Scalability:** Out of scope — synchronous, local POC

---

## Error States

| Scenario | Expected Behavior |
|---|---|
| `document_id` not found (GET) | HTTP 404: `{"error": "draft_not_found"}` |
| `document_id` not found (POST approve) | HTTP 404: `{"error": "draft_not_found"}` |
| Draft already approved (POST approve) | HTTP 409: `{"error": "already_approved"}` |
| Approval payload missing one or more fields from schema | HTTP 422: `{"error": "missing_fields", "detail": ["field_a", "field_b"]}` |
| Approval payload contains extra fields not in schema | `[SHOULD]` HTTP 422: `{"error": "unknown_fields", "detail": [...]}` — strict by default, rejects unknown keys |
| DB write failure | HTTP 500: `{"error": "internal_error"}` |

---

## Success Metrics & Instrumentation

- **Primary metric:** Full round-trip works end-to-end — `POST /draft` → `GET /review/{id}` → `POST /review/{id}/approve` → touch record persisted
- **Secondary metrics:** Touch rate on real test documents (spot-check: is the AI getting ~80%+ of fields right on a clean scientific paper?)
- **Events to track:** `approval_submitted` (document_id, total fields, touched count, touch rate), `draft_fetched` (document_id)
- **Evaluation timeline:** Manual verification on 3–5 test documents at first working build

---

## Dependencies & Prerequisites

- Feature 1b complete and merged — `drafts` table must exist; `DocumentDraft` Pydantic schema must be importable
- `db.py` must expose `get_draft(document_id)` and a new `save_approval()` function
- `FieldValue` schema (from 1b) must be importable in `core/diff.py`

---

## Design Constraints

- **`core/` stays pure** — `core/diff.py` has no FastAPI imports, no `sqlite3` calls; it is a pure function that takes dicts and returns a dict
- **All `sqlite3` in `db.py`** — `save_approval()` and `get_approval()` go there; nowhere else
- **Approvals are immutable** — once written, the approval record is never updated or deleted; this preserves the integrity of the training signal
- **Touch definition is explicit config, not assumption** — the whitespace-normalization rule and the deletion/addition rules are documented in `core/diff.py` and must be configurable in a future iteration (CLAUDE.md Open Question #2 on cosmetic edits)
- **Backend API only** — no UI, no HTML, no templates
- **Rejected approaches:** re-approval endpoints, partial approval, reviewer identity tracking — all deferred

---

## Verification

**Happy path (full round-trip):**
1. `POST /api/v1/extraction/draft` with a test document and schema `[{name: "title"}, {name: "authors"}]` → note `document_id`
2. `GET /api/v1/review/{document_id}` → HTTP 200, returns draft with `approval_status: "pending"`
3. `POST /api/v1/review/{document_id}/approve` with `{"fields": {"title": "Corrected Title", "authors": "Smith"}}` where `title` differs from draft → HTTP 200
4. Response includes `touch_summary.touched: ["title"]`, `touch_summary.untouched: ["authors"]`

**Already approved:**
1. Repeat step 3 above with the same `document_id`
2. Response is HTTP 409 with `{"error": "already_approved"}`

**Draft not found:**
1. `GET /api/v1/review/nonexistent-id` → HTTP 404 with `{"error": "draft_not_found"}`

**Missing fields:**
1. `POST /api/v1/review/{document_id}/approve` with `{"fields": {"title": "X"}}` (omitting `authors`)
2. Response is HTTP 422 with `{"error": "missing_fields", "detail": ["authors"]}`

**Whitespace not counted:**
1. Original draft has `title: "Deep Learning"`. Approve with `title: "  Deep Learning  "` (extra spaces)
2. Response includes `title` in `untouched` list — whitespace difference ignored

---

## Open Questions

1. **Whitespace normalization scope**: Resolved — strip leading/trailing AND collapse internal runs. `"Deep  Learning"` == `"Deep Learning"` → not touched.
2. **Unknown fields in approval payload**: Should extra keys be rejected (strict, 422) or silently ignored (lenient)? PRD says strict — reject with 422. Confirm before implementing.
3. **`approval_status` on GET**: Should `approval_status` be stored in the `drafts` table (requires updating a row) or derived by checking if an `approvals` row exists? Recommendation: derive it at query time — no row update needed, avoids mutation of the drafts table.

---

## Future Considerations

- Partial approval: reviewer marks some fields as "needs escalation" rather than submitting a final value — would require a richer approval payload and a `pending_fields` state
- Re-approval / amendment: allow a reviewer to correct a mistake after approval — requires versioning the approval record
- Reviewer identity: attach a reviewer ID to the approval for per-reviewer accuracy tracking (management dashboard use case from Vision Brief)
- Bulk approval endpoint for high-volume use

---

## Revision History

| Date | Change | Author |
|---|---|---|
| 2026-06-23 | Initial draft | Eric Rooney / Claude |
