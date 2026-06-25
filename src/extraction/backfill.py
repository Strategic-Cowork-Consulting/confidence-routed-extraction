"""Feature 2a — backfill field_observations from approvals recorded before 2a.

Orchestration only: reads approvals via ``db``, rebuilds observations with the
pure ``core.build_observations``, and writes them via ``db.save_observations``.
No SQL here (that lives in ``db``); no I/O in ``core``.

Idempotent — observations use an ``INSERT OR IGNORE`` on
``(document_id, field_name)``, so running this repeatedly never duplicates rows.

Run as a script:  ``python -m extraction.backfill``
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from pydantic import ValidationError

from extraction import db
from extraction.core.observations import build_observations
from extraction.schemas.extraction import DocumentDraft

logger = logging.getLogger(__name__)


def run_backfill() -> dict[str, int]:
    """Derive observations for every existing approval.

    Returns a report: ``approvals_processed``, ``rows_written``, ``skipped``.
    An approval whose draft can't be reconstructed is skipped and counted, not
    fatal — the rest of the run continues.
    """
    processed = 0
    rows_written = 0
    skipped = 0

    for row in db.iter_approvals_with_drafts():
        processed += 1
        document_id = row["document_id"]
        try:
            draft = DocumentDraft.model_validate_json(row["draft_json"])
            touches: dict[str, bool] = json.loads(row["touch_json"])
            approved_at = datetime.fromisoformat(row["approved_at"])
            observations = build_observations(draft, touches, approved_at)
        except (json.JSONDecodeError, ValidationError, KeyError, ValueError):
            # Only the failure modes that genuinely mean "this draft can't be
            # reconstructed" are skipped: bad JSON, a draft that fails schema
            # validation, a missing field/key, or a bad timestamp. Any other
            # exception (e.g. a bug in build_observations) propagates so it
            # surfaces as a real error rather than an ambiguous skip.
            logger.exception("backfill: skipping approval %s — draft unrebuildable", document_id)
            skipped += 1
            continue
        rows_written += db.save_observations(observations)

    report = {
        "approvals_processed": processed,
        "rows_written": rows_written,
        "skipped": skipped,
    }
    logger.info("backfill_run %s", report)
    return report


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    print(run_backfill())
