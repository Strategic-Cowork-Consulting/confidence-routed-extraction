import json
import sqlite3
from pathlib import Path
from typing import Any

_DB_PATH = Path("drafts.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO approvals (document_id, approved_json, touch_json, approved_at)
            VALUES (?, ?, ?, ?)
            """,
            (document_id, approved_json, touch_json, approved_at),
        )


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
