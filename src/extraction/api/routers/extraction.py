from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

from extraction import db
from extraction.core.config import PERSONA_CONFIG_KEY, compose_persona
from extraction.core.pipeline import (
    MAX_FILE_BYTES,
    SUPPORTED_MEDIA_TYPES,
    FileTooLargeError,
    NoDocumentsError,
    UnsupportedFormatError,
    resolve_media_type,
    run_extraction,
)
from extraction.schemas.extraction import DocumentDraft, FieldSchemaItem, ReferenceDoc

router = APIRouter(prefix="/api/v1/extraction")


@router.post("/draft", response_model=DocumentDraft)
async def create_draft(
    documents: Annotated[
        list[UploadFile], File(description="Source documents (pdf, png, jpg, txt)")
    ],
    field_schema: Annotated[
        str, Form(description="JSON array of {name, description?} objects")
    ],
    analyst_persona: Annotated[str | None, Form()] = None,
) -> DocumentDraft:
    if not documents:
        raise NoDocumentsError("At least one document is required")

    try:
        schema_data = json.loads(field_schema)
        schema = [FieldSchemaItem(**item) for item in schema_data]
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_field_schema", "detail": str(exc)},
        ) from exc

    if not schema:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_field_schema", "detail": "field_schema must not be empty"},
        )

    doc_tuples: list[tuple[str, bytes, str]] = []
    for i, upload in enumerate(documents):
        content = await upload.read()
        filename = upload.filename or f"document_{i}"
        media_type = resolve_media_type(upload.content_type, filename)

        if media_type not in SUPPORTED_MEDIA_TYPES:
            raise UnsupportedFormatError(f"Unsupported format for '{filename}': {media_type!r}")
        if len(content) > MAX_FILE_BYTES:
            raise FileTooLargeError(f"'{filename}' exceeds 10 MB limit")

        doc_tuples.append((filename, content, media_type))

    # Feature 1a — resolve the effective persona (per-request > persisted > default)
    # and fold in saved reference documents.
    base_persona = analyst_persona or db.get_config(PERSONA_CONFIG_KEY)
    reference_docs = [ReferenceDoc.model_validate(row) for row in db.list_reference_docs()]
    effective_persona = compose_persona(base_persona, reference_docs)

    draft = await run_extraction(doc_tuples, schema, effective_persona)

    # sqlite3 is synchronous — acceptable for this local POC
    db.save_draft(draft.document_id, draft.model_dump_json(), draft.created_at.isoformat())

    return draft
