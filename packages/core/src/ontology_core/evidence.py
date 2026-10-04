"""Immutable evidence storage for ingestion.

Runtime artifacts live under ``<workspace>/.ontology/`` — a git-ignored
directory:

- ``snapshots/sha256-<digest>.<format>`` — raw source bytes, written once
  under their content digest.
- ``observations/<ingest_id>.jsonl`` — one JSON object per source record,
  written atomically (temp file + rename) so a crash never leaves a
  truncated observation file.
- ``trace.jsonl`` — append-only trace events (see trace.py).

v0.1 assumes a single writer per workspace; no locking is added.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Protocol

from .trace import JsonlTraceSink, TraceSink

RUNTIME_DIR = ".ontology"


class EvidenceStore(Protocol):
    """Narrow persistence boundary for ingestion evidence."""

    def store_snapshot(self, content: bytes, digest: str, format: str) -> Path:
        """Persist raw source bytes once under their content digest."""
        ...

    def write_observations(self, name: str, lines: Iterable[str]) -> Path:
        """Persist one ingest run's observations atomically."""
        ...

    def trace_sink(self) -> TraceSink:
        """Return the trace destination for this store."""
        ...


class FileEvidenceStore:
    """Filesystem evidence store rooted at a runtime directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @property
    def snapshots_dir(self) -> Path:
        return self.root / "snapshots"

    @property
    def observations_dir(self) -> Path:
        return self.root / "observations"

    @property
    def trace_path(self) -> Path:
        return self.root / "trace.jsonl"

    def store_snapshot(self, content: bytes, digest: str, format: str) -> Path:
        name = digest.replace(":", "-") + "." + format
        path = self.snapshots_dir / name
        if path.exists():
            # Content-addressed: an existing file is the same bytes.
            return path
        _atomic_write(path, content)
        return path

    def write_observations(self, name: str, lines: Iterable[str]) -> Path:
        text = "".join(line + "\n" for line in lines)
        path = self.observations_dir / f"{name}.jsonl"
        _atomic_write(path, text.encode("utf-8"))
        return path

    def trace_sink(self) -> TraceSink:
        return JsonlTraceSink(self.trace_path)


def _atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically via a same-directory rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
