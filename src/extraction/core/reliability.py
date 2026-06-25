"""Feature 2b — reliability model: scores + routing over touch-observation counts.

Pure module: no FastAPI, no sqlite3. Consumes the per-bucket counts produced by
``db.get_bucket_counts`` (themselves built from Feature 2a's observations) and
turns each bucket into a change-rate score and a routing decision.

Routing truth table (per field-group):
    samples <  min_samples              -> mandatory_review / unproven
    samples >= min_samples, rate >= thr -> mandatory_review / high_change_rate
    samples >= min_samples, rate <  thr -> light_review     / low_change_rate
    (no observations for the bucket)    -> mandatory_review / unproven

The locked principle (design decision #9): an unproven or unseen field-group
always routes to mandatory review and never inherits another bucket's score.
"""
from __future__ import annotations

from typing import Any

from extraction.schemas.extraction import (
    FieldFlag,
    ReliabilityScore,
    Routing,
    RoutingBasis,
    RoutingDecision,
)

# POC defaults (configurable; locked 2026-06-25, design decision #9).
DEFAULT_MIN_SAMPLES = 5
DEFAULT_CHANGE_RATE_THRESHOLD = 0.20


def _decide(
    samples: int, change_rate: float, min_samples: int, threshold: float
) -> tuple[Routing, RoutingBasis]:
    if samples < min_samples:
        return "mandatory_review", "unproven"
    if change_rate >= threshold:
        return "mandatory_review", "high_change_rate"
    return "light_review", "low_change_rate"


def _score_row(
    row: dict[str, Any], *, min_samples: int, change_rate_threshold: float
) -> ReliabilityScore:
    samples = int(row["samples"])
    touched = int(row["touched"])
    change_rate = touched / samples if samples else 0.0
    routing, basis = _decide(samples, change_rate, min_samples, change_rate_threshold)
    return ReliabilityScore(
        field_name=row["field_name"],
        document_type=row["document_type"],
        samples=samples,
        touched=touched,
        change_rate=change_rate,
        reliability=1.0 - change_rate,
        proven=samples >= min_samples,
        routing=routing,
        basis=basis,
    )


def score_buckets(
    bucket_counts: list[dict[str, Any]],
    *,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    change_rate_threshold: float = DEFAULT_CHANGE_RATE_THRESHOLD,
) -> list[ReliabilityScore]:
    """Score every bucket from ``db.get_bucket_counts`` into a reliability record."""
    return [
        _score_row(row, min_samples=min_samples, change_rate_threshold=change_rate_threshold)
        for row in bucket_counts
    ]


def route_for(
    field_name: str,
    document_type: str,
    scores: list[ReliabilityScore],
) -> RoutingDecision:
    """Routing for one field-group. Unseen combination -> mandatory_review / unproven."""
    for s in scores:
        if s.field_name == field_name and s.document_type == document_type:
            return RoutingDecision(
                field_name=field_name,
                document_type=document_type,
                routing=s.routing,
                basis=s.basis,
                samples=s.samples,
            )
    return RoutingDecision(
        field_name=field_name,
        document_type=document_type,
        routing="mandatory_review",
        basis="unproven",
        samples=0,
    )


def build_field_flags(
    field_types: dict[str, str], scores: list[ReliabilityScore]
) -> dict[str, FieldFlag]:
    """Per-field review flags (Feature 2c) — one ``FieldFlag`` per draft field.

    Each field is routed against its own ``document_type`` bucket; ``flagged`` is
    the boolean "highlight this" signal (``routing == "mandatory_review"``).
    Unproven/unseen field-groups flag by the safe default.
    """
    flags: dict[str, FieldFlag] = {}
    for field_name, document_type in field_types.items():
        decision = route_for(field_name, document_type, scores)
        flags[field_name] = FieldFlag(
            field_name=field_name,
            document_type=document_type,
            routing=decision.routing,
            basis=decision.basis,
            flagged=decision.routing == "mandatory_review",
            samples=decision.samples,
        )
    return flags
