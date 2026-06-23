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
