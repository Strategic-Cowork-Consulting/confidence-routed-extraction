"""Integration tests for the Feature 2b reliability API."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from extraction import db
from extraction.schemas.extraction import FieldObservation

_AT = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)


def _seed(specs: list[tuple[str, str, int, int]]) -> None:
    """specs: list of (field_name, document_type, samples, touched)."""
    obs: list[FieldObservation] = []
    for field, dt, samples, touched in specs:
        for i in range(samples):
            obs.append(
                FieldObservation(
                    document_id=f"{field}-{dt}-{i}",
                    field_name=field,
                    document_type=dt,
                    touched=i < touched,
                    approved_at=_AT,
                )
            )
    db.save_observations(obs)


def test_reliability_endpoint_scores_and_routes(client: TestClient) -> None:
    _seed(
        [
            ("assessed_value", "invoice", 5, 3),  # proven, rate 0.60 -> mandatory/high
            ("tax_class", "invoice", 6, 0),  # proven, rate 0.00 -> light
            ("owner_name", "invoice", 3, 1),  # 3<5 -> unproven/mandatory
        ]
    )
    r = client.get("/api/v1/reliability")
    assert r.status_code == 200
    by = {s["field_name"]: s for s in r.json()}

    assert by["assessed_value"]["routing"] == "mandatory_review"
    assert by["assessed_value"]["basis"] == "high_change_rate"
    assert by["assessed_value"]["change_rate"] == 0.6
    assert by["tax_class"]["routing"] == "light_review"
    assert by["tax_class"]["basis"] == "low_change_rate"
    assert by["owner_name"]["routing"] == "mandatory_review"
    assert by["owner_name"]["basis"] == "unproven"
    assert by["owner_name"]["proven"] is False


def test_reliability_filter_by_document_type(client: TestClient) -> None:
    _seed([("assessed_value", "invoice", 5, 1), ("assessed_value", "receipt", 5, 1)])
    r = client.get("/api/v1/reliability", params={"document_type": "receipt"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["document_type"] == "receipt"


def test_route_endpoint_for_known_bucket(client: TestClient) -> None:
    _seed([("assessed_value", "invoice", 6, 0)])  # proven, rate 0 -> light
    r = client.get(
        "/api/v1/reliability/route",
        params={"field_name": "assessed_value", "document_type": "invoice"},
    )
    assert r.status_code == 200
    assert r.json() == {
        "field_name": "assessed_value",
        "document_type": "invoice",
        "routing": "light_review",
        "basis": "low_change_rate",
        "samples": 6,
    }


def test_route_endpoint_unseen_defaults_mandatory(client: TestClient) -> None:
    _seed([("assessed_value", "invoice", 6, 0)])
    r = client.get(
        "/api/v1/reliability/route",
        params={"field_name": "ghost", "document_type": "form"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["routing"] == "mandatory_review"
    assert body["basis"] == "unproven"
    assert body["samples"] == 0


def test_empty_store_returns_empty_list(client: TestClient) -> None:
    r = client.get("/api/v1/reliability")
    assert r.status_code == 200
    assert r.json() == []


def test_invalid_threshold_is_422(client: TestClient) -> None:
    bad_rate = client.get("/api/v1/reliability", params={"change_rate_threshold": 1.5})
    assert bad_rate.status_code == 422
    bad_min = client.get("/api/v1/reliability", params={"min_samples": 0})
    assert bad_min.status_code == 422


def test_override_thresholds_via_query(client: TestClient) -> None:
    _seed([("assessed_value", "invoice", 3, 2)])  # 3 samples, rate 0.667
    # default min_samples=5 -> unproven
    assert client.get("/api/v1/reliability").json()[0]["basis"] == "unproven"
    # override min_samples=3, threshold=0.5 -> proven & high_change_rate
    r = client.get(
        "/api/v1/reliability",
        params={"min_samples": 3, "change_rate_threshold": 0.5},
    )
    assert r.json()[0]["basis"] == "high_change_rate"


def test_get_bucket_counts_rejects_invalid_group_key(tmp_db: Path) -> None:
    """ERR-3 contract: an off-whitelist grouping column raises ValueError."""
    with pytest.raises(ValueError):
        db.get_bucket_counts(("nonsense_column",))
