"""Feature 1a — compose the effective extraction persona from config + reference docs.

Pure module: no FastAPI, no sqlite3. Takes the resolved persona (per-request or
persisted, may be ``None``) plus the saved reference documents and produces the
system prompt the extraction step should use.
"""
from __future__ import annotations

from extraction.core.pipeline import DEFAULT_PERSONA
from extraction.schemas.extraction import ReferenceDoc

PERSONA_CONFIG_KEY = "analyst_persona"
_REFERENCE_HEADER = "# Reference material"


def compose_persona(
    persona: str | None, reference_docs: list[ReferenceDoc]
) -> str | None:
    """Build the effective system prompt.

    - No persona and no reference docs -> ``None`` (extraction uses ``DEFAULT_PERSONA``,
      so behavior is unchanged when nothing is configured).
    - Persona only -> the persona verbatim.
    - Reference docs present -> the base persona (the given one, else ``DEFAULT_PERSONA``)
      followed by every reference document, clearly delimited.
    """
    if persona is None and not reference_docs:
        return None
    base = persona if persona is not None else DEFAULT_PERSONA
    if not reference_docs:
        return base
    refs = "\n\n".join(f"## Reference: {doc.name}\n{doc.content}" for doc in reference_docs)
    return f"{base}\n\n{_REFERENCE_HEADER}\n\n{refs}"
