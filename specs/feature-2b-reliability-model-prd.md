# Feature: Reliability Model (Per-Field-Group Scores + Routing)

**Epic:** Epic 2 — Reliability Learning Loop (issue #2)
**Tracking issue:** #7
**Vision Brief:** specs/confidence-routed-extraction-vision.md

---

## Summary
Turn the touch observations captured by Feature 2a into a per-field-group **reliability score** and a **routing decision** (mandatory vs. lighter review) for each field, so the system can direct reviewer attention to the fields that are actually unreliable — defaulting any unproven field-group to mandatory review.

---

## Motivation
Feature 2a records, per approved document, which fields the reviewer had to correct, and exposes per-bucket counts via `db.get_bucket_counts` (`{field_name, document_type, samples, touched}`). But counts are not a decision: nothing yet tells a reviewer *"on a transcript, the GPA field is wrong 67% of the time — scrutinize it; the degree field is never wrong — skim it."*

Feature 2b is that decision layer. It aggregates the touch counts into a change rate per field-group and converts it into a routing decision, honoring the locked safety rule: **a field-group that hasn't earned a trustworthy score yet always routes to mandatory review.** This is the payoff of Epic 2 and the core of the Vision Brief — *"the more documents go through review, the more precisely the system knows where to direct human attention"* — and its biggest stated risk (overfitting / cold-start) is answered by the unproven→mandatory fallback.

2b produces the model and the routing; **Feature 2c (#8)** consumes it to highlight fields during review.

---

## User Stories & Acceptance Criteria

### US-1 — Per-field-group reliability scores
**As a** reliability pipeline, **I want** a change-rate score computed per field-group from the touch observations, **so that** I can quantify how often each field is corrected.

**Acceptance Criteria:**
1. `AC-1.1` `[MUST]` For each bucket from `db.get_bucket_counts(group_key)`, compute `change_rate = touched / samples` (and `reliability = 1 - change_rate`), exactly.
2. `AC-1.2` `[MUST]` Each score carries the bucket's grouping dimensions (POC default `field_name`, `document_type`), `samples`, `touched`, `change_rate`, and a `proven` flag.
3. `AC-1.3` `[MUST]` `proven = samples >= min_sample_threshold` (POC default 5; configurable).
4. `AC-1.4` `[MUST]` Scoring is a pure function in `core/reliability.py` — no FastAPI, no `sqlite3` — taking the bucket-count rows and thresholds, returning score objects.
5. `AC-1.5` `[MUST]` The grouping key comes from a single configurable source (`observations.DEFAULT_GROUP_KEY`); scoring never hardcodes `field × document_type`.

### US-2 — Routing decision per field-group
**As a** reviewer, **I want** each field-group classified as `mandatory_review` or `light_review`, **so that** I know where to focus.

**Acceptance Criteria:**
1. `AC-2.1` `[MUST]` An **unproven** bucket (`samples < min_sample_threshold`) routes to `mandatory_review` with basis `unproven` — regardless of its change rate.
2. `AC-2.2` `[MUST]` A **proven** bucket with `change_rate >= change_rate_threshold` routes to `mandatory_review` with basis `high_change_rate`.
3. `AC-2.3` `[MUST]` A **proven** bucket with `change_rate < change_rate_threshold` routes to `light_review` with basis `low_change_rate`.
4. `AC-2.4` `[MUST]` `min_sample_threshold` and `change_rate_threshold` are both configurable, with documented POC defaults (5 and 0.20); no threshold is hardcoded at a call site.
5. `AC-2.5` `[MUST]` A new field-group never inherits another bucket's score or routing — each is decided solely from its own counts.

### US-3 — Routing lookup for a specific field on a specific document
**As a** review interface (Feature 2c), **I want** to ask "how should field X on a document of type Y be reviewed?", **so that** I can flag it appropriately — even for combinations never seen before.

**Acceptance Criteria:**
1. `AC-3.1` `[MUST]` Given a `field_name` + `document_type`, return the routing decision for that bucket.
2. `AC-3.2` `[MUST]` If that bucket has **no observations at all** (unseen combination), return `mandatory_review` with basis `unproven` — the safe default. No error.
3. `AC-3.3` `[MUST]` The lookup is a pure function over the computed scores; identical inputs yield identical results.

### US-4 — Reliability API for inspection and 2c
**As a** pipeline operator (and Feature 2c), **I want** to read the reliability model over HTTP, **so that** I can inspect routing and feed the review UI.

**Acceptance Criteria:**
1. `AC-4.1` `[MUST]` `GET /api/v1/reliability` returns the list of per-bucket scores + routing decisions for all buckets.
2. `AC-4.2` `[MUST]` The endpoint accepts optional `document_type`, `min_samples`, and `change_rate_threshold` query params to filter / override defaults for inspection.
3. `AC-4.3` `[SHOULD]` `GET /api/v1/reliability/route?field_name=&document_type=` returns the single routing decision for one field-group (thin wrapper over US-3).

### Global Acceptance Criteria
1. `AC-G.1` `[MUST]` All scoring/routing logic lives in `core/reliability.py` and is import-pure; the router and `db` layer carry no scoring math.
2. `AC-G.2` `[MUST]` 2b reads counts via `db.get_bucket_counts` and adds **no new table** — scores are derived on read, always reflecting current observations.
3. `AC-G.3` `[MUST]` 2b never recomputes or alters touch values — it consumes 2a's `touched` counts as-is.

---

## Scope

### In Scope
- `core/reliability.py` — pure scoring + routing over bucket counts, with configurable thresholds.
- Pydantic models for a reliability score and a routing decision (+ routing/basis enums).
- A reliability read API (`GET /api/v1/reliability`, plus a single-field route lookup).
- Documented POC defaults: `min_sample_threshold = 5`, `change_rate_threshold = 0.20`.

### Out of Scope
- **Confidence highlighting / red-flagging in the review UI — Feature 2c (#8).** 2b returns routing; 2c renders it.
- Capturing observations — Feature 2a (already merged).
- Changing the touch definition or grouping key — owned by 1c / 2a.
- Persisting scores to a table, caching, or recompute scheduling — derived-on-read for the POC.
- Statistical smoothing / confidence intervals / Bayesian priors — POC uses raw rate + a hard sample threshold (see Future Considerations).
- Feeding reliability back into the extraction prompt to *reduce* errors — future (Vision Brief "Future Considerations").

---

## Approach
Derived-on-read, no new storage:

1. **`core/reliability.py` (pure):**
   - Constants `DEFAULT_MIN_SAMPLES = 5`, `DEFAULT_CHANGE_RATE_THRESHOLD = 0.20`.
   - `score_buckets(bucket_counts, *, min_samples, change_rate_threshold) -> list[ReliabilityScore]` — maps each `{field_name, document_type, samples, touched}` row to a score + routing.
   - `route_for(field_name, document_type, scores) -> RoutingDecision` — looks up the bucket; unseen → `mandatory_review` / `unproven`.
2. **`schemas/`:** `ReliabilityScore` (dims + samples + touched + change_rate + reliability + proven + routing + basis), `RoutingDecision`, and `Routing` / `RoutingBasis` enums.
3. **`api/routers/reliability.py`:** `GET /api/v1/reliability` (calls `db.get_bucket_counts(DEFAULT_GROUP_KEY)` → `score_buckets`), optional filters/overrides; `GET /api/v1/reliability/route` for one field-group.
4. **Reuse `db.get_bucket_counts`** from 2a; the only `db` involvement is that existing read.

Routing truth table (per field-group):

| samples | change_rate | → routing | basis |
|---|---|---|---|
| `< min_samples` | any | `mandatory_review` | `unproven` |
| `>= min_samples` | `>= threshold` | `mandatory_review` | `high_change_rate` |
| `>= min_samples` | `< threshold` | `light_review` | `low_change_rate` |
| (no observations) | — | `mandatory_review` | `unproven` |

---

## Data & Validation

**`ReliabilityScore` (Pydantic, response model — not persisted):**

| Field | Type | Required | Rules / limits |
|-------|------|----------|----------------|
| `field_name` | text | yes | Bucket dimension |
| `document_type` | text | yes | Bucket dimension (`"unknown"` allowed) |
| `samples` | int | yes | ≥ 1 (buckets with 0 rows don't appear in counts) |
| `touched` | int | yes | 0 ≤ touched ≤ samples |
| `change_rate` | float | yes | `touched / samples`, 0.0–1.0 |
| `reliability` | float | yes | `1 - change_rate`, 0.0–1.0 |
| `proven` | bool | yes | `samples >= min_sample_threshold` |
| `routing` | enum | yes | `mandatory_review` \| `light_review` |
| `basis` | enum | yes | `unproven` \| `high_change_rate` \| `low_change_rate` |

**Config inputs (request/override, validated):**

| Field | Type | Rules |
|-------|------|-------|
| `min_samples` | int | ≥ 1 |
| `change_rate_threshold` | float | 0.0 ≤ x ≤ 1.0 |

**Cross-field & business rules:**
- `change_rate = touched / samples`; `reliability = 1 - change_rate`.
- Routing follows the truth table above — `unproven` always wins over change rate.

---

## Non-Functional Requirements
- **Performance:** `NFR-1` `[MUST]` Computing the full model and `GET /api/v1/reliability` completes in under 200ms for POC volumes (single `get_bucket_counts` read + in-memory mapping; no Claude calls).
- **Correctness:** `NFR-2` `[MUST]` For every bucket, `change_rate` equals `touched/samples` to within floating-point tolerance, and `routing`/`basis` match the truth table exactly. Verified by unit tests over boundary cases (at, just below, just above each threshold).
- **Purity:** `NFR-3` `[MUST]` `core/reliability.py` imports neither FastAPI nor `sqlite3`.
- **Determinism:** `NFR-4` `[MUST]` Same observations + same thresholds → identical scores and routing on repeated calls.

---

## Error States

| ID | Scenario | Expected Behavior | Priority |
|----|----------|-------------------|----------|
| `ERR-1` | Routing requested for an unseen `field_name`/`document_type` (no observations) | Return `mandatory_review` / `unproven`; no error | `[MUST]` |
| `ERR-2` | Bucket with `samples` below threshold | `mandatory_review` / `unproven`, never `light_review` | `[MUST]` |
| `ERR-3` | Invalid `group_key`/column passed through to `get_bucket_counts` | Surface as HTTP 422 `{"error": "invalid_group_key"}` (db raises `ValueError`) | `[MUST]` |
| `ERR-4` | `change_rate_threshold` outside 0.0–1.0, or `min_samples < 1` | HTTP 422 `{"error": "invalid_threshold"}` | `[MUST]` |
| `ERR-5` | No observations exist at all (empty store) | `GET /reliability` returns `[]` (HTTP 200); route lookups return `mandatory_review`/`unproven` | `[MUST]` |

---

## Success Metrics & Instrumentation
- **Primary metric:** The model correctly separates routing — at least one field-group reaches `light_review` and at least one stays `mandatory_review` once enough data exists (demonstrable on the live probe data, where `gpa` change-rate ≈ 0.67 → mandatory vs. `degree` ≈ 0.00 → light).
- **Secondary metrics:** Count of `proven` buckets (≥ 5 samples); share of field-groups in `light_review` (the reviewer-time savings lever).
- **Events to track:** `reliability_served` (bucket count, proven count, light_review count); `route_decided` (field_name, document_type, routing, basis).
- **Evaluation timeline:** Manual verification on the existing observation store at first working build; revisit once 2c consumes it.

---

## Dependencies & Prerequisites
- **Feature 2a (#6) — merged.** `db.get_bucket_counts`, the `field_observations` store, and `observations.DEFAULT_GROUP_KEY`.
- **Locked design decisions (#9):** configurable grouping key + thresholds; unproven→mandatory; count-all-touches.
- **Issue #13 (document_type enum) — PREREQUISITE (decided 2026-06-25).** 2b is built with `field × document_type` grouping *after* #13 is fixed, so buckets are genuinely separated (transcript vs. diploma) rather than conflated under `diploma`. 2b works mechanically regardless, but its per-`document_type` routing is only trustworthy once the enum is corrected — so #13 lands first.

---

## Design Constraints
- **`core/` stays pure** — scoring/routing take dicts/Pydantic in, return objects out; no FastAPI, no `sqlite3`.
- **No new persistence** — derived on read from `get_bucket_counts`; scores always reflect current observations.
- **Single source for the grouping key** — `observations.DEFAULT_GROUP_KEY`; do not re-declare.
- **Thresholds configurable, never hardcoded at call sites** — module defaults + request overrides only.
- **Unproven → mandatory review is inviolable** — the safety rule wins over any change-rate computation (locked principle #9).
- **2b does not render anything** — routing is data; the UI/highlighting is 2c.
- **Reuse 2a's touch counts verbatim** — no recomputation of `touched`.

---

## Verification

### Happy path (scores + routing from real data)
1. With observations present (e.g. the live-probe store), `GET /api/v1/reliability` returns one score per bucket; `gpa` shows `change_rate ≈ 0.67` → `mandatory_review`/`high_change_rate`; `degree` shows `0.00` → `light_review`/`low_change_rate`. _(AC-1.1, AC-1.2, AC-2.2, AC-2.3, AC-4.1)_

### Threshold boundaries (unit)
2. Bucket with `samples = 4` (below 5) → `mandatory_review`/`unproven` regardless of change rate. _(AC-1.3, AC-2.1, ERR-2)_
3. Bucket with `samples = 5`, `change_rate` exactly at threshold (0.20) → `mandatory_review`/`high_change_rate`; just below → `light_review`/`low_change_rate`. _(AC-2.2, AC-2.3, NFR-2)_

### Unseen / safe default
4. `GET /api/v1/reliability/route?field_name=ghost&document_type=passport` with no such observations → `mandatory_review`/`unproven`. _(AC-3.1, AC-3.2, ERR-1)_

### Empty store
5. With no observations, `GET /api/v1/reliability` → `[]` (200); a route lookup → `mandatory_review`/`unproven`. _(ERR-5)_

### Bad input
6. `change_rate_threshold=1.5` → HTTP 422 `invalid_threshold`. `min_samples=0` → 422. _(ERR-4)_
7. An invalid grouping column → HTTP 422 `invalid_group_key`. _(ERR-3)_

### Purity / determinism
8. `core/reliability.py` imports no FastAPI/sqlite3; repeated calls on the same store return identical output. _(NFR-3, NFR-4, AC-3.3, AC-G.1)_

---

## Definition of Done
- [ ] All `[MUST]` acceptance criteria pass (US, Global, NFR, Error State)
- [ ] Every `[MUST]` criterion is covered by a verification step that has actually been run
- [ ] `pyright src/`, `ruff check`, and `pytest` pass (new unit + integration tests)
- [ ] NFR-1 latency and NFR-2 truth-table correctness measured, not assumed
- [ ] `reliability_served` / `route_decided` instrumentation fires and is observable
- [ ] CLAUDE.md repo-structure note updated (`core/reliability.py` now implemented; new router)
- [ ] No `sqlite3`/FastAPI in `core/`; thresholds verified configurable with documented defaults

## Open Questions
_All resolved 2026-06-25 at spec time._
1. **`change_rate_threshold` default — RESOLVED: 0.20.** A proven field-group corrected ≥ 20% of the time routes to mandatory review. Configurable; 0.20 is the POC default.
2. **Routing tiers — RESOLVED: binary.** Two states only (`mandatory_review` / `light_review`) for the POC. A third "watch" tier is a future consideration.
3. **document_type label quality (#13) — RESOLVED: fix #13 first.** #13 (add `transcript` to the enum) is a **prerequisite** — 2b is built and trusted with grouping key `field × document_type` only after the enum is corrected, so buckets are genuinely separated rather than conflated. (See Dependencies.)

## Future Considerations
- Statistical smoothing / confidence intervals so small-but-over-threshold buckets aren't over-trusted (e.g. Wilson interval instead of a hard count cutoff).
- Persist/cache scores if read volume grows (currently derived-on-read).
- Finer grouping dimensions (`country`, `language`) once data and the enum (#13) support them.
- Feed reliability back into extraction prompting to *reduce* errors, not just route them (Vision Brief).
- Per-reviewer reliability views (management dashboard from the Vision Brief).

## Revision History
| Date | Change | Author |
|------|--------|--------|
| 2026-06-25 | Initial draft | Eric Rooney / Claude |
