"""Unit tests for core/config.py — pure persona composition (Feature 1a)."""
from __future__ import annotations

from datetime import UTC, datetime

from extraction.core.config import compose_persona
from extraction.core.pipeline import DEFAULT_PERSONA
from extraction.schemas.extraction import ReferenceDoc

_AT = datetime(2026, 6, 25, tzinfo=UTC)


def _ref(name: str, content: str) -> ReferenceDoc:
    return ReferenceDoc(id="r1", name=name, content=content, created_at=_AT)


def test_no_persona_no_refs_returns_none() -> None:
    assert compose_persona(None, []) is None


def test_persona_only_returned_verbatim() -> None:
    assert compose_persona("You are a paralegal.", []) == "You are a paralegal."


def test_refs_without_persona_use_default_base() -> None:
    out = compose_persona(None, [_ref("Guide", "Cite sources.")])
    assert out is not None
    assert out.startswith(DEFAULT_PERSONA)
    assert "Cite sources." in out
    assert "Guide" in out


def test_persona_and_refs_combined() -> None:
    out = compose_persona("Custom analyst.", [_ref("G1", "AAA"), _ref("G2", "BBB")])
    assert out is not None
    assert out.startswith("Custom analyst.")
    assert "AAA" in out and "BBB" in out
    assert "# Reference material" in out
