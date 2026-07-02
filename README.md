# Confidence-Routed Document Extraction

An AI document-extraction pipeline that **learns which fields it gets wrong from reviewers' normal edits — and routes human attention to exactly those fields.**

> **The result, in one sentence:** Trained on 5 NYC property-tax bills, the system learned that **assessed value** was the unreliable field — and then automatically flagged it for mandatory review on a **sixth, never-seen bill**, while letting the reliably-extracted fields pass with light review. No model self-confidence, no separate annotation step — the signal comes entirely from the corrections reviewers were already making.

```
GET /api/v1/reliability   (after 5 reviewed property-tax bills, assessed_value corrected on 4)
  assessed_value     change_rate=0.80  ->  mandatory_review
  owner_name         change_rate=0.00  ->  light_review
  borough_block_lot  change_rate=0.00  ->  light_review
  tax_class          change_rate=0.00  ->  light_review

GET /api/v1/review/{fresh-bill}   (never reviewed)
  flagged_count: 1/4
    🚩 assessed_value     -> mandatory_review (high_change_rate)
       owner_name         -> light_review
       borough_block_lot  -> light_review
       tax_class          -> light_review
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

## Design decisions

A few non-obvious choices shaped this system. The reasoning for each is captured in the PRDs and decision records under [`/specs`](specs/).

**Correction history, not model self-confidence.** The obvious way to find unreliable fields is to ask the model how sure it is. That does not work — models report high confidence on wrong answers just as readily as on right ones. This system ignores self-reported confidence entirely. The signal is whether a human actually had to correct the field during normal review: measured reality, not the model's opinion of itself. The training labels are free, because they are the edits reviewers were already making.

**Unproven field-groups always route to mandatory review.** Any field-group the system has not yet seen enough of — too few samples, or a document type it has never encountered — routes to mandatory review by default. It never inherits another field's reliability score. This is a deliberate guard against overfitting: a new document type or country earns its reliability from its own history rather than borrowing a score that may not apply.

**Grouping granularity is a tunable tradeoff.** Reliability is tracked per `field × document_type`. Group too coarsely (just `field`) and a field that is hard on one document type but easy on another gets averaged into noise. Group too finely (field × type × source × language) and no bucket ever accumulates enough samples to learn anything. The grouping key is configurable; the default balances signal against sample density.

**Validated against real documents, not just tests.** Running the pipeline on real documents surfaced a bug the unit tests could not: the model was silently misclassifying a document type, which would have fragmented the reliability buckets so none ever crossed the sample threshold — the learning loop would have looked like it was working while learning nothing. Caught and fixed before it could corrupt the signal. The lesson: a learning system has to be tested against reality, because its failure mode is silent.

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

## How it was built

Built as the capstone for **Agentic AI for Claude Builders**, using a spec-first workflow: every feature was specified in a PRD, tracked as a GitHub issue, and shipped through a reviewed pull request. The full set of specs and design-decision records lives in [`/specs`](specs/) — they document the reasoning behind each decision, not just the final code.

## Scope

A generic, domain-agnostic proof of concept: any document type with discrete extractable fields. SQLite and synchronous processing are deliberate POC choices. Out of scope: production scaling, authentication, a review UI (this is the API; the interface is assumed external), and semantic/vector retrieval of reference material.
