"""Unit tests for core/pipeline.py — all Claude API calls are mocked."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anthropic
import httpx
import pytest

from extraction.core.pipeline import (
    AllExtractionsFailed,
    ClaudeRateLimitError,
    ClaudeTimeoutError,
    run_extraction,
)
from extraction.schemas.extraction import FieldSchemaItem

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

_HTTP_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
_SCHEMA = [FieldSchemaItem(name="owner_name"), FieldSchemaItem(name="mailing_address")]
_PDF_DOCS: list[tuple[str, bytes, str]] = [("bill.pdf", b"fake pdf bytes", "application/pdf")]
_TXT_DOCS: list[tuple[str, bytes, str]] = [("doc.txt", b"some text", "text/plain")]


def _mock_client(tool_input: dict[str, Any]) -> AsyncMock:
    """Build a mock AsyncAnthropic client that returns tool_input as the tool_use block."""
    tool_use = MagicMock()
    tool_use.type = "tool_use"
    tool_use.input = tool_input

    message = MagicMock()
    message.content = [tool_use]

    client = MagicMock(spec=anthropic.AsyncAnthropic)
    client.messages = MagicMock()
    client.messages.create = AsyncMock(return_value=message)
    return client  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_successful_extraction_returns_draft():
    client = _mock_client({
        "document_type": "report",
        "fields": {
            "owner_name": {"value": "Acme Holdings LLC", "not_found": False},
            "mailing_address": {"value": "100 Main St", "not_found": False},
        },
    })
    draft = await run_extraction(_TXT_DOCS, _SCHEMA, None, client)

    assert draft.fields["owner_name"].value == "Acme Holdings LLC"
    assert draft.fields["owner_name"].source_document == "doc.txt"
    assert draft.fields["owner_name"].not_found is False
    assert draft.fields["mailing_address"].value == "100 Main St"
    assert draft.documents[0].document_type == "report"
    assert draft.documents[0].extraction_status == "ok"
    assert draft.document_id  # UUID present


async def test_custom_persona_is_forwarded():
    client = _mock_client({
        "document_type": "form",
        "fields": {
            "owner_name": {"value": "v", "not_found": False},
            "mailing_address": {"value": "v", "not_found": False},
        },
    })
    await run_extraction(_TXT_DOCS, _SCHEMA, "Custom persona string", client)
    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["system"] == "Custom persona string"


async def test_default_persona_used_when_none():
    from extraction.core.pipeline import DEFAULT_PERSONA

    client = _mock_client({
        "document_type": "unknown",
        "fields": {
            "owner_name": {"value": None, "not_found": True},
            "mailing_address": {"value": None, "not_found": True},
        },
    })
    await run_extraction(_TXT_DOCS, _SCHEMA, None, client)
    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["system"] == DEFAULT_PERSONA


# ---------------------------------------------------------------------------
# not_found handling
# ---------------------------------------------------------------------------


async def test_not_found_field():
    client = _mock_client({
        "document_type": "form",
        "fields": {
            "owner_name": {"value": None, "not_found": True},
            "mailing_address": {"value": None, "not_found": True},
        },
    })
    draft = await run_extraction(_PDF_DOCS, _SCHEMA, None, client)

    assert draft.fields["owner_name"].not_found is True
    assert draft.fields["owner_name"].value is None
    assert draft.fields["owner_name"].extraction_status == "ok"  # doc succeeded, field absent


# ---------------------------------------------------------------------------
# Multi-document merge: first non-not_found wins
# ---------------------------------------------------------------------------


async def test_first_non_not_found_wins_across_docs():
    call_count = 0

    async def _side_effect(**kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        tool_use = MagicMock()
        tool_use.type = "tool_use"
        if call_count == 1:
            tool_use.input = {
                "document_type": "legal_document",
                "fields": {
                    "owner_name": {"value": "First Owner", "not_found": False},
                    "mailing_address": {"value": None, "not_found": True},
                },
            }
        else:
            tool_use.input = {
                "document_type": "legal_document",
                "fields": {
                    "owner_name": {"value": "Second Owner", "not_found": False},
                    "mailing_address": {"value": "200 Oak Ave", "not_found": False},
                },
            }
        msg = MagicMock()
        msg.content = [tool_use]
        return msg

    client: Any = MagicMock(spec=anthropic.AsyncAnthropic)
    client.messages = MagicMock()
    client.messages.create = AsyncMock(side_effect=_side_effect)

    docs = [
        ("doc1.pdf", b"a", "application/pdf"),
        ("doc2.pdf", b"b", "application/pdf"),
    ]
    draft = await run_extraction(docs, _SCHEMA, None, client)

    assert draft.fields["owner_name"].value == "First Owner"
    assert draft.fields["owner_name"].source_document == "doc1.pdf"
    # doc2 fills in where doc1 had not_found
    assert draft.fields["mailing_address"].value == "200 Oak Ave"
    assert draft.fields["mailing_address"].source_document == "doc2.pdf"


# ---------------------------------------------------------------------------
# Partial failure
# ---------------------------------------------------------------------------


async def test_partial_failure_continues_with_remaining_docs():
    call_count = 0

    async def _side_effect(**kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise anthropic.APIStatusError(
                "server error",
                response=httpx.Response(500, request=_HTTP_REQUEST),
                body=None,
            )
        tool_use = MagicMock()
        tool_use.type = "tool_use"
        tool_use.input = {
            "document_type": "legal_document",
            "fields": {
                "owner_name": {"value": "Acme Holdings LLC", "not_found": False},
                "mailing_address": {"value": "100 Main St", "not_found": False},
            },
        }
        msg = MagicMock()
        msg.content = [tool_use]
        return msg

    client: Any = MagicMock(spec=anthropic.AsyncAnthropic)
    client.messages = MagicMock()
    client.messages.create = AsyncMock(side_effect=_side_effect)

    docs = [("bad.pdf", b"", "application/pdf"), ("good.pdf", b"content", "application/pdf")]
    draft = await run_extraction(docs, _SCHEMA, None, client)

    assert draft.documents[0].extraction_status == "failed"
    assert draft.documents[1].extraction_status == "ok"
    assert draft.fields["owner_name"].value == "Acme Holdings LLC"


async def test_all_failed_raises_all_extractions_failed():
    client: Any = MagicMock(spec=anthropic.AsyncAnthropic)
    client.messages = MagicMock()
    client.messages.create = AsyncMock(
        side_effect=anthropic.APIStatusError(
            "server error",
            response=httpx.Response(500, request=_HTTP_REQUEST),
            body=None,
        )
    )
    with pytest.raises(AllExtractionsFailed):
        await run_extraction(_PDF_DOCS, _SCHEMA, None, client)


# ---------------------------------------------------------------------------
# Fatal Claude errors (abort the whole request)
# ---------------------------------------------------------------------------


async def test_rate_limit_raises_claude_rate_limit_error():
    client: Any = MagicMock(spec=anthropic.AsyncAnthropic)
    client.messages = MagicMock()
    client.messages.create = AsyncMock(
        side_effect=anthropic.RateLimitError(
            "rate limited",
            response=httpx.Response(429, request=_HTTP_REQUEST),
            body=None,
        )
    )
    with pytest.raises(ClaudeRateLimitError):
        await run_extraction(_PDF_DOCS, _SCHEMA, None, client)


async def test_timeout_raises_claude_timeout_error():
    client: Any = MagicMock(spec=anthropic.AsyncAnthropic)
    client.messages = MagicMock()
    client.messages.create = AsyncMock(
        side_effect=anthropic.APITimeoutError(request=_HTTP_REQUEST)
    )
    with pytest.raises(ClaudeTimeoutError):
        await run_extraction(_PDF_DOCS, _SCHEMA, None, client)
