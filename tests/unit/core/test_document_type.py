"""Unit tests for the document_type vocabulary fix (#13)."""
from __future__ import annotations

from extraction.core.pipeline import _build_extraction_tool, _normalize_document_type
from extraction.schemas.extraction import DOCUMENT_TYPES, FieldSchemaItem


def test_transcript_is_in_vocabulary() -> None:
    assert "transcript" in DOCUMENT_TYPES
    assert "diploma" in DOCUMENT_TYPES  # still distinct from transcript


def test_normalize_keeps_valid_types() -> None:
    assert _normalize_document_type("transcript") == "transcript"
    assert _normalize_document_type("diploma") == "diploma"
    assert _normalize_document_type("unknown") == "unknown"


def test_normalize_coerces_offlist_to_unknown() -> None:
    assert _normalize_document_type("academic_record") == "unknown"
    assert _normalize_document_type("") == "unknown"
    assert _normalize_document_type(None) == "unknown"
    assert _normalize_document_type(123) == "unknown"


def test_tool_schema_constrains_document_type_to_enum() -> None:
    tool = _build_extraction_tool([FieldSchemaItem(name="gpa")])
    enum = tool["input_schema"]["properties"]["document_type"]["enum"]
    assert "transcript" in enum
    assert set(enum) == set(DOCUMENT_TYPES)
