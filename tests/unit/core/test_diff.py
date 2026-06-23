"""Unit tests for core/diff.py — pure function, no mocking needed."""
from __future__ import annotations

from extraction.core.diff import compute_touches
from extraction.schemas.extraction import FieldValue


def _fv(value: str | None, *, not_found: bool = False) -> FieldValue:
    return FieldValue(value=value, source_document="doc.pdf", not_found=not_found)


ORIGINAL: dict[str, FieldValue] = {
    "title": _fv("Deep Learning Survey"),
    "authors": _fv("Smith et al."),
    "abstract": _fv(None, not_found=True),
    "date": _fv("2023"),
}


def _approved(**overrides: str | None) -> dict[str, str | None]:
    """Return base approved values with optional field overrides."""
    base: dict[str, str | None] = {
        "title": "Deep Learning Survey",
        "authors": "Smith et al.",
        "abstract": None,
        "date": "2023",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Not touched
# ---------------------------------------------------------------------------


def test_identical_values_not_touched() -> None:
    result = compute_touches(ORIGINAL, _approved())
    assert result == {"title": False, "authors": False, "abstract": False, "date": False}


def test_leading_trailing_whitespace_not_touched() -> None:
    result = compute_touches(ORIGINAL, _approved(title="  Deep Learning Survey  "))
    assert result["title"] is False


def test_internal_whitespace_collapse_not_touched() -> None:
    result = compute_touches(ORIGINAL, _approved(title="Deep  Learning   Survey"))
    assert result["title"] is False


def test_null_to_null_not_touched() -> None:
    result = compute_touches(ORIGINAL, _approved())
    assert result["abstract"] is False


# ---------------------------------------------------------------------------
# Touched
# ---------------------------------------------------------------------------


def test_changed_value_touched() -> None:
    result = compute_touches(ORIGINAL, _approved(title="A Different Title"))
    assert result["title"] is True
    assert result["authors"] is False


def test_deletion_is_touched() -> None:
    result = compute_touches(ORIGINAL, _approved(authors=None))
    assert result["authors"] is True


def test_addition_into_blank_is_touched() -> None:
    approved = {
        "title": "Deep Learning Survey",
        "authors": "Smith et al.",
        "abstract": "This paper surveys...",
        "date": "2023",
    }
    result = compute_touches(ORIGINAL, approved)
    assert result["abstract"] is True


def test_content_change_with_whitespace_still_touched() -> None:
    result = compute_touches(ORIGINAL, _approved(title="Machine  Learning Survey"))
    assert result["title"] is True


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_all_schema_fields_returned() -> None:
    result = compute_touches(ORIGINAL, _approved(title="X", authors="Y", abstract="Z", date="W"))
    assert set(result.keys()) == {"title", "authors", "abstract", "date"}


def test_missing_key_in_approved_treated_as_none() -> None:
    # If approved_values is missing a key entirely, .get() returns None → treated as empty
    original = {"title": _fv("Something")}
    result = compute_touches(original, {})  # empty approved dict
    assert result["title"] is True  # "Something" != "" → touched
