"""Integration tests for Feature 2c — per-field flags on the review-fetch response."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from extraction import db
from extraction.schemas.extraction import (
    DocumentDraft,
    DocumentResult,
    FieldObservation,
    FieldValue,
)

_AT = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)


def _fv(source: str) -> FieldValue:
    return FieldValue(value="x", source_document=source, not_found=False)


def _seed_draft(
    document_id: str, fields: dict[str, FieldValue], docs: list[DocumentResult]
) -> None:
    draft = DocumentDraft(
        document_id=document_id, fields=fields, documents=docs, created_at=_AT
    )
    db.save_draft(document_id, draft.model_dump_json(), draft.created_at.isoformat())


def _seed_obs(specs: list[tuple[str, str, int, int]]) -> None:
    obs: list[FieldObservation] = []
    for field, dt, samples, touched in specs:
        for i in range(samples):
            obs.append(
                FieldObservation(
                    document_id=f"{field}-{dt}-{i}",
                    field_name=field,
                    document_type=dt,
                    touched=i < touched,
                    approved_at=_AT,
                )
            )
    db.save_observations(obs)


def test_review_annotates_fields_with_flags(client: TestClient) -> None:
    _seed_obs([("gpa", "transcript", 5, 3), ("degree", "transcript", 5, 0)])
    _seed_draft(
        "doc-1",
        {"gpa": _fv("t.pdf"), "degree": _fv("t.pdf")},
        [DocumentResult(filename="t.pdf", document_type="transcript")],
    )
    r = client.get("/api/v1/review/doc-1")
    assert r.status_code == 200
    body = r.json()

    # Backward-compatible: pre-2c fields still present (NFR-4, AC-G.3)
    assert body["approval_status"] == "pending"
    assert set(body["fields"]) == {"gpa", "degree"}

    flags = body["field_flags"]
    assert flags["gpa"]["flagged"] is True
    assert flags["gpa"]["basis"] == "high_change_rate"
    assert flags["degree"]["flagged"] is False
    assert flags["degree"]["basis"] == "low_change_rate"
    assert body["flagged_count"] == 1
    assert body["total_fields"] == 2


def test_each_field_flagged_by_its_own_document_type(client: TestClient) -> None:
    # transcript/gpa reliable (light); diploma/seal unreliable (flagged)
    _seed_obs([("gpa", "transcript", 5, 0), ("seal", "diploma", 5, 4)])
    _seed_draft(
        "doc-2",
        {"gpa": _fv("t.pdf"), "seal": _fv("d.pdf")},
        [
            DocumentResult(filename="t.pdf", document_type="transcript"),
            DocumentResult(filename="d.pdf", document_type="diploma"),
        ],
    )
    flags = client.get("/api/v1/review/doc-2").json()["field_flags"]
    assert flags["gpa"]["document_type"] == "transcript"
    assert flags["gpa"]["flagged"] is False
    assert flags["seal"]["document_type"] == "diploma"
    assert flags["seal"]["flagged"] is True


def test_empty_store_flags_every_field_unproven(client: TestClient) -> None:
    _seed_draft(
        "doc-3",
        {"a": _fv("t.pdf"), "b": _fv("t.pdf")},
        [DocumentResult(filename="t.pdf", document_type="transcript")],
    )
    body = client.get("/api/v1/review/doc-3").json()
    assert body["flagged_count"] == 2
    assert all(f["flagged"] and f["basis"] == "unproven" for f in body["field_flags"].values())


def test_unknown_document_type_field_is_flagged(client: TestClient) -> None:
    _seed_draft(
        "doc-4",
        {"abstract": _fv("")},  # empty source -> document_type "unknown"
        [DocumentResult(filename="t.pdf", document_type="transcript")],
    )
    flag = client.get("/api/v1/review/doc-4").json()["field_flags"]["abstract"]
    assert flag["document_type"] == "unknown"
    assert flag["flagged"] is True
    assert flag["basis"] == "unproven"


def test_not_found_still_404(client: TestClient) -> None:
    r = client.get("/api/v1/review/nope")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "draft_not_found"
