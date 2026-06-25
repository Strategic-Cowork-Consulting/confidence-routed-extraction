"""Unit tests for core/reliability.py — pure scoring + routing (Feature 2b)."""
from __future__ import annotations

from extraction.core.reliability import (
    DEFAULT_CHANGE_RATE_THRESHOLD,
    DEFAULT_MIN_SAMPLES,
    route_for,
    score_buckets,
)


def _row(field: str, dt: str, samples: int, touched: int) -> dict[str, object]:
    return {"field_name": field, "document_type": dt, "samples": samples, "touched": touched}


def test_defaults_are_locked_values() -> None:
    assert DEFAULT_MIN_SAMPLES == 5
    assert DEFAULT_CHANGE_RATE_THRESHOLD == 0.20


def test_change_rate_and_reliability() -> None:
    [s] = score_buckets([_row("gpa", "transcript", samples=4, touched=3)])
    assert s.change_rate == 0.75
    assert s.reliability == 0.25


def test_unproven_below_min_samples_is_mandatory_regardless_of_rate() -> None:
    # 4 < 5 samples, and a 100% change rate — still 'unproven', not 'high_change_rate'
    [s] = score_buckets([_row("gpa", "transcript", samples=4, touched=4)])
    assert s.proven is False
    assert s.routing == "mandatory_review"
    assert s.basis == "unproven"


def test_proven_at_threshold_is_mandatory_high_change() -> None:
    # 5 samples, rate exactly 0.20 (== threshold) -> mandatory
    [s] = score_buckets([_row("gpa", "transcript", samples=5, touched=1)])
    assert s.proven is True
    assert s.change_rate == 0.20
    assert s.routing == "mandatory_review"
    assert s.basis == "high_change_rate"


def test_proven_just_below_threshold_is_light() -> None:
    # 10 samples, rate 0.10 (< 0.20) -> light
    [s] = score_buckets([_row("degree", "transcript", samples=10, touched=1)])
    assert s.routing == "light_review"
    assert s.basis == "low_change_rate"


def test_zero_change_rate_is_light() -> None:
    [s] = score_buckets([_row("degree", "transcript", samples=6, touched=0)])
    assert s.change_rate == 0.0
    assert s.routing == "light_review"


def test_configurable_thresholds() -> None:
    row = [_row("gpa", "transcript", samples=3, touched=2)]
    # with min_samples=3 it's proven; rate 0.667 >= 0.5 -> mandatory/high
    [s] = score_buckets(row, min_samples=3, change_rate_threshold=0.5)
    assert s.proven is True
    assert s.basis == "high_change_rate"


def test_route_for_found_bucket() -> None:
    scores = score_buckets([_row("gpa", "transcript", samples=5, touched=4)])
    d = route_for("gpa", "transcript", scores)
    assert d.routing == "mandatory_review"
    assert d.basis == "high_change_rate"
    assert d.samples == 5


def test_route_for_unseen_is_mandatory_unproven() -> None:
    scores = score_buckets([_row("gpa", "transcript", samples=5, touched=0)])
    d = route_for("ghost", "passport", scores)
    assert d.routing == "mandatory_review"
    assert d.basis == "unproven"
    assert d.samples == 0


def test_deterministic() -> None:
    rows = [_row("gpa", "transcript", 5, 3), _row("degree", "diploma", 7, 0)]
    assert score_buckets(rows) == score_buckets(rows)
