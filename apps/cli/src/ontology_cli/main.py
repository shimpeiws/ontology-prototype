"""CLI entry point.

The CLI owns argument parsing and presentation only. All workspace loading
and validation is delegated to ``ontology_core.load_workspace`` so that the
CLI and any future app always agree on accepted/rejected configurations.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from ontology_core import __version__ as core_version
from ontology_core import (
    Diagnostic,
    ProviderError,
    decide,
    get_diagnosis_run,
    get_projection_run,
    ingest_snapshot,
    load_workspace,
    proposal_view,
    run_diagnosis,
    run_projection,
)

from . import __version__
from .providers import DEFAULT_MODEL, MODEL_ENV, PROVIDERS, resolve_provider

EXIT_OK = 0
EXIT_INVALID = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ontology",
        description=(
            "FDE operational loop CLI. Implemented: 'check' (workspace "
            "validation), 'ingest' (CSV/JSON snapshots into observation "
            "evidence and canonical state), 'project' (deterministic "
            "projection over canonical events), 'diagnose' "
            "(model-backed diagnosis with validated action proposals), "
            "and 'proposals'/'decide' (proposal inspection and human "
            "approve/reject decisions). Write-back and evaluation are "
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

    project = commands.add_parser(
        "project",
        help="compute a projection over canonical events and freeze the run",
    )
    project.add_argument(
        "workspace",
        type=Path,
        help="directory containing domain.yaml, mapping.yaml, evaluation.yaml, agent.yaml",
    )
    project.add_argument(
        "projection",
        nargs="?",
        help="projection name; defaults to the workspace agent's projection",
    )
    project.add_argument(
        "--as-of",
        metavar="DATETIME",
        help="ISO 8601 datetime with an explicit offset; required unless --run",
    )
    project.add_argument(
        "--run",
        metavar="RUN_ID",
        help="inspect a previously frozen projection run instead of computing",
    )
    project.add_argument(
        "--json",
        action="store_true",
        help="print the projection result as JSON",
    )

    diagnose = commands.add_parser(
        "diagnose",
        help="run model-backed diagnosis over projection candidates",
    )
    diagnose.add_argument(
        "workspace",
        type=Path,
        help="directory containing domain.yaml, mapping.yaml, evaluation.yaml, agent.yaml",
    )
    diagnose.add_argument(
        "--run",
        metavar="RUN_ID",
        help="diagnose the candidates of a frozen projection run",
    )
    diagnose.add_argument(
        "--as-of",
        metavar="DATETIME",
        help="ISO 8601 datetime with an explicit offset; compute a fresh "
        "projection first, then diagnose",
    )
    diagnose.add_argument(
        "--inspect",
        metavar="RUN_ID",
        help="inspect a recorded diagnosis run instead of running",
    )
    diagnose.add_argument(
        "--provider",
        choices=PROVIDERS,
        default="openrouter",
        help="provider adapter; 'stub' is a deterministic offline double",
    )
    diagnose.add_argument(
        "--model",
        metavar="MODEL",
        help=f"model id (default: ${MODEL_ENV} or {DEFAULT_MODEL!r})",
    )
    diagnose.add_argument(
        "--json",
        action="store_true",
        help="print the diagnosis result as JSON",
    )

    proposals = commands.add_parser(
        "proposals",
        help="list persisted action proposals or inspect one",
    )
    proposals.add_argument(
        "workspace",
        type=Path,
        help="directory containing domain.yaml, mapping.yaml, evaluation.yaml, agent.yaml",
    )
    proposals.add_argument(
        "--show",
        metavar="PROPOSAL_ID",
        help="inspect one proposal and its decision history",
    )
    proposals.add_argument(
        "--json",
        action="store_true",
        help="print the proposal list or detail as JSON",
    )

    decide_parser = commands.add_parser(
        "decide",
        help="record a human approve/reject decision on a proposal",
    )
    decide_parser.add_argument(
        "workspace",
        type=Path,
        help="directory containing domain.yaml, mapping.yaml, evaluation.yaml, agent.yaml",
    )
    decide_parser.add_argument(
        "proposal",
        metavar="PROPOSAL_ID",
        help="the persisted proposal to decide on",
    )
    decision_group = decide_parser.add_mutually_exclusive_group(required=True)
    decision_group.add_argument(
        "--approve", action="store_true", help="approve the proposal"
    )
    decision_group.add_argument(
        "--reject", action="store_true", help="reject the proposal"
    )
    decide_parser.add_argument(
        "--reviewer",
        metavar="NAME",
        required=True,
        help="explicit reviewer identity recorded with the decision",
    )
    decide_parser.add_argument(
        "--reason",
        metavar="TEXT",
        help="optional reason recorded with the decision",
    )
    decide_parser.add_argument(
        "--json",
        action="store_true",
        help="print the decision result as JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return _cmd_check(args.workspace, as_json=args.json)
    if args.command == "ingest":
        return _cmd_ingest(args.workspace, args.snapshot, as_json=args.json)
    if args.command == "project":
        return _cmd_project(
            args.workspace,
            args.projection,
            as_of=args.as_of,
            run_id=args.run,
            as_json=args.json,
        )
    if args.command == "diagnose":
        return _cmd_diagnose(
            args.workspace,
            inspect_id=args.inspect,
            run_id=args.run,
            as_of=args.as_of,
            provider_name=args.provider,
            model=args.model,
            as_json=args.json,
        )
    if args.command == "proposals":
        return _cmd_proposals(
            args.workspace, show_id=args.show, as_json=args.json
        )
    if args.command == "decide":
        return _cmd_decide(
            args.workspace,
            args.proposal,
            decision="approved" if args.approve else "rejected",
            reviewer=args.reviewer,
            reason=args.reason,
            as_json=args.json,
        )
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
            "canonical": (
                {
                    "entities_upserted": result.canonical.entities_upserted,
                    "events_appended": result.canonical.events_appended,
                    "events_reobserved": result.canonical.events_reobserved,
                }
                if result.canonical is not None
                else None
            ),
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
            if result.canonical is not None:
                print(
                    f"canonical: {result.canonical.entities_upserted} entities, "
                    f"{result.canonical.events_appended} events appended, "
                    f"{result.canonical.events_reobserved} re-observed"
                )

    return EXIT_OK if result.ok else EXIT_INVALID


def _parse_as_of(text: str) -> datetime | None:
    """Parse ``--as-of``: an ISO 8601 datetime with an explicit offset.

    A naive value is rejected rather than interpreted — an as-of time with
    no stated zone is ambiguous and must never be guessed.
    """
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        return None
    return parsed


def _emit_error(
    workspace: Path, as_json: bool, code: str, message: str, *, hint: str | None = None
) -> None:
    """Print one CLI-level error through the same channels as diagnostics."""
    diagnostic = Diagnostic("error", code, message, hint=hint)
    if as_json:
        print(
            json.dumps(
                {
                    "workspace": str(workspace),
                    "ok": False,
                    "diagnostics": [diagnostic.to_dict()],
                },
                indent=2,
            )
        )
    else:
        print(diagnostic.format())


def _cmd_project(
    workspace: Path,
    projection: str | None,
    *,
    as_of: str | None,
    run_id: str | None,
    as_json: bool,
) -> int:
    loaded = load_workspace(workspace)
    if not loaded.ok:
        if as_json:
            print(
                json.dumps(
                    {
                        "workspace": str(workspace),
                        "valid": False,
                        "ok": False,
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

    if run_id is not None and (as_of is not None or projection is not None):
        _emit_error(
            workspace,
            as_json,
            "PROJECTION.CONFLICTING_ARGS",
            "--run inspects a frozen run and cannot be combined with "
            "--as-of or a projection name",
        )
        return EXIT_INVALID

    if run_id is not None:
        try:
            record = get_projection_run(loaded.workspace, run_id)
        except Exception as exc:
            _emit_error(
                workspace,
                as_json,
                "PROJECTION.STORE_ERROR",
                f"cannot read canonical store: {exc}",
            )
            return EXIT_INVALID
        if record is None:
            _emit_error(
                workspace,
                as_json,
                "PROJECTION.UNKNOWN_RUN",
                f"no frozen run {run_id!r}",
                hint="runs are recorded only when the projection evaluates without errors",
            )
            return EXIT_INVALID
        if as_json:
            print(
                json.dumps(
                    {"workspace": str(workspace), "ok": True, "run": record},
                    indent=2,
                )
            )
        else:
            print(
                f"run {record['run_id']}: {record['projection']} "
                f"as-of {record['as_of']} — {len(record['candidates'])} candidates"
            )
            for candidate in record["candidates"]:
                print(
                    f"  {candidate['subject']['entity']}/"
                    f"{candidate['subject']['id']} "
                    f"elapsed {candidate['elapsed_days']} days "
                    f"(evidence: {candidate['evidence']['fact_key']})"
                )
        return EXIT_OK

    if as_of is None:
        _emit_error(
            workspace,
            as_json,
            "PROJECTION.AS_OF_REQUIRED",
            "--as-of is required to compute a projection",
            hint="or pass --run <run-id> to inspect a frozen run",
        )
        return EXIT_INVALID
    as_of_dt = _parse_as_of(as_of)
    if as_of_dt is None:
        _emit_error(
            workspace,
            as_json,
            "PROJECTION.INVALID_AS_OF",
            f"invalid --as-of {as_of!r}: expected an ISO 8601 datetime "
            "with an explicit offset (e.g. '2026-10-05T00:00:00Z')",
        )
        return EXIT_INVALID

    name = projection or loaded.workspace.agent.projection
    result = run_projection(loaded.workspace, name, as_of=as_of_dt)

    if as_json:
        payload = {
            "workspace": str(workspace),
            "ok": result.ok,
            "run_id": result.run_id,
            "projection": result.projection,
            "as_of": result.as_of.isoformat() if result.as_of else None,
            "definition_digest": result.definition_digest,
            "recorded": result.recorded,
            "candidates": [c.to_dict() for c in result.candidates],
            "inputs": result.inputs,
            "diagnostics": [d.to_dict() for d in result.diagnostics],
        }
        print(json.dumps(payload, indent=2))
    else:
        for diagnostic in result.diagnostics:
            print(diagnostic.format())
        if result.run_id is not None:
            print(
                f"run {result.run_id}: {result.projection} — "
                f"{len(result.candidates)} candidates"
            )
            for candidate in result.candidates:
                assignee = candidate.properties.get("assignee")
                suffix = f", assignee {assignee}" if assignee else ""
                print(
                    f"  {candidate.subject_entity}/{candidate.subject_id} "
                    f"completed {candidate.occurred_at.isoformat()}, "
                    f"elapsed {candidate.elapsed_seconds / 86400:.2f} days{suffix}"
                )

    return EXIT_OK if result.ok else EXIT_INVALID


def _cmd_diagnose(
    workspace: Path,
    *,
    inspect_id: str | None,
    run_id: str | None,
    as_of: str | None,
    provider_name: str,
    model: str | None,
    as_json: bool,
) -> int:
    loaded = load_workspace(workspace)
    if not loaded.ok:
        if as_json:
            print(
                json.dumps(
                    {
                        "workspace": str(workspace),
                        "valid": False,
                        "ok": False,
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

    if inspect_id is not None:
        if run_id is not None or as_of is not None:
            _emit_error(
                workspace,
                as_json,
                "DIAGNOSIS.CONFLICTING_ARGS",
                "--inspect reads a recorded run and cannot be combined with "
                "--run or --as-of",
            )
            return EXIT_INVALID
        try:
            record = get_diagnosis_run(loaded.workspace, inspect_id)
        except Exception as exc:
            _emit_error(
                workspace,
                as_json,
                "DIAGNOSIS.STORE_ERROR",
                f"cannot read canonical store: {exc}",
            )
            return EXIT_INVALID
        if record is None:
            _emit_error(
                workspace,
                as_json,
                "DIAGNOSIS.UNKNOWN_RUN",
                f"no recorded diagnosis run {inspect_id!r}",
            )
            return EXIT_INVALID
        if as_json:
            print(
                json.dumps(
                    {"workspace": str(workspace), "ok": True, "run": record},
                    indent=2,
                )
            )
        else:
            _print_run_record(record)
        return EXIT_OK

    # Validate the candidate source before touching the provider so a
    # missing/conflicting --run/--as-of is not masked by a credential error.
    if run_id is not None and as_of is not None:
        _emit_error(
            workspace,
            as_json,
            "DIAGNOSIS.CONFLICTING_ARGS",
            "a diagnosis run takes either --run or --as-of, not both",
        )
        return EXIT_INVALID
    if run_id is None and as_of is None:
        _emit_error(
            workspace,
            as_json,
            "DIAGNOSIS.NO_INPUT",
            "a diagnosis run needs a candidate source",
            hint="pass --run <projection-run-id> or --as-of <datetime>",
        )
        return EXIT_INVALID

    if as_of is not None:
        as_of_dt = _parse_as_of(as_of)
        if as_of_dt is None:
            _emit_error(
                workspace,
                as_json,
                "DIAGNOSIS.INVALID_AS_OF",
                f"invalid --as-of {as_of!r}: expected an ISO 8601 datetime "
                "with an explicit offset (e.g. '2026-10-05T00:00:00Z')",
            )
            return EXIT_INVALID
    else:
        as_of_dt = None

    try:
        provider = resolve_provider(provider_name)
    except ProviderError as exc:
        _emit_error(workspace, as_json, "DIAGNOSIS.PROVIDER_ERROR", str(exc))
        return EXIT_INVALID

    chosen_model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
    result = run_diagnosis(
        loaded.workspace,
        provider,
        model=chosen_model,
        run_id=run_id,
        as_of=as_of_dt,
    )

    if as_json:
        payload = {
            "workspace": str(workspace),
            "ok": result.ok,
            "run_id": result.run_id,
            "projection_run_id": result.projection_run_id,
            "provider": result.provider,
            "model": result.model,
            "status": result.status,
            "candidate_count": result.candidate_count,
            "deliverables": result.deliverables,
            "proposals": [p.to_dict() for p in result.proposals],
            "rejected": result.rejected,
            "recorded": result.recorded,
            "diagnostics": [d.to_dict() for d in result.diagnostics],
        }
        print(json.dumps(payload, indent=2))
    else:
        for diagnostic in result.diagnostics:
            print(diagnostic.format())
        if result.run_id is not None:
            print(
                f"run {result.run_id}: {result.provider}/{result.model} "
                f"— {result.status}, {result.candidate_count} candidates, "
                f"{len(result.proposals)} proposals, "
                f"{len(result.rejected)} rejected"
            )
            if result.deliverables.get("explanation"):
                print(f"  explanation: {result.deliverables['explanation']}")
            for proposal in result.proposals:
                print(
                    f"  proposal: {proposal.action} "
                    f"{json.dumps(proposal.params, sort_keys=True)}"
                )
            for rejected in result.rejected:
                print(f"  rejected[{rejected['index']}]: {rejected['reason']}")

    return EXIT_OK if result.ok else EXIT_INVALID


def _print_run_record(record: dict) -> None:
    print(
        f"run {record['run_id']}: {record['provider']}/{record['model']} "
        f"— {record['status']}, prompt {record['prompt_version']}"
    )
    if record["projection_run_id"]:
        print(f"  projection run: {record['projection_run_id']}")
    print(f"  input digest: {record['input_digest']}")
    if not record["provider_called"]:
        print("  provider was not called (no candidates in scope)")
    explanation = record["deliverables"].get("explanation")
    if explanation:
        print(f"  explanation: {explanation}")
    hypotheses = record["deliverables"].get("hypotheses") or []
    for hypothesis in hypotheses:
        print(f"  hypothesis: {hypothesis['statement']}")
    questions = record["deliverables"].get("follow_up_questions") or []
    for question in questions:
        print(f"  question: {question}")
    for proposal in record["proposals"]:
        print(
            f"  proposal: {proposal['action']} "
            f"{json.dumps(proposal['params'], sort_keys=True)}"
        )
    for rejected in record["rejected"]:
        print(f"  rejected[{rejected['index']}]: {rejected['reason']}")
    for diagnostic in record["diagnostics"]:
        if diagnostic["severity"] == "error":
            print(f"  error: {diagnostic['code']} {diagnostic['message']}")


def _cmd_proposals(
    workspace: Path, *, show_id: str | None, as_json: bool
) -> int:
    loaded = load_workspace(workspace)
    if not loaded.ok:
        if as_json:
            print(
                json.dumps(
                    {
                        "workspace": str(workspace),
                        "ok": False,
                        "diagnostics": [d.to_dict() for d in loaded.diagnostics],
                    },
                    indent=2,
                )
            )
        else:
            for diagnostic in loaded.diagnostics:
                print(diagnostic.format())
        return EXIT_INVALID

    views = proposal_view(loaded.workspace)

    if show_id is not None:
        view = next((v for v in views if v["proposal_id"] == show_id), None)
        if view is None:
            _emit_error(
                workspace,
                as_json,
                "DECISION.UNKNOWN_PROPOSAL",
                f"no persisted proposal {show_id!r}",
                hint="run 'ontology proposals <workspace>' to list "
                "persisted proposals",
            )
            return EXIT_INVALID
        if as_json:
            print(
                json.dumps(
                    {
                        "workspace": str(workspace),
                        "ok": True,
                        "proposal": view,
                    },
                    indent=2,
                )
            )
        else:
            _print_proposal(view, verbose=True)
        return EXIT_OK

    if as_json:
        print(
            json.dumps(
                {
                    "workspace": str(workspace),
                    "ok": True,
                    "proposals": views,
                },
                indent=2,
            )
        )
    else:
        if not views:
            print("no persisted proposals")
        for view in views:
            _print_proposal(view, verbose=False)
    return EXIT_OK


def _print_proposal(view: dict, *, verbose: bool) -> None:
    subject = (
        f"{view['subject_entity']}/{view['subject_id']}"
        if view["subject_id"]
        else "-"
    )
    print(
        f"{view['proposal_id']}: {view['action']} on {subject} "
        f"— {view['status']}"
    )
    latest = view["latest_decision"]
    if latest:
        print(f"  by {latest['reviewer']} at {latest['decided_at']}")
    if verbose:
        print(f"  run: {view['diagnosis_run_id']}")
        print(f"  digest: {view['payload_digest']}")
        print(f"  params: {json.dumps(view['params'], sort_keys=True)}")
        if view["reason"]:
            print(f"  reason: {view['reason']}")
        for related in view["related_entities"]:
            print(
                f"  related: {related['param']} -> "
                f"{related['entity']}/{related['id']}"
            )
        for key, value in view["evidence"].items():
            print(f"  evidence.{key}: {value}")
        for past in view["decisions"]:
            print(
                f"  decision: {past['decision']} by {past['reviewer']} "
                f"at {past['decided_at']}"
                + (f" — {past['reason']}" if past["reason"] else "")
            )


def _cmd_decide(
    workspace: Path,
    proposal_id: str,
    *,
    decision: str,
    reviewer: str,
    reason: str | None,
    as_json: bool,
) -> int:
    loaded = load_workspace(workspace)
    if not loaded.ok:
        if as_json:
            print(
                json.dumps(
                    {
                        "workspace": str(workspace),
                        "ok": False,
                        "diagnostics": [d.to_dict() for d in loaded.diagnostics],
                    },
                    indent=2,
                )
            )
        else:
            for diagnostic in loaded.diagnostics:
                print(diagnostic.format())
        return EXIT_INVALID

    result = decide(
        loaded.workspace,
        proposal_id,
        decision,
        reviewer=reviewer,
        reason=reason,
    )

    if as_json:
        print(
            json.dumps(
                {
                    "workspace": str(workspace),
                    "ok": result.ok,
                    "decision_id": result.decision_id,
                    "proposal_id": result.proposal_id,
                    "decision": result.decision,
                    "recorded": result.recorded,
                    "diagnostics": [d.to_dict() for d in result.diagnostics],
                },
                indent=2,
            )
        )
    else:
        for diagnostic in result.diagnostics:
            print(diagnostic.format())
        if result.recorded:
            print(
                f"{result.decision}: {result.proposal_id} "
                f"(decision {result.decision_id})"
            )
    return EXIT_OK if result.ok else EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
