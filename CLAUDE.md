# CLAUDE.md — Confidence-Routed Document Extraction

## What This Project Does

An AI-assisted document extraction pipeline with a self-improving reliability model.

The core loop:
1. AI drafts field extraction from a document
2. Human reviews and approves (editing any wrong fields)
3. System records, per field, whether the human changed it ("touched" = 1, untouched = 0)
4. Change rates aggregate into a **per-field reliability model**
5. High-change-rate fields get flagged for mandatory human review on future documents; low-change-rate fields pass with lighter review

The training signal is free: the human's normal review edits are the ground-truth labels. No separate annotation step, no model self-confidence, no semantic diffing.

---

## Tech Stack

| Layer | Choice |
|---|---|
| API | FastAPI (async) |
| Data models | Pydantic v2 |
| Database | SQLite via raw `sqlite3` — deliberate POC choice, local testing only, no production target |
| Migrations | None — `create_all()` on startup; schema changes are manual for this POC |
| Task queue | ARQ (async Redis queue) — planned for Epic 2, not active yet |
| AI extraction | Anthropic Claude API (claude-sonnet-4-6 default) |
| Testing | pytest + pytest-asyncio |
| Linting | ruff |
| Type checking | pyright (strict) |

---

## Repository Structure

```
confidence-routed-extraction/
├── CLAUDE.md
├── pyproject.toml
├── src/
│   └── extraction/
│       ├── api/            # FastAPI routers
│       ├── core/           # Business logic (no framework deps)
│       │   ├── pipeline.py     # Draft → review → approval flow
│       │   ├── diff.py         # Touch detection
│       │   └── reliability.py  # Per-field reliability model
│       ├── schemas/        # Pydantic request/response schemas
│       ├── tasks/          # ARQ background tasks (Epic 2)
│       └── db.py           # All sqlite3 access in one place
└── tests/
    ├── unit/
    └── integration/
```

**Rule:** Business logic lives in `core/` with no FastAPI imports. Routers are thin — they validate input, call core, return output.

**Rule:** All `sqlite3` calls live in `db.py`. No cursor or connection usage anywhere else in the codebase.

---

## Development Setup

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # fill in ANTHROPIC_API_KEY
PYTHONPATH=src uvicorn extraction.api.main:app --reload
```

The SQLite database (`drafts.db`) is created automatically on first startup — no migration step needed.

Run tests (`pythonpath = ["src"]` is set in pyproject.toml so no env var needed for pytest):
```bash
pytest                        # all tests
pytest tests/unit/            # unit only (no DB or API key required)
```

Type check and lint:
```bash
pyright src/
ruff check src/ tests/
```

---

## Development Lifecycle

This project follows a **define → plan → implement → verify → review → ship** cycle. Each stage has a specific gate before moving forward.

### Define
- All new features start with a written description of the field schema change, endpoint contract, or reliability model behavior being added.
- Open architectural questions (see below) must be explicitly resolved — or explicitly deferred — before implementation begins.
- CLAUDE.md is updated if a decision closes one of the open questions.

### Plan
- Use `/feature-dev` to explore the codebase and draft an implementation plan before writing code.
- Architectural decisions belong in the plan, not in comments or PR descriptions.
- DB schema changes are made directly in `db.py` (`CREATE TABLE` statements); no migration tooling exists for this POC.

### Implement
- New endpoints: router → schema → core logic — in that order.
- No business logic in routers. No `sqlite3` calls outside `db.py`.
- Pydantic models are the contract boundary: use them for all API input/output and for field schemas passed to the AI extraction step.
- Keep `core/` pure: no FastAPI, no `sqlite3` inside `core/pipeline.py`, `core/diff.py`, or `core/reliability.py`.

### Verify
- Every PR must pass: `pyright`, `ruff`, `pytest tests/unit/`
- Unit tests mock the Claude API and do not require a running DB or API key.
- Run `/security-guidance` before committing. (Configure as a hook once the repo is active.)

### Review
- Use `/code-review` for automated PR review before requesting human review.
- Reviewer checklist: core/router separation respected, Pydantic schemas complete, all `sqlite3` calls confined to `db.py`, touch-detection edge cases covered.

### Ship
- Use `/commit-commands` for commit, push, and PR creation.
- Squash-merge only. PR title = commit message.
- Tag releases that change the reliability model schema — downstream consumers depend on field routing behavior.

---

## Coding Conventions

**Async for HTTP and Claude API calls.** FastAPI route handlers and `anthropic` SDK calls are `async`. `sqlite3` is synchronous — calls go through `db.py` directly without asyncio wrappers (acceptable for this local POC).

**Pydantic v2 style.** Use `model_validator` and `field_validator`, not `validator`. Annotate with `Annotated[...]` for constraints.

**No bare `except`.** Catch specific exceptions. FastAPI exception handlers live in `api/exceptions.py`.

**Type everything.** pyright strict is the bar. No `Any` without a comment explaining why.

**Tests mirror src.** `tests/unit/core/test_diff.py` tests `src/extraction/core/diff.py`. Same path structure.

**No magic strings for field names.** Document field schemas are defined as Pydantic models or enums — never as free-form dict keys in business logic.

---

## Key Architectural Decisions (Locked)

These were evaluated and rejected — do not re-open without a strong reason:

- **Model self-reported confidence** — rejected. Models report high confidence on wrong answers. Not a reliable signal.
- **Logprobs / multi-sample agreement** — rejected. Proxies for uncertainty. We want measured correction history, not estimated uncertainty.
- **Semantic field diffing** — rejected. Too brittle ("B.S." vs "Bachelor of Science" is cosmetic, not an error). We don't need to know *how* a field changed — only *that* it did.
- **Touch definition:** deletions count as touched. Fields left blank by the AI that the human fills in count as touched. The question is "was this field ship-ready as drafted?" — a binary.

---

## Open Design Questions (Not Yet Resolved)

These are active design questions. Do not assume an answer; surface them explicitly when they affect implementation.

**1. Granularity of the reliability model (CENTRAL)**
Per-field is the floor. The useful version is conditional: `field × document_type`, possibly `× source_country` or `× language`.
- Too coarse ("GPA" across all doc types) → flags everything, no signal
- Too fine (per-institution) → never enough samples
The right grouping is unsolved. Any code that computes or stores reliability scores must not bake in a granularity assumption — design for the grouping key to be configurable.

**2. Cosmetic edits**
Current lean: count ALL touches as signal. The question "was this field ship-ready?" means a cosmetic edit IS a signal that the field wasn't ready. But this must be an explicit config option, not a hidden assumption.

**3. Minimum sample threshold before routing decisions**
How many samples does a field-group need before its reliability score is trusted? Below threshold, default behavior (mandatory review) must be documented.

---

## Scope Boundary

**In scope:**
- Generic, domain-agnostic pipeline
- Any document type with discrete extractable fields (invoices, lab reports, intake forms)
- The draft → review → approval → touch-capture → reliability loop
- A routing decision per field based on reliability score + configurable threshold

**Out of scope:**
- Credential evaluation rules or academic equivalency logic
- CRM integrations (Salesforce, HubSpot, etc.)
- Any proprietary document formats or client-specific field definitions
- UI — this is an API; the review interface is assumed to exist externally
