"""Unit tests for core/observations.py — pure function, no DB or API."""
from __future__ import annotations

from datetime import UTC, datetime

from extraction.core.observations import DEFAULT_GROUP_KEY, build_observations
from extraction.schemas.extraction import DocumentDraft, DocumentResult, FieldValue

_AT = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)


def _fv(value: str | None, source: str, *, not_found: bool = False) -> FieldValue:
    return FieldValue(value=value, source_document=source, not_found=not_found)


def _draft(
    fields: dict[str, FieldValue],
    documents: list[DocumentResult],
    document_id: str = "doc-1",
) -> DocumentDraft:
    return DocumentDraft(
        document_id=document_id,
        fields=fields,
        documents=documents,
        created_at=_AT,
    )


def test_one_observation_per_field() -> None:
    draft = _draft(
        {"assessed_value": _fv("412000", "t.pdf"), "owner_name": _fv("Acme LLC", "t.pdf")},
        [DocumentResult(filename="t.pdf", document_type="invoice")],
    )
    obs = build_observations(draft, {"assessed_value": True, "owner_name": False}, _AT)
    assert {o.field_name for o in obs} == {"assessed_value", "owner_name"}
    assert len(obs) == 2


def test_document_type_resolved_from_source() -> None:
    draft = _draft(
        {"assessed_value": _fv("412000", "t.pdf"), "tax_class": _fv("1", "d.pdf")},
        [
            DocumentResult(filename="t.pdf", document_type="invoice"),
            DocumentResult(filename="d.pdf", document_type="receipt"),
        ],
    )
    obs = {
        o.field_name: o
        for o in build_observations(draft, {"assessed_value": False, "tax_class": False}, _AT)
    }
    assert obs["assessed_value"].document_type == "invoice"
    assert obs["tax_class"].document_type == "receipt"


def test_empty_source_is_unknown() -> None:
    draft = _draft(
        {"exemptions": _fv(None, "", not_found=True)},
        [DocumentResult(filename="t.pdf", document_type="invoice")],
    )
    obs = build_observations(draft, {"exemptions": True}, _AT)
    assert obs[0].document_type == "unknown"


def test_unmatched_source_is_unknown() -> None:
    draft = _draft(
        {"assessed_value": _fv("412000", "ghost.pdf")},
        [DocumentResult(filename="t.pdf", document_type="invoice")],
    )
    obs = build_observations(draft, {"assessed_value": False}, _AT)
    assert obs[0].document_type == "unknown"


def test_touched_copied_verbatim() -> None:
    draft = _draft(
        {"a": _fv("1", "t.pdf"), "b": _fv("2", "t.pdf")},
        [DocumentResult(filename="t.pdf", document_type="invoice")],
    )
    touches = {"a": True, "b": False}
    obs = {o.field_name: o.touched for o in build_observations(draft, touches, _AT)}
    assert obs == touches


def test_carries_document_id_and_timestamp() -> None:
    draft = _draft(
        {"a": _fv("1", "t.pdf")},
        [DocumentResult(filename="t.pdf", document_type="invoice")],
        document_id="doc-42",
    )
    obs = build_observations(draft, {"a": False}, _AT)
    assert obs[0].document_id == "doc-42"
    assert obs[0].approved_at == _AT


def test_default_group_key_is_field_by_document_type() -> None:
    assert DEFAULT_GROUP_KEY == ("field_name", "document_type")
