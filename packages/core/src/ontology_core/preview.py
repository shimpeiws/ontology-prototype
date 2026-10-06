"""Non-persisting mapping preview over in-memory source samples.

``preview_snapshot`` runs the exact normalization/mapping path of
``ingest_snapshot`` — the same CSV/JSON parsers, the same per-record
normalization boundary, the same binding resolution and ``when`` evaluation —
against source bytes held in memory, so an authoring UI can show what a draft
mapping would produce before any real ingest runs.

The differences from ingest are the point of the type:

- Nothing is persisted: no snapshot store, no observations file, no trace
  events, and no canonical apply. The returned ``Observation`` objects are the
  whole result; ``.ontology/`` is never touched.
- The input is bounded: ``MAX_PREVIEW_BYTES`` and ``MAX_PREVIEW_RECORDS`` keep
  a pasted sample small enough for interactive editing.
- Versions are explicit: the result carries the sample's content digest and
  the same ``mapping_digest`` recipe ingest records, and the preview id and
  observation ids are derived deterministically from those digests — a
  preview result states exactly which input and which mapping produced it.

Never raises on bad input: encoding errors, parse failures, and malformed
records all become diagnostics, same as ingest.

This module deliberately reaches into ``ingest``'s private helpers
(``_parse_csv``/``_parse_json``/``_process_record``/``_mapping_digest``)
rather than reimplementing them: preview parity is guaranteed only if both
call the same code. The helpers stay underscore-private — they are an
intra-package seam, not a public API — so any change to their signature or
semantics must be reviewed against ``preview_snapshot`` and the parity
tests (``test_preview.py`` / ``test_preview_parity_with_core``), which pin
field-for-field equality including run-level diagnostics.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .diagnostics import Diagnostic, Diagnostics
from .ingest import (
    Observation,
    _mapping_digest,
    _parse_csv,
    _parse_json,
    _process_record,
)
from .model import Workspace
from .yamlio import MAX_FILE_BYTES

# A preview sample is pasted text, not an operational snapshot — it stays at
# the workspace-file size cap, far below the 50 MB ingest limit.
MAX_PREVIEW_BYTES = MAX_FILE_BYTES
MAX_PREVIEW_RECORDS = 500


@dataclass
class PreviewResult:
    """Outcome of ``preview_snapshot``: observations plus run diagnostics.

    ``record_count`` counts every record the parser produced, including ones
    dropped by the record cap; ``truncated`` marks that drop. ``preview_id``
    is deterministic — derived from the input and mapping digests — so an
    identical re-run reports identical observation ids.
    """

    diagnostics: list[Diagnostic] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    preview_id: str | None = None
    source: str | None = None
    format: str | None = None
    content_digest: str | None = None
    mapping_digest: str | None = None
    observed_at: datetime | None = None
    record_count: int = 0
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)

    @property
    def accepted(self) -> list[Observation]:
        return [o for o in self.observations if o.status == "accepted"]

    @property
    def rejected(self) -> list[Observation]:
        return [o for o in self.observations if o.status == "rejected"]


def preview_snapshot(
    workspace: Workspace,
    content: bytes,
    *,
    observed_at: datetime | None = None,
) -> PreviewResult:
    """Run the draft mapping over in-memory source bytes — no side effects.

    ``workspace`` is the already-validated draft bundle (e.g. from
    ``load_workspace_texts``); ``content`` is the raw sample. The source
    format comes from ``mapping.source.format``, like ingest.
    """
    diag = Diagnostics()
    result = PreviewResult()

    source = workspace.mapping.source
    result.source = source.name
    result.format = source.format
    result.mapping_digest = _mapping_digest(workspace)
    # Diagnostics name the sample like a snapshot file: "jobs.csv".
    label = f"{source.name}.{source.format}"

    if observed_at is None:
        observed_at = datetime.now(timezone.utc)
    elif observed_at.tzinfo is None or observed_at.tzinfo.utcoffset(observed_at) is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    else:
        observed_at = observed_at.astimezone(timezone.utc)
    result.observed_at = observed_at

    if len(content) > MAX_PREVIEW_BYTES:
        diag.error(
            "PREVIEW.SAMPLE_TOO_LARGE",
            f"sample is {len(content)} bytes; limit is {MAX_PREVIEW_BYTES}",
            file=label,
        )
        result.diagnostics = diag.items
        return result

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        diag.error(
            "INGEST.ENCODING_ERROR",
            f"sample is not valid UTF-8: {exc}",
            file=label,
        )
        result.diagnostics = diag.items
        return result

    result.content_digest = "sha256:" + hashlib.sha256(content).hexdigest()

    records = (
        _parse_csv(text, diag, label)
        if source.format == "csv"
        else _parse_json(text, diag, label)
    )
    if records is None:
        result.diagnostics = diag.items
        return result

    result.record_count = len(records)
    if len(records) > MAX_PREVIEW_RECORDS:
        result.truncated = True
        diag.warning(
            "PREVIEW.TRUNCATED",
            f"sample has {len(records)} records; preview resolves the first "
            f"{MAX_PREVIEW_RECORDS}",
            file=label,
        )
        records = records[:MAX_PREVIEW_RECORDS]
    if not records:
        diag.warning("INGEST.EMPTY", "sample contains no records", file=label)

    result.preview_id = (
        f"prev-{result.content_digest[7:19]}-{result.mapping_digest[7:19]}"
    )
    for row, raw in records:
        result.observations.append(
            _process_record(
                workspace,
                raw,
                row=row,
                file_label=label,
                ingest_id=result.preview_id,
                observed_at=observed_at,
                content_digest=result.content_digest,
                mapping_digest=result.mapping_digest,
                run_diag=diag,
            )
        )
    result.diagnostics = diag.items
    return result


__all__ = [
    "MAX_PREVIEW_BYTES",
    "MAX_PREVIEW_RECORDS",
    "PreviewResult",
    "preview_snapshot",
]
