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
import tempfile
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
            # Content-addressed: an existing file is accepted only when its
            # bytes match; a mismatch means corrupted or substituted evidence.
            if path.read_bytes() == content:
                return path
            raise FileExistsError(
                f"snapshot evidence exists with different bytes: {path}"
            )
        _atomic_write(path, content)
        return path

    def write_observations(self, name: str, lines: Iterable[str]) -> Path:
        path = self.observations_dir / f"{name}.jsonl"
        if path.exists():
            # Each run gets a unique ingest id; an existing file is a
            # collision, and observation evidence must never be overwritten.
            raise FileExistsError(f"observation file already exists: {path}")
        text = "".join(line + "\n" for line in lines)
        _atomic_write(path, text.encode("utf-8"))
        return path

    def observation_files(self) -> list[Path]:
        """Persisted observation files, each named ``<ingest_id>.jsonl``."""
        if not self.observations_dir.is_dir():
            return []
        return sorted(self.observations_dir.glob("*.jsonl"))

    def snapshot_files(self) -> list[Path]:
        """Stored source snapshots, named ``sha256-<digest>.<format>``."""
        if not self.snapshots_dir.is_dir():
            return []
        return sorted(self.snapshots_dir.iterdir())

    def trace_sink(self) -> TraceSink:
        return JsonlTraceSink(self.trace_path)


def _atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically via a same-directory rename.

    The temp file is created with ``mkstemp`` (exclusive, unpredictable name)
    so a pre-planted file or symlink cannot redirect the write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f"{path.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
