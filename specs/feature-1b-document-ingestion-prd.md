# Feature: Document Ingestion + AI Draft Generation

**Epic:** Epic 1 — Document Processing Pipeline
**Vision Brief:** specs/confidence-routed-extraction-vision.md

---

## Summary

Accept a portfolio of source documents and an analyst persona, call the Claude API to extract fields, and return a structured JSON draft ready for human review.

---

## Motivation

This is the foundation of the entire pipeline — and the riskiest assumption in the system. Everything downstream (inline review, edit capture, reliability model, confidence highlighting) depends on the AI being able to extract fields from diverse document types with enough accuracy to be worth reviewing at all. If the extraction step can't reliably handle scientific papers, legal documents, and handwritten notes, nothing else is worth building.

Current alternative is manual Claude chat: it can produce a draft but is slow, not reproducible, produces free-text output rather than structured fields, and generates no signal for learning. This feature replaces that with a structured, repeatable API call that returns field-level JSON suitable for downstream processing.

---

## User Stories & Acceptance Criteria

### Story 1: Submit document portfolio and receive a structured draft

**As a** pipeline operator (or the review interface on their behalf), **I want to** submit a set of source documents with a field schema and receive a structured JSON draft **so that** a reviewer can begin correcting fields immediately.

**Acceptance Criteria:**
1. `[MUST]` API accepts one or more source documents per request (PDF, PNG, JPG, plain text)
2. `[MUST]` API accepts a field schema — a list of named fields with optional descriptions — that defines what to extract
3. `[MUST]` API accepts an analyst persona prompt that configures extraction behavior (e.g., "You are a paralegal specializing in immigration documents")
4. `[MUST]` Response returns a JSON draft where every field in the schema is present, with either an extracted value or an explicit `null` + `not_found: true` flag — no fields are silently omitted
5. `[MUST]` Response includes a unique `document_id` for use in subsequent review and approval steps
6. `[MUST]` Each extracted field records which source document it was drawn from (by filename or index)
7. `[SHOULD]` Extraction completes within 30 seconds for a portfolio of up to 5 documents
8. `[COULD]` Field extraction includes a brief rationale string explaining why that value was chosen (useful for reviewer context)

---

### Story 2: Handle document type detection

**As a** pipeline operator, **I want** each submitted document to be identified by type **so that** the reliability model can later group errors by document type when sufficient data exists.

**Acceptance Criteria:**
1. `[MUST]` Each document in the response is tagged with a detected `document_type` (e.g., `"scientific_paper"`, `"legal_document"`, `"handwritten_note"`, `"unknown"`)
2. `[SHOULD]` If document type cannot be determined, tag is `"unknown"` rather than an error — extraction still proceeds

---

### Story 3: Handle extraction failures gracefully

**As a** pipeline operator, **I want** extraction failures to be explicit and structured **so that** I can surface them to the reviewer rather than silently returning wrong data.

**Acceptance Criteria:**
1. `[MUST]` If a document cannot be read or parsed, its fields are marked `extraction_status: "failed"` with an `error` string — not silently null
2. `[MUST]` Partial success is allowed: if 2 of 3 documents process successfully, return those 2 with errors noted for the third
3. `[MUST]` API returns HTTP 422 with a structured error body if no documents in the portfolio can be processed at all
4. `[SHOULD]` Claude API errors (rate limit, timeout) return HTTP 503 with a `Retry-After` header rather than 500

---

### Global Acceptance Criteria

1. `[MUST]` Response JSON validates against the `DocumentDraft` Pydantic schema
2. `[MUST]` Analyst persona prompt is optional — if omitted, a neutral default is used and documented
3. `[MUST]` No submitted documents are persisted to disk or database — extraction is stateless within the request lifecycle

---

## Scope

### In Scope
- POST endpoint to accept document portfolio + extraction config
- Document type detection (Claude API)
- Field extraction via Claude API structured output
- JSON draft response with per-field source attribution and status flags
- Error handling for unsupported formats, partial failures, and Claude API errors

### Out of Scope
- Review interface (Feature 1c)
- Reference document management (Feature 1a)
- Edit capture and reliability model (Epic 2)
- Persistent document storage
- Authentication and authorization
- Vector DB lookup
- Async/job-queue processing (synchronous only for v1)
- UI of any kind — this is a backend API

---

## Approach

Single FastAPI endpoint: `POST /api/v1/extraction/draft`

Accepts multipart form data:
- `documents[]` — one or more files
- `field_schema` — JSON array of `{name, description?}` objects
- `analyst_persona` — optional string prompt

Processing steps:
1. Validate inputs, reject unsupported MIME types with 422
2. For each document: detect type via Claude API, then extract fields per schema
3. Aggregate results into a `DocumentDraft` response object
4. Return — no storage, no side effects

Use Claude API tool_use / structured output (not free-text parsing) to guarantee JSON-shaped responses. Field schema drives the extraction prompt dynamically — no hardcoded field names anywhere in the extraction logic.

---

## Non-Functional Requirements

- **Performance:** End-to-end response under 30 seconds for portfolios of up to 5 documents
- **Security:** Documents are not written to disk; processed in memory only. `ANTHROPIC_API_KEY` sourced from environment, never logged
- **Reliability:** Claude API failures return structured errors — the server does not crash or return 500 on API errors
- **Scalability:** Out of scope for this version — synchronous only

---

## Error States

| Scenario | Expected Behavior |
|---|---|
| Unsupported file format (e.g., `.docx`) | HTTP 422: `{"error": "unsupported_format", "detail": "Supported: pdf, png, jpg, txt"}` |
| File exceeds size limit | HTTP 422: `{"error": "file_too_large", "detail": "Max 10MB per document"}` |
| No documents submitted | HTTP 422: `{"error": "no_documents", "detail": "At least one document is required"}` |
| Claude API rate limit | HTTP 503 with `Retry-After: 60` header |
| Claude API timeout | HTTP 504: `{"error": "extraction_timeout"}` |
| Document unreadable (corrupt, blank) | Field-level: `extraction_status: "failed"`, `error: "unreadable_document"` — rest of portfolio continues |
| All documents fail extraction | HTTP 422: `{"error": "all_extractions_failed"}` |

---

## Success Metrics & Instrumentation

- **Primary metric:** Extraction completes successfully (HTTP 200) for all three test document types — scientific paper, legal document, handwritten note
- **Secondary metrics:** Field population rate (% of schema fields returning a non-null value); extraction latency p50 and p95
- **Events to track:** `extraction_requested` (document count, schema field count), `extraction_completed` (latency, field population rate), `extraction_failed` (error type), `document_type_detected` (detected type)
- **Evaluation timeline:** Manual spot-check on 5 test documents per type at first working build; no automated metrics infra required for capstone

---

## Dependencies & Prerequisites

- `ANTHROPIC_API_KEY` configured in `.env`
- `pyproject.toml` with `anthropic`, `fastapi`, `pydantic>=2`, `python-multipart` dependencies
- `DocumentDraft` Pydantic schema defined before endpoint implementation
- Alembic and database setup are **not** required for this feature — it is stateless

---

## Design Constraints

- **Claude API only** — no other AI backends, no fallback models
- **Domain-agnostic by construction** — field schema is always request-defined; no field names are hardcoded anywhere in extraction logic
- **Stateless** — no document storage within this feature; `document_id` is generated per-request (UUID) but nothing is persisted
- **Structured output only** — use Claude tool_use or structured output, not free-text parsing; downstream components depend on machine-readable JSON
- **`core/` stays pure** — extraction logic lives in `src/extraction/core/pipeline.py` with no FastAPI imports; router calls core, not the reverse
- **Document type detection uses Claude API inference** — no hardcoded classification rules or lookup tables
- **Rejected approaches** (from Vision Brief — do not revisit): model self-reported confidence scores, logprobs, semantic field diffing

---

## Verification

**Happy path:**
1. `POST /api/v1/extraction/draft` with a scientific paper PDF and schema `[{name: "title"}, {name: "authors"}, {name: "abstract"}, {name: "publication_date"}]`
2. Response is HTTP 200
3. All 4 fields present in JSON; `document_type` is `"scientific_paper"`; `document_id` is a UUID; each field has `source_document` set

**Error case:**
1. `POST /api/v1/extraction/draft` with a `.docx` file
2. Response is HTTP 422 with `error: "unsupported_format"`

**Partial failure:**
1. Submit one valid PDF and one corrupt/blank file
2. Response is HTTP 200; valid PDF fields are extracted; corrupt file fields have `extraction_status: "failed"`

---

## Open Questions

1. **Multi-page PDFs:** Should all pages be sent to Claude in a single call, or page by page? Single call is simpler; page-by-page may be necessary for large documents hitting context limits.
2. **Conflicting values across documents:** If two source documents give different values for the same field (e.g., two dates on different forms), should the system pick one, return both candidates, or flag it? No decision made — must be resolved before implementing field aggregation logic.
3. **Persona default:** What's the neutral default analyst persona when none is provided? Needs a specific string, not just "neutral."
4. **Schema field descriptions:** Are field descriptions in the schema required, optional, or irrelevant to extraction quality? Validate empirically during implementation.
5. **Draft persistence (cross-feature dependency):** Feature 1c (review and approval) needs the original draft to compute diffs at approval time. Three options: (a) Feature 1b stores the draft and `document_id` is a real DB key; (b) Feature 1c stores the draft when it receives it for review; (c) the client holds the draft in memory and submits both draft + final to the approval endpoint. Must be decided before implementing either feature — it determines whether this feature is truly stateless.

---

## Future Considerations

- Async processing with job ID for large portfolios (synchronous is fine for capstone)
- Vector DB enrichment — pass similar historical extractions as few-shot examples to improve accuracy
- Batch extraction endpoint for high-volume production use
- Per-field rationale strings exposed in review UI to help reviewer understand why a value was chosen

---

## Revision History

| Date | Change | Author |
|---|---|---|
| 2026-06-22 | Initial draft | Eric Rooney |
