"""CLI entry point.

The CLI owns argument parsing and presentation only. All workspace loading
and validation is delegated to ``ontology_core.load_workspace`` so that the
CLI and any future app always agree on accepted/rejected configurations.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ontology_core import __version__ as core_version
from ontology_core import load_workspace

from . import __version__

EXIT_OK = 0
EXIT_INVALID = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ontology",
        description=(
            "FDE operational loop CLI. Implemented in v0.1 bootstrap: 'check'. "
            "Ingestion, projections, agent runs, approvals, and evaluation are "
            "planned capabilities, not yet commands."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__} (ontology-core {core_version})",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser(
        "check",
        help="load a workspace directory and validate its four definition files",
    )
    check.add_argument(
        "workspace",
        type=Path,
        help="directory containing domain.yaml, mapping.yaml, evaluation.yaml, agent.yaml",
    )
    check.add_argument(
        "--json",
        action="store_true",
        help="print the diagnostics payload as JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return _cmd_check(args.workspace, as_json=args.json)
    return EXIT_INVALID  # pragma: no cover - argparse enforces the subcommand


def _cmd_check(workspace: Path, *, as_json: bool) -> int:
    result = load_workspace(workspace)

    if as_json:
        payload = {
            "workspace": str(workspace),
            "valid": result.ok,
            "diagnostics": [d.to_dict() for d in result.diagnostics],
        }
        print(json.dumps(payload, indent=2))
    else:
        for diagnostic in result.diagnostics:
            print(diagnostic.format())
        error_count = len(result.errors)
        warning_count = len(result.warnings)
        if result.ok:
            print(
                f"workspace valid: {workspace} "
                f"({error_count} errors, {warning_count} warnings)"
            )
        else:
            print(
                f"workspace invalid: {workspace} "
                f"({error_count} errors, {warning_count} warnings)"
            )

    return EXIT_OK if result.ok else EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
