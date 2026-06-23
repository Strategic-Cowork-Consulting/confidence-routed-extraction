from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class FieldSchemaItem(BaseModel):
    name: str
    description: str | None = None


class FieldValue(BaseModel):
    """A single extracted field — structured object, not a bare string.

    Shaped to allow a future `candidates: list[...]` to be added (Open Question #2)
    without a schema migration.
    """

    value: str | None
    source_document: str
    not_found: bool
    extraction_status: Literal["ok", "failed"] = "ok"
    error: str | None = None


class DocumentResult(BaseModel):
    filename: str
    document_type: str
    extraction_status: Literal["ok", "failed"] = "ok"
    error: str | None = None


class DocumentDraft(BaseModel):
    document_id: str
    fields: dict[str, FieldValue]
    documents: list[DocumentResult]
    created_at: datetime


# ---------------------------------------------------------------------------
# Review and approval (Feature 1c)
# ---------------------------------------------------------------------------


class DraftWithStatus(DocumentDraft):
    """DocumentDraft returned by GET /review/{id}, extended with approval status."""

    approval_status: Literal["pending", "approved"]


class ApprovalRequest(BaseModel):
    """Flat field-name → corrected value map submitted by the reviewer."""

    fields: dict[str, str | None]


class TouchSummary(BaseModel):
    total_fields: int
    touched_count: int
    touch_rate: float
    touched: list[str]
    untouched: list[str]


class ApprovalResult(BaseModel):
    document_id: str
    approved_at: datetime
    touch_summary: TouchSummary
