"""Integration tests for Feature 2a — capture, parity, grouped counts, backfill."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from extraction import db
from extraction.api.main import app
from extraction.backfill import run_backfill
from extraction.schemas.extraction import DocumentDraft, DocumentResult, FieldValue

_AT = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)


def _fv(value: str | None, source: str, *, not_found: bool = False) -> FieldValue:
    return FieldValue(value=value, source_document=source, not_found=not_found)


def _seed_draft(
    document_id: str,
    fields: dict[str, FieldValue],
    documents: list[DocumentResult],
) -> None:
    draft = DocumentDraft(
        document_id=document_id, fields=fields, documents=documents, created_at=_AT
    )
    db.save_draft(document_id, draft.model_dump_json(), draft.created_at.isoformat())


def _approve(client: TestClient, document_id: str, fields: dict[str, str | None]) -> None:
    resp = client.post(f"/api/v1/review/{document_id}/approve", json={"fields": fields})
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Capture at approval
# ---------------------------------------------------------------------------


def test_capture_writes_one_row_per_field(client: TestClient) -> None:
    _seed_draft(
        "doc-1",
        {"assessed_value": _fv("412000", "t.pdf"), "owner_name": _fv("Acme Holdings LLC", "t.pdf")},
        [DocumentResult(filename="t.pdf", document_type="invoice")],
    )
    _approve(client, "doc-1", {"assessed_value": "415000", "owner_name": "Acme Holdings LLC"})

    obs = {o["field_name"]: o for o in db.get_observations()}
    assert set(obs) == {"assessed_value", "owner_name"}
    assert obs["assessed_value"]["document_type"] == "invoice"
    assert obs["assessed_value"]["touched"] == 1  # changed
    assert obs["owner_name"]["touched"] == 0  # untouched


def test_observation_write_failure_rolls_back_approval(
    tmp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-1.7 / NFR-4 / ERR-4 — atomic: a failed observation write rolls back the
    whole approval and returns HTTP 500; nothing is persisted."""
    _seed_draft(
        "doc-x",
        {"assessed_value": _fv("412000", "t.pdf")},
        [DocumentResult(filename="t.pdf", document_type="invoice")],
    )

    # Force the observation-row build to raise inside save_approval's transaction,
    # after the approval INSERT has executed but before commit.
    def _boom(_observations: object) -> list[tuple[str, str, str, int, str]]:
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(db, "_observation_rows", _boom)

    with TestClient(app, raise_server_exceptions=False) as failing_client:
        resp = failing_client.post(
            "/api/v1/review/doc-x/approve", json={"fields": {"assessed_value": "415000"}}
        )

    assert resp.status_code == 500
    # Rolled back: neither the approval nor any observation survived.
    assert db.get_approval("doc-x") is None
    assert db.get_observations() == []


def test_unknown_document_type_still_captured(client: TestClient) -> None:
    # 'exemptions' has no source document (not found in any doc) -> document_type "unknown"
    _seed_draft(
        "doc-2",
        {
            "owner_name": _fv("Acme Holdings LLC", "p.pdf"),
            "exemptions": _fv(None, "", not_found=True),
        },
        [DocumentResult(filename="p.pdf", document_type="report")],
    )
    _approve(client, "doc-2", {"owner_name": "Acme Holdings LLC", "exemptions": "Now filled in"})

    obs = {o["field_name"]: o for o in db.get_observations()}
    assert obs["exemptions"]["document_type"] == "unknown"
    assert obs["exemptions"]["touched"] == 1  # addition counts as touched


def test_observations_match_stored_touch_json(client: TestClient) -> None:
    """NFR-2 — (field, touched) multiset equals the approval's touch_json."""
    _seed_draft(
        "doc-3",
        {"a": _fv("1", "t.pdf"), "b": _fv("2", "t.pdf"), "c": _fv("3", "t.pdf")},
        [DocumentResult(filename="t.pdf", document_type="invoice")],
    )
    _approve(client, "doc-3", {"a": "1", "b": "CHANGED", "c": "3"})

    approval = db.get_approval("doc-3")
    assert approval is not None
    from_touch_json = {(k, bool(v)) for k, v in approval["touch_json"].items()}
    from_observations = {
        (o["field_name"], bool(o["touched"])) for o in db.get_observations()
    }
    assert from_observations == from_touch_json


# ---------------------------------------------------------------------------
# Grouped reads (AC-3)
# ---------------------------------------------------------------------------


def test_bucket_counts_group_by_field_and_doc_type(client: TestClient) -> None:
    _seed_draft(
        "t-1",
        {"owner_name": _fv("Acme Holdings LLC", "t.pdf")},
        [DocumentResult(filename="t.pdf", document_type="invoice")],
    )
    _seed_draft(
        "t-2",
        {"owner_name": _fv("Beta Corp", "t.pdf")},
        [DocumentResult(filename="t.pdf", document_type="invoice")],
    )
    _seed_draft(
        "d-1",
        {"owner_name": _fv("Gamma Trust", "d.pdf")},
        [DocumentResult(filename="d.pdf", document_type="receipt")],
    )
    _approve(client, "t-1", {"owner_name": "Acme Holdings LLC CHANGED"})  # touched
    _approve(client, "t-2", {"owner_name": "Beta Corp"})  # untouched
    _approve(client, "d-1", {"owner_name": "Gamma Trust"})  # untouched

    buckets = {
        (b["field_name"], b["document_type"]): b
        for b in db.get_bucket_counts(("field_name", "document_type"))
    }
    assert buckets[("owner_name", "invoice")]["samples"] == 2
    assert buckets[("owner_name", "invoice")]["touched"] == 1
    assert buckets[("owner_name", "receipt")]["samples"] == 1
    assert buckets[("owner_name", "receipt")]["touched"] == 0


def test_get_observations_filters_by_document_type(client: TestClient) -> None:
    _seed_draft(
        "t-1",
        {"owner_name": _fv("Acme Holdings LLC", "t.pdf")},
        [DocumentResult(filename="t.pdf", document_type="invoice")],
    )
    _seed_draft(
        "d-1",
        {"owner_name": _fv("Gamma Trust", "d.pdf")},
        [DocumentResult(filename="d.pdf", document_type="receipt")],
    )
    _approve(client, "t-1", {"owner_name": "Acme Holdings LLC"})
    _approve(client, "d-1", {"owner_name": "Gamma Trust"})

    only_invoice = db.get_observations(document_type="invoice")
    assert len(only_invoice) == 1
    assert only_invoice[0]["document_id"] == "t-1"


# ---------------------------------------------------------------------------
# Backfill (AC-2, NFR-3, ERR-2/3)
# ---------------------------------------------------------------------------


def _seed_pre2a_approval(document_id: str, doc_type: str, touched: bool) -> None:
    """Create a draft + approval with NO observations (simulates pre-2a state)."""
    import json

    _seed_draft(
        document_id,
        {"owner_name": _fv("X", "f.pdf")},
        [DocumentResult(filename="f.pdf", document_type=doc_type)],
    )
    db.save_approval(
        document_id=document_id,
        approved_json=json.dumps({"owner_name": "X CHANGED" if touched else "X"}),
        touch_json=json.dumps({"owner_name": touched}),
        approved_at=_AT.isoformat(),
        observations=[],  # pre-2a: none captured
    )


def test_backfill_populates_then_is_idempotent(tmp_db: object) -> None:
    _seed_pre2a_approval("old-1", "invoice", touched=True)
    _seed_pre2a_approval("old-2", "receipt", touched=False)
    assert db.get_observations() == []  # nothing captured yet

    first = run_backfill()
    assert first == {"approvals_processed": 2, "rows_written": 2, "skipped": 0}
    assert len(db.get_observations()) == 2

    second = run_backfill()
    assert second["rows_written"] == 0  # idempotent — no duplicates
    assert len(db.get_observations()) == 2


def test_backfill_skips_unrebuildable_draft(tmp_db: object) -> None:
    import json

    _seed_pre2a_approval("good", "invoice", touched=True)
    # An approval whose draft JSON can't validate into a DocumentDraft.
    db.save_draft("bad", json.dumps({"not": "a draft"}), _AT.isoformat())
    db.save_approval(
        document_id="bad",
        approved_json=json.dumps({}),
        touch_json=json.dumps({}),
        approved_at=_AT.isoformat(),
        observations=[],
    )

    report = run_backfill()
    assert report["approvals_processed"] == 2
    assert report["skipped"] == 1
    assert report["rows_written"] == 1  # only the good one
    assert {o["document_id"] for o in db.get_observations()} == {"good"}
