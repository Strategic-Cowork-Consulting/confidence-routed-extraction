from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from extraction import db
from extraction.core.diff import compute_touches
from extraction.core.observations import build_observations
from extraction.schemas.extraction import (
    ApprovalRequest,
    ApprovalResult,
    DocumentDraft,
    DraftWithStatus,
    TouchSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/review")


@router.get("/{document_id}", response_model=DraftWithStatus)
async def get_draft_for_review(document_id: str) -> DraftWithStatus:
    raw = db.get_draft(document_id)
    if raw is None:
        raise HTTPException(status_code=404, detail={"error": "draft_not_found"})

    approval_status = "approved" if db.get_approval(document_id) is not None else "pending"
    draft = DocumentDraft.model_validate(raw)
    return DraftWithStatus(
        document_id=draft.document_id,
        fields=draft.fields,
        documents=draft.documents,
        created_at=draft.created_at,
        approval_status=approval_status,
    )


@router.post("/{document_id}/approve", response_model=ApprovalResult)
async def approve_draft(document_id: str, body: ApprovalRequest) -> ApprovalResult:
    raw = db.get_draft(document_id)
    if raw is None:
        raise HTTPException(status_code=404, detail={"error": "draft_not_found"})

    if db.get_approval(document_id) is not None:
        raise HTTPException(status_code=409, detail={"error": "already_approved"})

    draft = DocumentDraft.model_validate(raw)
    schema_fields = set(draft.fields.keys())
    payload_fields = set(body.fields.keys())

    missing = schema_fields - payload_fields
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"error": "missing_fields", "detail": sorted(missing)},
        )

    unknown = payload_fields - schema_fields
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={"error": "unknown_fields", "detail": sorted(unknown)},
        )

    touches = compute_touches(draft.fields, body.fields)
    touched = sorted(name for name, t in touches.items() if t)
    untouched = sorted(name for name, t in touches.items() if not t)
    total = len(touches)
    touch_count = len(touched)

    approved_at = datetime.now(tz=UTC)
    observations = build_observations(draft, touches, approved_at)
    # Atomic (AC-1.7): approval + observations write in one transaction. A DB
    # failure rolls both back and propagates → HTTP 500; the approval is not
    # persisted and the reviewer resubmits. Deliberate POC trade-off.
    db.save_approval(
        document_id=document_id,
        approved_json=json.dumps(body.fields),
        touch_json=json.dumps(touches),
        approved_at=approved_at.isoformat(),
        observations=observations,
    )
    logger.info(
        "observations_written document_id=%s field_count=%d touched_count=%d doc_types=%d",
        document_id,
        total,
        touch_count,
        len({o.document_type for o in observations}),
    )

    return ApprovalResult(
        document_id=document_id,
        approved_at=approved_at,
        touch_summary=TouchSummary(
            total_fields=total,
            touched_count=touch_count,
            touch_rate=touch_count / total if total > 0 else 0.0,
            touched=touched,
            untouched=untouched,
        ),
    )
