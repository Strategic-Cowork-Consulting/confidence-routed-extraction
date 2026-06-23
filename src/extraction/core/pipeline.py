from __future__ import annotations

import base64
import mimetypes
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import anthropic

from extraction.schemas.extraction import (
    DocumentDraft,
    DocumentResult,
    FieldSchemaItem,
    FieldValue,
)

DEFAULT_PERSONA = (
    "You are a precise document analyst with one year of professional experience. "
    "You are trained in reviewing passports, legal documents, proposals, diplomas, "
    "and published papers (journal, scientific, and academic). "
    "Extract the requested fields accurately and completely from the provided documents."
)

SUPPORTED_MEDIA_TYPES: frozenset[str] = frozenset(
    {"application/pdf", "image/png", "image/jpeg", "image/jpg", "text/plain"}
)
MAX_FILE_BYTES: int = 10 * 1024 * 1024  # 10 MB


# ---------------------------------------------------------------------------
# Public exceptions — caught by FastAPI exception handlers in api/main.py
# ---------------------------------------------------------------------------


class ExtractionError(Exception):
    pass


class UnsupportedFormatError(ExtractionError):
    pass


class FileTooLargeError(ExtractionError):
    pass


class NoDocumentsError(ExtractionError):
    pass


class AllExtractionsFailed(ExtractionError):
    pass


class ClaudeRateLimitError(ExtractionError):
    pass


class ClaudeTimeoutError(ExtractionError):
    pass


# ---------------------------------------------------------------------------
# Internal exception — marks a single document failed without aborting the run
# ---------------------------------------------------------------------------


class _DocumentFailed(Exception):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_media_type(content_type: str | None, filename: str) -> str:
    """Return the effective MIME type, falling back to filename extension."""
    if content_type and content_type not in ("application/octet-stream", ""):
        return content_type
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or ""


def _build_extraction_tool(field_schema: list[FieldSchemaItem]) -> dict[str, Any]:
    field_props: dict[str, Any] = {}
    for field in field_schema:
        desc = f"Extracted value for '{field.name}'"
        if field.description:
            desc += f" — {field.description}"
        field_props[field.name] = {
            "type": "object",
            "description": desc,
            "properties": {
                "value": {
                    "type": ["string", "null"],
                    "description": "The extracted value, or null if not present in this document",
                },
                "not_found": {
                    "type": "boolean",
                    "description": "True if the field was not found in this document",
                },
            },
            "required": ["value", "not_found"],
        }

    return {
        "name": "extract_document_data",
        "description": (
            "Extract the document type and all requested field values from this document."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document_type": {
                    "type": "string",
                    "description": (
                        "Detected document type. Use one of: 'scientific_paper', "
                        "'legal_document', 'passport', 'diploma', 'proposal', "
                        "'handwritten_note', 'unknown'."
                    ),
                },
                "fields": {
                    "type": "object",
                    "description": "Extracted values for each requested field",
                    "properties": field_props,
                    "required": [f.name for f in field_schema],
                },
            },
            "required": ["document_type", "fields"],
        },
    }


def _make_content_block(content: bytes, media_type: str) -> dict[str, Any]:
    if media_type in ("image/png", "image/jpeg", "image/jpg"):
        norm = "image/jpeg" if media_type == "image/jpg" else media_type
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": norm,
                "data": base64.standard_b64encode(content).decode(),
            },
        }
    if media_type == "application/pdf":
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(content).decode(),
            },
        }
    # text/plain
    return {"type": "text", "text": content.decode("utf-8", errors="replace")}


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------


async def _extract_one(
    filename: str,
    content: bytes,
    media_type: str,
    field_schema: list[FieldSchemaItem],
    persona: str,
    tool: dict[str, Any],
    client: anthropic.AsyncAnthropic,
) -> tuple[DocumentResult, dict[str, FieldValue]]:
    content_block = _make_content_block(content, media_type)
    try:
        response = await client.messages.create(  # type: ignore[call-overload]
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=persona,
            tools=[tool],  # type: ignore[arg-type]
            tool_choice={"type": "tool", "name": "extract_document_data"},  # type: ignore[arg-type]
            messages=[
                {
                    "role": "user",
                    "content": [
                        content_block,
                        {"type": "text", "text": "Extract all requested fields from this document."},  # noqa: E501
                    ],
                }
            ],
        )
    except anthropic.RateLimitError as exc:
        raise ClaudeRateLimitError("Claude API rate limit reached") from exc
    except anthropic.APITimeoutError as exc:
        raise ClaudeTimeoutError("Claude API timed out") from exc
    except anthropic.APIError as exc:
        raise _DocumentFailed(f"Claude API error: {exc}") from exc

    tool_use = next(
        (block for block in response.content if block.type == "tool_use"),
        None,
    )
    if tool_use is None:
        raise _DocumentFailed("Claude did not return a tool_use block")

    raw = cast(dict[str, Any], tool_use.input)  # type: ignore[union-attr]
    document_type: str = raw.get("document_type", "unknown")
    raw_fields = cast(dict[str, Any], raw.get("fields", {}))

    field_values: dict[str, FieldValue] = {}
    for field in field_schema:
        raw_field = raw_fields.get(field.name)
        if not isinstance(raw_field, dict):
            raw_field = {}
        value = raw_field.get("value")
        not_found = raw_field.get("not_found", value is None)
        field_values[field.name] = FieldValue(
            value=value if isinstance(value, str) else None,
            source_document=filename,
            not_found=bool(not_found),
        )

    return DocumentResult(filename=filename, document_type=document_type), field_values


async def run_extraction(
    documents: list[tuple[str, bytes, str]],  # (filename, content, media_type)
    field_schema: list[FieldSchemaItem],
    analyst_persona: str | None,
    client: anthropic.AsyncAnthropic | None = None,
) -> DocumentDraft:
    if not documents:
        raise NoDocumentsError("At least one document is required")

    if client is None:
        client = anthropic.AsyncAnthropic()

    persona = analyst_persona or DEFAULT_PERSONA
    tool = _build_extraction_tool(field_schema)

    doc_results: list[DocumentResult] = []
    merged_fields: dict[str, FieldValue] = {}  # first non-not_found value per field wins

    for filename, content, media_type in documents:
        try:
            doc_result, field_values = await _extract_one(
                filename, content, media_type, field_schema, persona, tool, client
            )
            doc_results.append(doc_result)
            for field_name, fv in field_values.items():
                existing = merged_fields.get(field_name)
                if existing is None or (existing.not_found and not fv.not_found):
                    merged_fields[field_name] = fv
        except _DocumentFailed as exc:
            doc_results.append(
                DocumentResult(
                    filename=filename,
                    document_type="unknown",
                    extraction_status="failed",
                    error=str(exc),
                )
            )

    if all(r.extraction_status == "failed" for r in doc_results):
        raise AllExtractionsFailed("All documents failed extraction")

    # Ensure every schema field present even if no document produced it
    for field in field_schema:
        if field.name not in merged_fields:
            merged_fields[field.name] = FieldValue(
                value=None,
                source_document="",
                not_found=True,
                extraction_status="failed",
                error="Field not found in any document",
            )

    return DocumentDraft(
        document_id=str(uuid.uuid4()),
        fields=merged_fields,
        documents=doc_results,
        created_at=datetime.now(tz=UTC),
    )
