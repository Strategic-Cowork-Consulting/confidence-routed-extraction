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
        {"gpa": _fv("t.pdf"), "degree": _fv("d.pdf"), "abstract": _fv("")},
        [
            DocumentResult(filename="t.pdf", document_type="transcript"),
            DocumentResult(filename="d.pdf", document_type="diploma"),
        ],
    )
    assert field_document_types(draft) == {
        "gpa": "transcript",
        "degree": "diploma",
        "abstract": "unknown",  # empty source
    }


def test_build_field_flags_flags_high_change_and_unseen() -> None:
    scores = score_buckets(
        [_row("gpa", "transcript", 5, 3), _row("degree", "transcript", 5, 0)]
    )
    flags = build_field_flags(
        {"gpa": "transcript", "degree": "transcript", "name": "passport"}, scores
    )
    assert flags["gpa"].flagged is True
    assert flags["gpa"].basis == "high_change_rate"
    assert flags["degree"].flagged is False
    assert flags["degree"].basis == "low_change_rate"
    # 'name'/'passport' has no bucket -> unseen -> flagged/unproven, samples 0
    assert flags["name"].flagged is True
    assert flags["name"].basis == "unproven"
    assert flags["name"].samples == 0


def test_flagged_equals_mandatory_routing() -> None:
    scores = score_buckets([_row("gpa", "transcript", 5, 3)])
    flags = build_field_flags({"gpa": "transcript"}, scores)
    f = flags["gpa"]
    assert f.flagged == (f.routing == "mandatory_review")
