"""Local startup for the API: ``python -m ontology_api`` or ``ontology-api``.

At least one workspace root is required, via repeatable ``--root`` flags
or ``$ONTOLOGY_API_ROOTS``. The server binds to loopback by default.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import HOST_ENV, PORT_ENV, ROOTS_ENV, ApiConfig
from .main import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ontology-api",
        description=(
            "Local ontology API server. Restricts workspace access to the "
            "directories given by --root or ONTOLOGY_API_ROOTS."
        ),
    )
    parser.add_argument(
        "--root",
        action="append",
        dest="roots",
        metavar="DIR",
        help="allowed workspace root directory (repeatable); "
        f"falls back to ${ROOTS_ENV} (os.pathsep-separated)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help=f"bind host (default: ${HOST_ENV} or 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"bind port (default: ${PORT_ENV} or 8000)",
    )
    args = parser.parse_args(argv)

    env_config = ApiConfig.from_env()
    roots = (
        tuple(Path(r).expanduser() for r in args.roots)
        if args.roots
        else env_config.workspace_roots
    )
    if not roots:
        parser.error(
            f"no workspace roots configured: pass --root DIR or set ${ROOTS_ENV}"
        )
    host = args.host or env_config.host
    port = args.port or env_config.port

    import uvicorn

    uvicorn.run(
        create_app(ApiConfig(workspace_roots=roots, host=host, port=port)),
        host=host,
        port=port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
