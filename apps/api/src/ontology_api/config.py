"""Startup configuration for the local API.

The API is a single-user local tool. The only mandatory configuration is
the set of workspace roots: directories under which workspace paths may be
resolved. Roots come from repeatable ``--root`` flags or the
``ONTOLOGY_API_ROOTS`` environment variable (``os.pathsep``-separated).
With no roots configured the workspace endpoints fail closed — there is no
implicit allow-all.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

ROOTS_ENV = "ONTOLOGY_API_ROOTS"
HOST_ENV = "ONTOLOGY_API_HOST"
PORT_ENV = "ONTOLOGY_API_PORT"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


@dataclass(frozen=True)
class ApiConfig:
    """Startup configuration for one API process.

    ``workspace_roots`` are the only directories the API will read
    workspace files from (enforced after symlink resolution). ``host``
    defaults to loopback: this is a local tool and must not be exposed on a
    network interface without an explicit choice.
    """

    workspace_roots: tuple[str | Path, ...] = field(default_factory=tuple)
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ApiConfig:
        source = os.environ if env is None else env
        raw_roots = source.get(ROOTS_ENV, "")
        roots = tuple(
            Path(part).expanduser()
            for part in raw_roots.split(os.pathsep)
            if part.strip()
        )
        host = source.get(HOST_ENV, DEFAULT_HOST)
        raw_port = source.get(PORT_ENV, "")
        try:
            port = int(raw_port) if raw_port.strip() else DEFAULT_PORT
        except ValueError:
            port = DEFAULT_PORT
        return cls(workspace_roots=roots, host=host, port=port)
