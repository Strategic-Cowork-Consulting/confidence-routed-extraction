from __future__ import annotations

import re

from extraction.schemas.extraction import FieldValue


def _normalize(value: str | None) -> str:
    """Strip leading/trailing whitespace and collapse internal runs to a single space."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", value.strip())


def compute_touches(
    original_fields: dict[str, FieldValue],
    approved_values: dict[str, str | None],
) -> dict[str, bool]:
    """Return per-field touched flag.

    A field is touched when its approved value differs from the original draft value
    after whitespace normalization. Deletions (non-null → null) and additions
    (null/not_found → non-null) both count as touched. Whitespace-only differences
    — including internal runs — do not count.
    """
    return {
        name: _normalize(fv.value) != _normalize(approved_values.get(name))
        for name, fv in original_fields.items()
    }
