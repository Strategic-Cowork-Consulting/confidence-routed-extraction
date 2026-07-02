"""Unit tests for Feature 2c building blocks: field_document_types + build_field_flags."""
from __future__ import annotations

from datetime import UTC, datetime

from extraction.core.observations import field_document_types
from extraction.core.reliability import build_field_flags, score_buckets
from extraction.schemas.extraction import DocumentDraft, DocumentResult, FieldValue

_AT = datetime(2026, 6, 25, tzinfo=UTC)


def _fv(source: str) -> FieldValue:
    return FieldValue(value="x", source_document=source, not_found=False)


def _draft(fields: dict[str, FieldValue], docs: list[DocumentResult]) -> DocumentDraft:
    return DocumentDraft(document_id="d1", fields=fields, documents=docs, created_at=_AT)


def _row(field: str, dt: str, samples: int, touched: int) -> dict[str, object]:
    return {"field_name": field, "document_type": dt, "samples": samples, "touched": touched}


def test_field_document_types_maps_each_field_to_its_source_type() -> None:
    draft = _draft(
        {"assessed_value": _fv("t.pdf"), "tax_class": _fv("d.pdf"), "exemptions": _fv("")},
        [
            DocumentResult(filename="t.pdf", document_type="invoice"),
            DocumentResult(filename="d.pdf", document_type="receipt"),
        ],
    )
    assert field_document_types(draft) == {
        "assessed_value": "invoice",
        "tax_class": "receipt",
        "exemptions": "unknown",  # empty source
    }


def test_build_field_flags_flags_high_change_and_unseen() -> None:
    scores = score_buckets(
        [_row("assessed_value", "invoice", 5, 3), _row("tax_class", "invoice", 5, 0)]
    )
    flags = build_field_flags(
        {"assessed_value": "invoice", "tax_class": "invoice", "owner_name": "form"}, scores
    )
    assert flags["assessed_value"].flagged is True
    assert flags["assessed_value"].basis == "high_change_rate"
    assert flags["tax_class"].flagged is False
    assert flags["tax_class"].basis == "low_change_rate"
    # 'owner_name'/'form' has no bucket -> unseen -> flagged/unproven, samples 0
    assert flags["owner_name"].flagged is True
    assert flags["owner_name"].basis == "unproven"
    assert flags["owner_name"].samples == 0


def test_flagged_equals_mandatory_routing() -> None:
    scores = score_buckets([_row("assessed_value", "invoice", 5, 3)])
    flags = build_field_flags({"assessed_value": "invoice"}, scores)
    f = flags["assessed_value"]
    assert f.flagged == (f.routing == "mandatory_review")
