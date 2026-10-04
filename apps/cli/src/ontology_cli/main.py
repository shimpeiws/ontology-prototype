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
from ontology_core import ingest_snapshot, load_workspace

from . import __version__

EXIT_OK = 0
EXIT_INVALID = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ontology",
        description=(
            "FDE operational loop CLI. Implemented: 'check' (workspace "
            "validation) and 'ingest' (CSV/JSON snapshots into observation "
            "evidence). Projections, agent runs, approvals, and evaluation "
            "are planned capabilities, not yet commands."
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

    ingest = commands.add_parser(
        "ingest",
        help="ingest a CSV/JSON snapshot into immutable observation evidence",
    )
    ingest.add_argument(
        "workspace",
        type=Path,
        help="directory containing domain.yaml, mapping.yaml, evaluation.yaml, agent.yaml",
    )
    ingest.add_argument(
        "snapshot",
        type=Path,
        help="CSV or JSON snapshot file matching mapping.source.format",
    )
    ingest.add_argument(
        "--json",
        action="store_true",
        help="print the ingest result as JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return _cmd_check(args.workspace, as_json=args.json)
    if args.command == "ingest":
        return _cmd_ingest(args.workspace, args.snapshot, as_json=args.json)
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


def _cmd_ingest(workspace: Path, snapshot: Path, *, as_json: bool) -> int:
    loaded = load_workspace(workspace)
    if not loaded.ok:
        if as_json:
            print(
                json.dumps(
                    {
                        "workspace": str(workspace),
                        "valid": False,
                        "diagnostics": [d.to_dict() for d in loaded.diagnostics],
                    },
                    indent=2,
                )
            )
        else:
            for diagnostic in loaded.diagnostics:
                print(diagnostic.format())
            print(f"workspace invalid: {workspace} ({len(loaded.errors)} errors)")
        return EXIT_INVALID

    assert loaded.workspace is not None
    result = ingest_snapshot(loaded.workspace, snapshot)

    if as_json:
        payload = {
            "workspace": str(workspace),
            "snapshot": str(snapshot),
            "ok": result.ok,
            "ingest_id": result.ingest_id,
            "source": result.source,
            "content_digest": result.content_digest,
            "mapping_digest": result.mapping_digest,
            "observed_at": (
                result.observed_at.isoformat() if result.observed_at else None
            ),
            "accepted": len(result.accepted),
            "rejected": len(result.rejected),
            "evidence": {
                "snapshot": str(result.snapshot_path)
                if result.snapshot_path
                else None,
                "observations": str(result.observations_path)
                if result.observations_path
                else None,
            },
            "diagnostics": [d.to_dict() for d in result.diagnostics],
        }
        print(json.dumps(payload, indent=2))
    else:
        for diagnostic in result.diagnostics:
            print(diagnostic.format())
        accepted = len(result.accepted)
        rejected = len(result.rejected)
        if result.ingest_id is not None:
            print(
                f"ingest {result.ingest_id}: "
                f"{accepted} accepted, {rejected} rejected"
            )
            if result.observations_path is not None:
                print(f"evidence: {result.observations_path}")

    return EXIT_OK if result.ok else EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
