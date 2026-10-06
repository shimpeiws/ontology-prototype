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
import tempfile
from datetime import datetime
from pathlib import Path

from ontology_core import __version__ as core_version
from ontology_core import (
    DEFAULT_BUDGET,
    CsvReminderAdapter,
    Diagnostic,
    EvalError,
    ProviderError,
    RunBudget,
    SubjectSpec,
    decide,
    execute,
    get_diagnosis_run,
    get_evaluation_run,
    get_projection_run,
    ingest_snapshot,
    load_workspace,
    proposal_view,
    run_comparison,
    run_diagnosis,
    run_evaluation,
    run_projection,
)
from ontology_core.evidence import RUNTIME_DIR

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
            "'proposals'/'decide' (proposal inspection and human "
            "approve/reject decisions), 'execute' (write-back of an "
            "approved proposal through a local CSV adapter with "
            "receipts and retry protection), 'evaluate' "
            "(baseline/after outcome evaluation with a frozen report), "
            "and 'diagnose-compare' (bounded provider/model comparison "
            "over one shared frozen eval input)."
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

    execute_parser = commands.add_parser(
        "execute",
        help="execute an approved proposal through a write-back adapter",
    )
    execute_parser.add_argument(
        "workspace",
        type=Path,
        help="directory containing domain.yaml, mapping.yaml, evaluation.yaml, agent.yaml",
    )
    execute_parser.add_argument(
        "proposal",
        metavar="PROPOSAL_ID",
        help="the persisted proposal to execute",
    )
    execute_parser.add_argument(
        "--adapter",
        choices=("csv",),
        default="csv",
        help="write-back adapter; 'csv' appends to "
        "<workspace>/.ontology/reminders.csv",
    )
    execute_parser.add_argument(
        "--json",
        action="store_true",
        help="print the execution result as JSON",
    )

    evaluate_parser = commands.add_parser(
        "evaluate",
        help="evaluate baseline/after cohorts of the contract interval "
        "and freeze the report",
    )
    evaluate_parser.add_argument(
        "workspace",
        type=Path,
        help="directory containing domain.yaml, mapping.yaml, evaluation.yaml, agent.yaml",
    )
    evaluate_parser.add_argument(
        "--boundary",
        metavar="DATETIME",
        help="ISO 8601 datetime with an explicit offset splitting the "
        "baseline and after cohorts; required unless --run",
    )
    evaluate_parser.add_argument(
        "--as-of",
        metavar="DATETIME",
        help="ISO 8601 datetime with an explicit offset; required unless --run",
    )
    evaluate_parser.add_argument(
        "--run",
        metavar="RUN_ID",
        help="inspect a previously frozen evaluation report instead of computing",
    )
    evaluate_parser.add_argument(
        "--json",
        action="store_true",
        help="print the evaluation result as JSON",
    )

    compare_parser = commands.add_parser(
        "diagnose-compare",
        help="evaluate provider/model subjects against one shared frozen "
        "eval input and write a JSON comparison report artifact",
    )
    compare_parser.add_argument(
        "scratch",
        nargs="?",
        type=Path,
        help="directory for the materialized fixture workspace "
        "(default: a fresh temporary directory)",
    )
    compare_parser.add_argument(
        "--subject",
        action="append",
        required=True,
        dest="subjects",
        metavar="PROVIDER:MODEL",
        help="one comparison subject; repeat once per provider/model pair "
        "(e.g. --subject openrouter:openai/gpt-4o-mini)",
    )
    compare_parser.add_argument(
        "--repeats",
        type=int,
        default=DEFAULT_BUDGET.repeats,
        metavar="N",
        help="runs per subject over the frozen input (default: 1)",
    )
    compare_parser.add_argument(
        "--max-provider-requests",
        type=int,
        default=DEFAULT_BUDGET.max_provider_requests,
        metavar="N",
        help="provider-invocation cap per subject "
        f"(default: {DEFAULT_BUDGET.max_provider_requests})",
    )
    compare_parser.add_argument(
        "--out",
        type=Path,
        metavar="PATH",
        help="comparison report artifact path "
        "(default: <scratch>/comparison-report.json)",
    )
    compare_parser.add_argument(
        "--json",
        action="store_true",
        help="print the comparison report as JSON",
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
    if args.command == "execute":
        return _cmd_execute(
            args.workspace,
            args.proposal,
            adapter_name=args.adapter,
            as_json=args.json,
        )
    if args.command == "evaluate":
        return _cmd_evaluate(
            args.workspace,
            boundary=args.boundary,
            as_of=args.as_of,
            run_id=args.run,
            as_json=args.json,
        )
    if args.command == "diagnose-compare":
        return _cmd_diagnose_compare(
            args.scratch,
            args.subjects,
            repeats=args.repeats,
            max_provider_requests=args.max_provider_requests,
            out=args.out,
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
                    "fact_links": [
                        link.to_dict() for link in result.canonical.links
                    ],
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
                if result.canonical.links:
                    for link in result.canonical.links:
                        print(
                            f"link: {link.fact_key} "
                            f"{link.relation} {link.target}"
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

    try:
        views = proposal_view(loaded.workspace)
    except Exception as exc:
        _emit_error(
            workspace,
            as_json,
            "DECISION.STORE_ERROR",
            f"cannot read canonical store: {exc}",
        )
        return EXIT_INVALID

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
        for attempt in view["executions"]:
            receipt = attempt["receipt"] or {}
            detail = (
                f" reminder {receipt['reminder_id']}"
                if receipt.get("reminder_id")
                else ""
            )
            error = f" — {attempt['error']}" if attempt["error"] else ""
            print(
                f"  execution: {attempt['execution_id']} "
                f"attempt {attempt['attempt']} via {attempt['adapter']} "
                f"— {attempt['status']}{detail}{error}"
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


def _cmd_execute(
    workspace: Path,
    proposal_id: str,
    *,
    adapter_name: str,
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
    assert loaded.workspace is not None

    # The CLI supplies the adapter and its destination; the approval
    # gate lives inside the core executor, so no transport can bypass it.
    adapters = {
        "csv": lambda: CsvReminderAdapter(
            Path(loaded.workspace.path) / RUNTIME_DIR / "reminders.csv"
        ),
    }
    result = execute(loaded.workspace, proposal_id, adapters[adapter_name]())

    if as_json:
        print(
            json.dumps(
                {
                    "workspace": str(workspace),
                    "ok": result.ok,
                    "execution_id": result.execution_id,
                    "idempotency_key": result.idempotency_key,
                    "proposal_id": result.proposal_id,
                    "adapter": result.adapter,
                    "status": result.status,
                    "attempt": result.attempt,
                    "attempts": result.attempts,
                    "replayed": result.replayed,
                    "receipt": result.receipt,
                    "error": result.error,
                    "recorded": result.recorded,
                    "diagnostics": [d.to_dict() for d in result.diagnostics],
                },
                indent=2,
            )
        )
    else:
        for diagnostic in result.diagnostics:
            print(diagnostic.format())
        if result.status == "succeeded":
            receipt = result.receipt or {}
            suffix = " (already executed)" if result.replayed else ""
            print(
                f"{result.status}: {result.proposal_id} "
                f"(execution {result.execution_id}, "
                f"attempt {result.attempt}){suffix}"
            )
            if receipt.get("reminder_id"):
                print(
                    f"  reminder {receipt['reminder_id']} "
                    f"-> {receipt.get('path')}"
                )
        elif result.status is not None:
            print(
                f"{result.status}: {result.proposal_id} "
                f"(execution {result.execution_id})"
            )
            if result.error:
                print(f"  error: {result.error}")
    return EXIT_OK if result.ok else EXIT_INVALID


def _cmd_evaluate(
    workspace: Path,
    *,
    boundary: str | None,
    as_of: str | None,
    run_id: str | None,
    as_json: bool,
) -> int:
    if run_id is not None and (as_of is not None or boundary is not None):
        _emit_error(
            workspace,
            as_json,
            "EVALUATION.CONFLICTING_ARGS",
            "--run inspects a frozen report and cannot be combined with "
            "--as-of or --boundary",
        )
        return EXIT_INVALID

    if run_id is not None:
        # Inspection is read-only and needs only the canonical store's
        # location — not the current definitions. A frozen report stays
        # inspectable even when a later edit makes the workspace invalid.
        try:
            record = get_evaluation_run(workspace, run_id)
        except Exception as exc:
            _emit_error(
                workspace,
                as_json,
                "EVALUATION.STORE_ERROR",
                f"cannot read canonical store: {exc}",
            )
            return EXIT_INVALID
        if record is None:
            _emit_error(
                workspace,
                as_json,
                "EVALUATION.UNKNOWN_RUN",
                f"no frozen evaluation report {run_id!r}",
                hint="reports are recorded only when an evaluation "
                "completes without errors",
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
            _print_evaluation_report(record["report"], run_id=record["run_id"])
        return EXIT_OK

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

    missing = []
    if boundary is None:
        missing.append("--boundary")
    if as_of is None:
        missing.append("--as-of")
    if missing:
        _emit_error(
            workspace,
            as_json,
            "EVALUATION.REQUIRED_ARGS",
            f"computing an evaluation needs {' and '.join(missing)}",
            hint="or pass --run <run-id> to inspect a frozen report",
        )
        return EXIT_INVALID
    parsed: dict[str, datetime] = {}
    for flag, text in (("--as-of", as_of), ("--boundary", boundary)):
        parsed_dt = _parse_as_of(text)
        if parsed_dt is None:
            _emit_error(
                workspace,
                as_json,
                "EVALUATION.INVALID_TIME",
                f"invalid {flag} {text!r}: expected an ISO 8601 datetime "
                "with an explicit offset (e.g. '2026-10-05T00:00:00Z')",
            )
            return EXIT_INVALID
        parsed[flag] = parsed_dt

    result = run_evaluation(
        loaded.workspace,
        as_of=parsed["--as-of"],
        boundary=parsed["--boundary"],
    )

    if as_json:
        payload = {
            "workspace": str(workspace),
            "ok": result.ok,
            "run_id": result.run_id,
            "evaluation": result.evaluation,
            "as_of": result.as_of.isoformat() if result.as_of else None,
            "boundary": (
                result.boundary.isoformat() if result.boundary else None
            ),
            "contract_digest": result.contract_digest,
            "recorded": result.recorded,
            "report": result.report,
            "diagnostics": [d.to_dict() for d in result.diagnostics],
        }
        print(json.dumps(payload, indent=2))
    else:
        for diagnostic in result.diagnostics:
            print(diagnostic.format())
        if result.report is not None:
            # Only a recorded run is labeled `run <id>`: an unrecorded
            # report (e.g. the freeze itself failed) must never present
            # a run id that `evaluate --run` cannot find.
            _print_evaluation_report(
                result.report,
                run_id=result.run_id if result.recorded else None,
            )
            if not result.recorded:
                print("  report not frozen (run not recorded)")

    return EXIT_OK if result.ok else EXIT_INVALID


def _print_evaluation_report(report: dict, *, run_id: str | None) -> None:
    label = f"run {run_id}: " if run_id else ""
    print(
        f"{label}{report['evaluation']} — "
        f"as-of {report['as_of']}, boundary {report['cohort_boundary']}"
    )
    # `count` aggregates are interval counts, not durations.
    unit = "intervals" if report["aggregation"] == "count" else report["unit"]
    for name in ("baseline", "after"):
        cohort = report["cohorts"][name]
        counts = cohort["counts"]
        aggregate = (
            f"{cohort['aggregate']:g} {unit}"
            if cohort["aggregate"] is not None
            else "unavailable"
        )
        print(
            f"  {name}: {report['aggregation']} {aggregate} "
            f"({counts['paired']} paired, {counts['pending']} pending, "
            f"{counts['invalid']} invalid, {counts['ambiguous']} ambiguous)"
        )
    change = report["change"]
    if change["status"] == "measured":
        print(
            f"  change: {change['value']:g} {unit} "
            f"({change['direction']})"
        )
    else:
        print(f"  change: unavailable — {change['reason']}")
    target = report["target"]
    print(
        f"  target {target['comparator']} {target['value']:g} {unit}: "
        f"{target['status']}"
    )
    for guardrail in report["guardrails"]:
        detail = (
            f" — {guardrail['reason']}"
            if guardrail.get("reason")
            else ""
        )
        print(f"  guardrail {guardrail['name']}: {guardrail['status']}{detail}")
    if report["unpaired_ends"]:
        print(f"  unpaired end events: {len(report['unpaired_ends'])}")
    print(f"  overall: {report['overall']['status']}")


def _cmd_diagnose_compare(
    scratch: Path | None,
    subject_args: list[str],
    *,
    repeats: int,
    max_provider_requests: int,
    out: Path | None,
    as_json: bool,
) -> int:
    # The scratch path is not a workspace, but _emit_error carries the
    # context directory for diagnostics either way.
    context = scratch or Path(".")
    specs: list[SubjectSpec] = []
    for raw in subject_args:
        provider, sep, model = raw.partition(":")
        provider, model = provider.strip(), model.strip()
        if not sep or not provider or not model:
            _emit_error(
                context,
                as_json,
                "DIAGNOSIS.INVALID_SUBJECT",
                f"invalid --subject {raw!r}: expected PROVIDER:MODEL",
                hint="e.g. --subject openrouter:openai/gpt-4o-mini",
            )
            return EXIT_INVALID
        specs.append(SubjectSpec(provider=provider, model=model))
    if repeats < 1 or max_provider_requests < 1:
        _emit_error(
            context,
            as_json,
            "DIAGNOSIS.INVALID_BUDGET",
            "--repeats and --max-provider-requests must be at least 1",
        )
        return EXIT_INVALID

    scratch_dir = scratch or Path(
        tempfile.mkdtemp(prefix="ontology-compare-")
    )
    report_path = out or scratch_dir / "comparison-report.json"
    try:
        report = run_comparison(
            scratch_dir,
            specs,
            resolve_provider,
            report_path=report_path,
            repeats=repeats,
            budget=RunBudget(
                repeats=repeats,
                max_provider_requests=max_provider_requests,
            ),
        )
    except EvalError as exc:
        _emit_error(
            scratch_dir, as_json, "DIAGNOSIS.COMPARE_FAILED", str(exc)
        )
        return EXIT_INVALID
    except Exception as exc:
        # Final guard: an unexpected harness fault is a diagnostic, never
        # a traceback — and its message may carry sensitive detail, so
        # only the exception class is reported.
        _emit_error(
            scratch_dir,
            as_json,
            "DIAGNOSIS.COMPARE_FAILED",
            f"comparison failed: unexpected {type(exc).__name__}",
        )
        return EXIT_INVALID

    if as_json:
        payload = report.to_dict()
        payload["report_path"] = str(report_path)
        print(json.dumps(payload, indent=2))
    else:
        print(f"comparison report written: {report_path}")
        print(
            f"run records and the .ontology store: {report.workspace_dir}"
        )
        print(
            f"shared input: projection run {report.projection_run_id}, "
            f"{report.candidates} candidates "
            f"({', '.join(report.candidate_ids)}), "
            f"input digest {report.input_digest}"
        )
        if not report.input_identical:
            print("  WARNING: evaluated subjects did not share one "
                  "input digest")
        for subject in report.subjects:
            print(
                f"  [{subject.status}] {subject.provider}/{subject.model} "
                f"— {subject.repeats_completed} run(s), "
                f"{subject.provider_requests} provider request(s), "
                f"{subject.latency_seconds}s, "
                f"checks {'pass' if subject.passed else 'FAIL'}"
            )
            for run in subject.runs:
                for check in run["checks"]:
                    if not check["passed"]:
                        print(f"       FAIL {check['name']}: {check['detail']}")
        for entry in report.unavailable:
            print(
                f"  [unavailable] {entry.provider}/{entry.model}: "
                f"{entry.reason}"
            )

    # A comparison whose subjects could not all be evaluated on identical
    # input — or that evaluated nothing at all — is not a valid result.
    return (
        EXIT_OK
        if report.subjects and report.input_identical
        else EXIT_INVALID
    )


if __name__ == "__main__":
    sys.exit(main())
