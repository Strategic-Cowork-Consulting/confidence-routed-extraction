from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from extraction import db
from extraction.core import reliability
from extraction.core.observations import DEFAULT_GROUP_KEY
from extraction.schemas.extraction import ReliabilityScore, RoutingDecision

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/reliability")


def _validate_thresholds(min_samples: int, change_rate_threshold: float) -> None:
    if min_samples < 1 or not (0.0 <= change_rate_threshold <= 1.0):
        raise HTTPException(status_code=422, detail={"error": "invalid_threshold"})


def _scored(min_samples: int, change_rate_threshold: float) -> list[ReliabilityScore]:
    try:
        counts = db.get_bucket_counts(DEFAULT_GROUP_KEY)
    except ValueError:
        # Defensive: DEFAULT_GROUP_KEY is always valid today, but keep the contract
        # if the grouping key ever becomes caller-supplied.
        raise HTTPException(status_code=422, detail={"error": "invalid_group_key"}) from None
    return reliability.score_buckets(
        counts, min_samples=min_samples, change_rate_threshold=change_rate_threshold
    )


@router.get("", response_model=list[ReliabilityScore])
async def get_reliability(
    document_type: str | None = None,
    min_samples: int = reliability.DEFAULT_MIN_SAMPLES,
    change_rate_threshold: float = reliability.DEFAULT_CHANGE_RATE_THRESHOLD,
) -> list[ReliabilityScore]:
    _validate_thresholds(min_samples, change_rate_threshold)
    scores = _scored(min_samples, change_rate_threshold)
    if document_type is not None:
        scores = [s for s in scores if s.document_type == document_type]
    logger.info(
        "reliability_served buckets=%d proven=%d light_review=%d",
        len(scores),
        sum(s.proven for s in scores),
        sum(s.routing == "light_review" for s in scores),
    )
    return scores


@router.get("/route", response_model=RoutingDecision)
async def get_route(
    field_name: str,
    document_type: str,
    min_samples: int = reliability.DEFAULT_MIN_SAMPLES,
    change_rate_threshold: float = reliability.DEFAULT_CHANGE_RATE_THRESHOLD,
) -> RoutingDecision:
    _validate_thresholds(min_samples, change_rate_threshold)
    scores = _scored(min_samples, change_rate_threshold)
    decision = reliability.route_for(field_name, document_type, scores)
    logger.info(
        "route_decided field=%s document_type=%s routing=%s basis=%s",
        field_name,
        document_type,
        decision.routing,
        decision.basis,
    )
    return decision
