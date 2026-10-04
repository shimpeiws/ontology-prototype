"""Append-only trace hooks shared by every stage of the operational loop.

Each stage (ingestion, canonicalization, projection, diagnosis, review,
write-back, evaluation) emits events through the ``TraceSink`` protocol.
``JsonlTraceSink`` appends one JSON object per line; the trace file lives in
the workspace runtime directory (``.ontology/trace.jsonl``) and is excluded
from Git. Stages only emit — inspection and export land with the trace issue.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


class TraceSink(Protocol):
    """A destination for trace events emitted by pipeline stages."""

    def emit(self, stage: str, kind: str, data: dict[str, Any]) -> None:
        """Record one event. ``kind`` is a dotted name such as
        ``observation.accepted``; ``data`` is the event payload."""
        ...


class NullTraceSink:
    """Discards events; used when no trace destination is configured."""

    def emit(self, stage: str, kind: str, data: dict[str, Any]) -> None:
        return None


class JsonlTraceSink:
    """Appends trace events as JSON lines to a file (append-only)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def emit(self, stage: str, kind: str, data: dict[str, Any]) -> None:
        event = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "stage": stage,
            "kind": kind,
            "data": data,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, default=str) + "\n")
