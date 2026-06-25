"""Integration tests for Feature 1a — config + reference-doc endpoints and wiring."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from extraction.core.pipeline import DEFAULT_PERSONA
from extraction.schemas.extraction import DocumentDraft

# --- Persona ---------------------------------------------------------------


def test_get_persona_returns_default_when_unset(client: TestClient) -> None:
    r = client.get("/api/v1/config/persona")
    assert r.status_code == 200
    assert r.json()["persona"] == DEFAULT_PERSONA


def test_set_then_get_persona(client: TestClient) -> None:
    put = client.put("/api/v1/config/persona", json={"persona": "You are a registrar."})
    assert put.status_code == 200
    assert client.get("/api/v1/config/persona").json()["persona"] == "You are a registrar."


def test_empty_persona_rejected(client: TestClient) -> None:
    r = client.put("/api/v1/config/persona", json={"persona": "   "})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "invalid_persona"


# --- Reference documents ----------------------------------------------------


def test_reference_doc_crud(client: TestClient) -> None:
    created = client.post(
        "/api/v1/reference-docs", json={"name": "Grading guide", "content": "GPA is 0-4."}
    )
    assert created.status_code == 200
    doc_id = created.json()["id"]

    assert any(d["id"] == doc_id for d in client.get("/api/v1/reference-docs").json())
    got = client.get(f"/api/v1/reference-docs/{doc_id}")
    assert got.status_code == 200
    assert got.json()["content"] == "GPA is 0-4."

    assert client.delete(f"/api/v1/reference-docs/{doc_id}").status_code == 200
    assert client.get(f"/api/v1/reference-docs/{doc_id}").status_code == 404


def test_reference_doc_validation(client: TestClient) -> None:
    empty = client.post("/api/v1/reference-docs", json={"name": "x", "content": ""})
    assert empty.status_code == 422
    assert empty.json()["detail"]["error"] == "invalid_reference_doc"

    big = client.post(
        "/api/v1/reference-docs", json={"name": "x", "content": "a" * (50 * 1024 + 1)}
    )
    assert big.status_code == 422
    assert big.json()["detail"]["error"] == "reference_doc_too_large"


def test_unknown_reference_doc_404(client: TestClient) -> None:
    assert client.get("/api/v1/reference-docs/nope").status_code == 404
    assert client.delete("/api/v1/reference-docs/nope").status_code == 404


# --- Extraction wiring (no live Claude) ------------------------------------


def test_draft_uses_persona_and_reference_docs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str | None] = {}

    async def fake_run_extraction(
        doc_tuples: object, schema: object, analyst_persona: str | None, client: object = None
    ) -> DocumentDraft:
        captured["persona"] = analyst_persona
        return DocumentDraft(
            document_id="demo", fields={}, documents=[], created_at=datetime.now(tz=UTC)
        )

    monkeypatch.setattr(
        "extraction.api.routers.extraction.run_extraction", fake_run_extraction
    )

    client.put("/api/v1/config/persona", json={"persona": "You are a registrar."})
    client.post("/api/v1/reference-docs", json={"name": "Guide", "content": "GPA scale 0-4."})

    resp = client.post(
        "/api/v1/extraction/draft",
        files={"documents": ("t.txt", b"Transcript text", "text/plain")},
        data={"field_schema": '[{"name": "gpa"}]'},
    )
    assert resp.status_code == 200

    persona = captured["persona"]
    assert persona is not None
    assert persona.startswith("You are a registrar.")
    assert "GPA scale 0-4." in persona  # reference content folded in
