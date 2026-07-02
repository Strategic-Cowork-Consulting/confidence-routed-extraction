"""Unit tests for core/diff.py — pure function, no mocking needed."""
from __future__ import annotations

from extraction.core.diff import compute_touches
from extraction.schemas.extraction import FieldValue


def _fv(value: str | None, *, not_found: bool = False) -> FieldValue:
    return FieldValue(value=value, source_document="doc.pdf", not_found=not_found)


ORIGINAL: dict[str, FieldValue] = {
    "owner_name": _fv("Acme Holdings LLC"),
    "mailing_address": _fv("100 Main St"),
    "exemptions": _fv(None, not_found=True),
    "bill_date": _fv("2024-01-15"),
}


def _approved(**overrides: str | None) -> dict[str, str | None]:
    """Return base approved values with optional field overrides."""
    base: dict[str, str | None] = {
        "owner_name": "Acme Holdings LLC",
        "mailing_address": "100 Main St",
        "exemptions": None,
        "bill_date": "2024-01-15",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Not touched
# ---------------------------------------------------------------------------


def test_identical_values_not_touched() -> None:
    result = compute_touches(ORIGINAL, _approved())
    assert result == {
        "owner_name": False,
        "mailing_address": False,
        "exemptions": False,
        "bill_date": False,
    }


def test_leading_trailing_whitespace_not_touched() -> None:
    result = compute_touches(ORIGINAL, _approved(owner_name="  Acme Holdings LLC  "))
    assert result["owner_name"] is False


def test_internal_whitespace_collapse_not_touched() -> None:
    result = compute_touches(ORIGINAL, _approved(owner_name="Acme  Holdings   LLC"))
    assert result["owner_name"] is False


def test_null_to_null_not_touched() -> None:
    result = compute_touches(ORIGINAL, _approved())
    assert result["exemptions"] is False


# ---------------------------------------------------------------------------
# Touched
# ---------------------------------------------------------------------------


def test_changed_value_touched() -> None:
    result = compute_touches(ORIGINAL, _approved(owner_name="A Different Owner"))
    assert result["owner_name"] is True
    assert result["mailing_address"] is False


def test_deletion_is_touched() -> None:
    result = compute_touches(ORIGINAL, _approved(mailing_address=None))
    assert result["mailing_address"] is True


def test_addition_into_blank_is_touched() -> None:
    approved = {
        "owner_name": "Acme Holdings LLC",
        "mailing_address": "100 Main St",
        "exemptions": "Senior exemption applied",
        "bill_date": "2024-01-15",
    }
    result = compute_touches(ORIGINAL, approved)
    assert result["exemptions"] is True


def test_content_change_with_whitespace_still_touched() -> None:
    result = compute_touches(ORIGINAL, _approved(owner_name="Beta  Holdings LLC"))
    assert result["owner_name"] is True


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_all_schema_fields_returned() -> None:
    result = compute_touches(
        ORIGINAL,
        _approved(owner_name="X", mailing_address="Y", exemptions="Z", bill_date="W"),
    )
    assert set(result.keys()) == {"owner_name", "mailing_address", "exemptions", "bill_date"}


def test_missing_key_in_approved_treated_as_none() -> None:
    # If approved_values is missing a key entirely, .get() returns None → treated as empty
    original = {"owner_name": _fv("Something")}
    result = compute_touches(original, {})  # empty approved dict
    assert result["owner_name"] is True  # "Something" != "" → touched
