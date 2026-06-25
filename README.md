# Confidence-Routed Document Extraction

An AI document-extraction pipeline that **learns which fields it gets wrong from reviewers' normal edits — and routes human attention to exactly those fields.**

> **The result, in one sentence:** Trained on 5 transcripts, the system learned that **GPA** was the unreliable field — and then automatically flagged it for mandatory review on a **sixth, never-seen transcript**, while letting the reliably-extracted fields pass with light review. No model self-confidence, no separate annotation step — the signal comes entirely from the corrections reviewers were already making.

```
GET /api/v1/reliability   (after 5 reviewed transcripts, GPA corrected on 4)
  gpa              change_rate=0.80  ->  mandatory_review
  degree           change_rate=0.00  ->  light_review
  graduation_date  change_rate=0.00  ->  light_review
  student_name     change_rate=0.00  ->  light_review

GET /api/v1/review/{fresh-transcript}   (never reviewed)
  flagged_count: 1/4
    🚩 gpa              -> mandatory_review (high_change_rate)
       student_name     -> light_review
       degree           -> light_review
       graduation_date  -> light_review
```

---

## The idea

AI field extraction is wrong on some fraction of fields and never perfect on the first pass. Today a reviewer scrutinizes *every* field equally because there's no record of which fields are systematically unreliable. The expensive way to fix that is a large human-QA team. This project's bet is cheaper and self-improving:

1. The AI drafts field values from a document.
2. A human reviews and approves, correcting whatever's wrong.
3. The system records, per field, **whether the human had to change it** (`touched` = 1, untouched = 0).
4. Those touch rates aggregate into a **per-field-group reliability model**.
5. High-change-rate fields get flagged for mandatory review on future documents; low-change-rate fields pass with lighter review.

The training labels are free: they're the reviewer's normal edits. The more documents flow through, the more precisely the system knows where to send human attention.

## Architecture

```
                    ┌─────────── learning loop ───────────┐
                    │                                      │
 document ──▶ extract (Claude) ──▶ review + approve ──▶ capture per-field touch
                    │                     │                      │
            persona + reference     flag fields for          aggregate into
            docs (configurable)     the reviewer (2c)        reliability model (2b)
```

- **`core/`** — pure business logic, no web/DB imports: extraction pipeline, touch detection, observation capture, the reliability model, persona/reference composition.
- **`api/`** — thin FastAPI routers; validate, call `core`, return.
- **`db.py`** — all SQLite access in one place.
- **Reliability is derived on read** from the captured observations — always current, no separate model store.
- **Safe default everywhere:** any field-group the system hasn't proven yet (too few samples, or never seen) routes to **mandatory review** — it never inherits another field's reliability. This is the cold-start / overfitting guard.

## Quickstart

Requires Python 3.12 and an Anthropic API key.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # then put your real ANTHROPIC_API_KEY in .env
PYTHONPATH=src uvicorn extraction.api.main:app --reload
```

`.env` is loaded automatically at startup. The SQLite database is created on first run — no migrations.

Optional, recommended once per clone (enables the secret-scanning pre-commit hook):

```bash
git config core.hooksPath .githooks
```

## API

| Method & path | Purpose |
|---|---|
| `POST /api/v1/extraction/draft` | Extract fields from uploaded documents → structured draft |
| `GET /api/v1/review/{id}` | Fetch a draft for review, **with per-field reliability flags** |
| `POST /api/v1/review/{id}/approve` | Submit corrected values; capture per-field touches |
| `GET /api/v1/reliability` | Per-field-group reliability scores + routing |
| `GET /api/v1/reliability/route` | Routing decision for one `field × document_type` |
| `GET` / `PUT /api/v1/config/persona` | Read / set the analyst persona |
| `POST` / `GET` / `DELETE /api/v1/reference-docs` | Manage reference documents the AI draws on |

Interactive docs at `/docs` when the server is running.

## Reproduce the result

With the server running and a few example documents:

1. `POST /api/v1/extraction/draft` a document → note the `document_id`.
2. `GET /api/v1/review/{document_id}` → render fields (each carries a `flagged` signal).
3. `POST /api/v1/review/{document_id}/approve` with corrected values → touches captured.
4. Repeat across several same-type documents (≥ 5 to cross the sample threshold).
5. `GET /api/v1/reliability` → watch the hard fields cross into `mandatory_review`.
6. Submit a **new** document and `GET /api/v1/review/{new_id}` → its fields come back pre-flagged.

## Configuration

Routing is tunable (defaults shown):

- **Grouping key** — `field × document_type` (configurable).
- **Minimum samples** before a field-group is trusted — `5` (a demonstrability value for the POC, not a statistically rigorous one; production would use ~30+).
- **Change-rate threshold** for mandatory review — `0.20`.

## Tech & quality

Python 3.12 · FastAPI · Pydantic v2 · SQLite · Anthropic Claude.
Tested with pytest (75 tests), type-checked with pyright (strict), linted with ruff.

```bash
pytest
pyright src/
ruff check src/ tests/
```

## Scope

A generic, domain-agnostic proof of concept: any document type with discrete extractable fields. SQLite and synchronous processing are deliberate POC choices. Out of scope: production scaling, authentication, a review UI (this is the API; the interface is assumed external), and semantic/vector retrieval of reference material.
