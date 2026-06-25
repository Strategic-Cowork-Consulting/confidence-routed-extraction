import json
import sqlite3
from pathlib import Path
from typing import Any

from extraction.schemas.extraction import FieldObservation

_DB_PATH = Path("drafts.db")

# Columns on field_observations that callers are allowed to GROUP BY (Feature 2a).
# Whitelist guards the dynamic GROUP BY in get_bucket_counts against injection.
_GROUPABLE_COLUMNS: frozenset[str] = frozenset({"field_name", "document_type"})

_OBSERVATION_INSERT = (
    "INSERT OR IGNORE INTO field_observations "
    "(document_id, field_name, document_type, touched, approved_at) "
    "VALUES (?, ?, ?, ?, ?)"
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _observation_rows(
    observations: list[FieldObservation],
) -> list[tuple[str, str, str, int, str]]:
    return [
        (o.document_id, o.field_name, o.document_type, int(o.touched), o.approved_at.isoformat())
        for o in observations
    ]


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS drafts (
                id          TEXT PRIMARY KEY,
                draft_json  TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                document_id   TEXT PRIMARY KEY,
                approved_json TEXT NOT NULL,
                touch_json    TEXT NOT NULL,
                approved_at   TEXT NOT NULL,
                FOREIGN KEY (document_id) REFERENCES drafts(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS field_observations (
                document_id   TEXT NOT NULL,
                field_name    TEXT NOT NULL,
                document_type TEXT NOT NULL,   -- "unknown" when source doc unresolved
                touched       INTEGER NOT NULL,-- 0 / 1
                approved_at   TEXT NOT NULL,
                PRIMARY KEY (document_id, field_name),
                FOREIGN KEY (document_id) REFERENCES approvals(document_id)
            )
            """
        )


def save_draft(document_id: str, draft_json: str, created_at: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO drafts (id, draft_json, created_at) VALUES (?, ?, ?)",
            (document_id, draft_json, created_at),
        )


def get_draft(document_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT draft_json FROM drafts WHERE id = ?", (document_id,)
        ).fetchone()
    if row is None:
        return None
    data: str = row["draft_json"]
    result: dict[str, Any] = json.loads(data)
    return result


def save_approval(
    document_id: str,
    approved_json: str,
    touch_json: str,
    approved_at: str,
    observations: list[FieldObservation],
) -> None:
    """Persist the approval and its per-field observations in one transaction.

    The approval row and all observation rows commit together (AC-1.7) — an
    approval can never exist without its matching observations.

    Deliberate POC behavior: if either write raises, the ``with conn`` block
    rolls the whole transaction back (approval row included) and re-raises. The
    approve request then returns HTTP 500 and the approval is NOT persisted — the
    reviewer resubmits. We accept losing an approval over recording one whose
    training signal didn't persist. (See PRD AC-1.7 / NFR-4 / ERR-4.)
    """
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO approvals (document_id, approved_json, touch_json, approved_at)
            VALUES (?, ?, ?, ?)
            """,
            (document_id, approved_json, touch_json, approved_at),
        )
        if observations:
            conn.executemany(_OBSERVATION_INSERT, _observation_rows(observations))


def get_approval(document_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT approved_json, touch_json, approved_at FROM approvals WHERE document_id = ?",
            (document_id,),
        ).fetchone()
    if row is None:
        return None
    approved: dict[str, Any] = json.loads(row["approved_json"])
    touches: dict[str, Any] = json.loads(row["touch_json"])
    at: str = row["approved_at"]
    return {"approved_json": approved, "touch_json": touches, "approved_at": at}


# ---------------------------------------------------------------------------
# Field observations (Feature 2a)
# ---------------------------------------------------------------------------


def save_observations(observations: list[FieldObservation]) -> int:
    """Insert observations idempotently; return how many new rows were written.

    Used by backfill (no new approval to write). Re-inserting an existing
    (document_id, field_name) is ignored, so this is safe to run repeatedly.
    """
    if not observations:
        return 0
    with _connect() as conn:
        before = conn.total_changes
        conn.executemany(_OBSERVATION_INSERT, _observation_rows(observations))
        return conn.total_changes - before


def get_observations(
    field_name: str | None = None,
    document_type: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[str] = []
    if field_name is not None:
        clauses.append("field_name = ?")
        params.append(field_name)
    if document_type is not None:
        clauses.append("document_type = ?")
        params.append(document_type)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT document_id, field_name, document_type, touched, approved_at "
            f"FROM field_observations{where}",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_bucket_counts(group_key: tuple[str, ...]) -> list[dict[str, Any]]:
    """Per-bucket sample and touched counts, grouped by the given columns.

    `group_key` columns are validated against a whitelist before being
    interpolated into the GROUP BY — never pass user input unchecked.
    """
    invalid = [c for c in group_key if c not in _GROUPABLE_COLUMNS]
    if not group_key or invalid:
        raise ValueError(f"Invalid group key columns: {invalid or 'empty group key'}")
    cols = ", ".join(group_key)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {cols}, COUNT(*) AS samples, "  # noqa: S608 — cols are whitelisted
            f"SUM(touched) AS touched FROM field_observations GROUP BY {cols}"
        ).fetchall()
    return [dict(r) for r in rows]


def iter_approvals_with_drafts() -> list[dict[str, Any]]:
    """All approvals joined to their drafts — the source rows for backfill."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT a.document_id, d.draft_json, a.touch_json, a.approved_at "
            "FROM approvals a JOIN drafts d ON a.document_id = d.id"
        ).fetchall()
    return [dict(r) for r in rows]
