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
ORIGINS_ENV = "ONTOLOGY_API_ORIGINS"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

# The console's own dev/preview origins. Any other page — including one
# served from a different loopback port — gets no CORS approval, so a
# drive-by browser page cannot drive the write-side triggers.
DEFAULT_ALLOWED_ORIGINS: tuple[str, ...] = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
)


@dataclass(frozen=True)
class ApiConfig:
    """Startup configuration for one API process.

    ``workspace_roots`` are the only directories the API will read
    workspace files from (enforced after symlink resolution). ``host``
    defaults to loopback: this is a local tool and must not be exposed on a
    network interface without an explicit choice. ``allowed_origins`` is
    the browser-origin allowlist for cross-origin calls — the Vite dev
    proxy is same-origin and needs none of them, so the default covers
    only direct calls from the console's own dev/preview origins. The
    API is otherwise unauthenticated by design: it trusts the local
    user account, and non-browser local processes can reach it
    regardless of this list.
    """

    workspace_roots: tuple[str | Path, ...] = field(default_factory=tuple)
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS

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
        raw_origins = source.get(ORIGINS_ENV, "")
        origins = (
            tuple(part.strip() for part in raw_origins.split(",") if part.strip())
            or DEFAULT_ALLOWED_ORIGINS
        )
        return cls(
            workspace_roots=roots, host=host, port=port, allowed_origins=origins
        )
