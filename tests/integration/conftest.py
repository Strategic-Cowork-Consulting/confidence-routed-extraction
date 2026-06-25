"""Shared fixtures for integration tests — isolate the SQLite DB per test."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from extraction import db
from extraction.api.main import app


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point db at a throwaway file and create the schema."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(db, "_DB_PATH", path)
    db.init_db()
    return path


@pytest.fixture
def client(tmp_db: Path) -> Iterator[TestClient]:
    """TestClient bound to the isolated DB (lifespan re-runs init_db, idempotent)."""
    with TestClient(app) as test_client:
        yield test_client
