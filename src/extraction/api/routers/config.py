from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from extraction import db
from extraction.core.config import PERSONA_CONFIG_KEY
from extraction.core.pipeline import DEFAULT_PERSONA
from extraction.schemas.extraction import PersonaConfig, ReferenceDoc, ReferenceDocCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

_MAX_NAME_LEN = 200
_MAX_CONTENT_BYTES = 50 * 1024


# --- Analyst persona --------------------------------------------------------


@router.get("/config/persona", response_model=PersonaConfig)
async def get_persona() -> PersonaConfig:
    persona = db.get_config(PERSONA_CONFIG_KEY) or DEFAULT_PERSONA
    return PersonaConfig(persona=persona)


@router.put("/config/persona", response_model=PersonaConfig)
async def set_persona(body: PersonaConfig) -> PersonaConfig:
    persona = body.persona.strip()
    if not persona:
        raise HTTPException(status_code=422, detail={"error": "invalid_persona"})
    db.set_config(PERSONA_CONFIG_KEY, persona)
    logger.info("persona_updated len=%d", len(persona))
    return PersonaConfig(persona=persona)


# --- Reference documents ----------------------------------------------------


@router.post("/reference-docs", response_model=ReferenceDoc)
async def add_reference_doc(body: ReferenceDocCreate) -> ReferenceDoc:
    name = body.name.strip()
    if not name or len(name) > _MAX_NAME_LEN or not body.content:
        raise HTTPException(status_code=422, detail={"error": "invalid_reference_doc"})
    if len(body.content.encode("utf-8")) > _MAX_CONTENT_BYTES:
        raise HTTPException(status_code=422, detail={"error": "reference_doc_too_large"})

    doc = ReferenceDoc(
        id=str(uuid.uuid4()), name=name, content=body.content, created_at=datetime.now(tz=UTC)
    )
    db.add_reference_doc(doc.id, doc.name, doc.content, doc.created_at.isoformat())
    logger.info(
        "reference_doc_added id=%s name=%s content_length=%d", doc.id, doc.name, len(doc.content)
    )
    return doc


@router.get("/reference-docs", response_model=list[ReferenceDoc])
async def list_reference_docs() -> list[ReferenceDoc]:
    return [ReferenceDoc.model_validate(row) for row in db.list_reference_docs()]


@router.get("/reference-docs/{doc_id}", response_model=ReferenceDoc)
async def get_reference_doc(doc_id: str) -> ReferenceDoc:
    row = db.get_reference_doc(doc_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "reference_doc_not_found"})
    return ReferenceDoc.model_validate(row)


@router.delete("/reference-docs/{doc_id}")
async def delete_reference_doc(doc_id: str) -> dict[str, str]:
    if not db.delete_reference_doc(doc_id):
        raise HTTPException(status_code=404, detail={"error": "reference_doc_not_found"})
    logger.info("reference_doc_deleted id=%s", doc_id)
    return {"status": "deleted", "id": doc_id}
