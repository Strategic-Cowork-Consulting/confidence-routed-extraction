"""Feature 2a — build per-field touch observations from an approved draft.

Pure module: no FastAPI, no sqlite3. Takes a draft plus the touch record already
computed by ``core.diff.compute_touches`` and flattens it into one
``FieldObservation`` per field, tagged with the grouping dimensions the
reliability model (Feature 2b) aggregates over.
"""
from __future__ import annotations

from datetime import datetime

from extraction.schemas.extraction import DocumentDraft, FieldObservation

# The grouping key consumed by Feature 2b. Defined once here so no query or
# business logic hardcodes a granularity assumption (locked design decision #1).
# Each entry is a column on the field_observations table.
DEFAULT_GROUP_KEY: tuple[str, ...] = ("field_name", "document_type")

UNKNOWN_DOCUMENT_TYPE = "unknown"


def _document_type_for(source_document: str, type_by_filename: dict[str, str]) -> str:
    """Resolve a field's document type from its source filename.

    Falls back to ``"unknown"`` when the source is empty (field not found in any
    document) or matches no document in the draft — never raises, never drops.
    """
    if not source_document:
        return UNKNOWN_DOCUMENT_TYPE
    return type_by_filename.get(source_document, UNKNOWN_DOCUMENT_TYPE)


def field_document_types(draft: DocumentDraft) -> dict[str, str]:
    """Map each draft field to its resolved ``document_type``.

    Shared by Feature 2a (observation capture) and Feature 2c (review flags) so the
    field→document_type derivation lives in exactly one place. Unresolved sources
    resolve to ``"unknown"``.
    """
    type_by_filename = {doc.filename: doc.document_type for doc in draft.documents}
    return {
        field_name: _document_type_for(fv.source_document, type_by_filename)
        for field_name, fv in draft.fields.items()
    }


def build_observations(
    draft: DocumentDraft,
    touches: dict[str, bool],
    approved_at: datetime,
) -> list[FieldObservation]:
    """Flatten a draft + its touch record into one observation per field.

    `touches` is the verbatim output of ``compute_touches`` for this approval;
    `touched` is copied straight through, not recomputed.
    """
    types = field_document_types(draft)
    return [
        FieldObservation(
            document_id=draft.document_id,
            field_name=field_name,
            document_type=types[field_name],
            touched=touches[field_name],
            approved_at=approved_at,
        )
        for field_name in draft.fields
    ]
