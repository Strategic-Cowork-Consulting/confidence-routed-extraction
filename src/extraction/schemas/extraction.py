from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args

from pydantic import BaseModel

# Canonical document-type vocabulary — the single source of truth shared by the
# extraction tool (pipeline) and the reliability model (Feature 2b). `transcript`
# was missing originally, so transcripts were misclassified as `diploma` (#13).
DocumentType = Literal[
    "transcript",
    "diploma",
    "passport",
    "scientific_paper",
    "legal_document",
    "proposal",
    "handwritten_note",
    "unknown",
]
DOCUMENT_TYPES: tuple[DocumentType, ...] = get_args(DocumentType)


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
    document_type: DocumentType
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
    """DocumentDraft returned by GET /review/{id}, extended with approval status.

    Feature 2c additively annotates each field with a reliability flag.
    """

    approval_status: Literal["pending", "approved"]
    field_flags: dict[str, FieldFlag]  # keyed by field_name; one per draft field
    flagged_count: int
    total_fields: int


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


# ---------------------------------------------------------------------------
# Edit capture / reliability observations (Feature 2a)
# ---------------------------------------------------------------------------


class FieldObservation(BaseModel):
    """One per-field touch outcome for an approved document.

    The flattened, grouping-key-tagged unit the reliability model (Feature 2b)
    aggregates over. `touched` is copied verbatim from `core.diff.compute_touches`;
    2a never recomputes it.
    """

    document_id: str
    field_name: str
    document_type: str  # "unknown" when the source document can't be resolved
    touched: bool
    approved_at: datetime


# ---------------------------------------------------------------------------
# Reliability model (Feature 2b)
# ---------------------------------------------------------------------------

Routing = Literal["mandatory_review", "light_review"]
RoutingBasis = Literal["unproven", "high_change_rate", "low_change_rate"]


class ReliabilityScore(BaseModel):
    """Per-field-group reliability + routing, derived on read from touch counts."""

    field_name: str
    document_type: str
    samples: int
    touched: int
    change_rate: float  # touched / samples
    reliability: float  # 1 - change_rate
    proven: bool  # samples >= min_sample_threshold
    routing: Routing
    basis: RoutingBasis


class RoutingDecision(BaseModel):
    """Routing for one field on one document type; `samples == 0` means unseen."""

    field_name: str
    document_type: str
    routing: Routing
    basis: RoutingBasis
    samples: int


# ---------------------------------------------------------------------------
# Confidence highlighting (Feature 2c)
# ---------------------------------------------------------------------------


class FieldFlag(BaseModel):
    """Per-field review flag surfaced on the review-fetch response (Feature 2c).

    `flagged` is the boolean "highlight this" signal the external UI renders.
    """

    field_name: str
    document_type: str
    routing: Routing
    basis: RoutingBasis
    flagged: bool  # routing == "mandatory_review"
    samples: int


# Resolve DraftWithStatus.field_flags forward reference (FieldFlag defined above).
DraftWithStatus.model_rebuild()
