"""CLI tests for `ontology evaluate`."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from clisupport import copy_example

from ontology_cli.main import EXIT_INVALID, EXIT_OK, main
from ontology_core import SqliteCanonicalStore

JOBS_CSV = (
    "id,status,assignee,completed_at,invoiced_at\n"
    "B1,completed,taro,2026-09-21,2026-09-30\n"
    "B2,completed,taro,2026-09-22,2026-09-30\n"
    "B3,completed,taro,2026-09-20,2026-09-30\n"
    "A1,completed,hanako,2026-10-06,2026-10-09\n"
    "A2,completed,hanako,2026-10-07,2026-10-10\n"
    "A3,completed,hanako,2026-10-06,2026-10-10\n"
    "P1,completed,hanako,2026-10-08,\n"
)

BOUNDARY = "2026-10-05T00:00:00Z"
AS_OF = "2026-10-15T00:00:00Z"


def write_snapshot(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def ingested_workspace(tmp_path: Path) -> Path:
    workspace = copy_example(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", JOBS_CSV)
    assert main(["ingest", str(workspace), str(snapshot)]) == EXIT_OK
    return workspace


def evaluate_args(workspace: Path) -> list[str]:
    return [
        "evaluate",
        str(workspace),
        "--boundary",
        BOUNDARY,
        "--as-of",
        AS_OF,
    ]


def test_evaluate_reports_cohorts(tmp_path: Path, capsys) -> None:
    workspace = ingested_workspace(tmp_path)
    capsys.readouterr()

    assert main(evaluate_args(workspace)) == EXIT_OK
    out = capsys.readouterr().out
    assert "baseline: median 9 days" in out
    assert "after: median 3 days" in out
    assert "1 pending" in out
    assert "change: 6 days (improvement)" in out
    assert "target lt 4 days: attained" in out
    assert "guardrail invoice-error-rate: unavailable" in out
    assert "overall: unverified" in out


def test_evaluate_json_output(tmp_path: Path, capsys) -> None:
    workspace = ingested_workspace(tmp_path)
    capsys.readouterr()

    assert main(evaluate_args(workspace) + ["--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["recorded"] is True
    assert payload["run_id"].startswith("evl-")
    assert payload["contract_digest"].startswith("sha256:")
    report = payload["report"]
    assert report["cohorts"]["baseline"]["aggregate"] == 9.0
    assert report["cohorts"]["after"]["aggregate"] == 3.0
    assert report["cohorts"]["after"]["counts"]["pending"] == 1
    assert report["change"]["value"] == 6.0
    assert report["target"]["status"] == "attained"
    assert report["overall"]["status"] == "unverified"


def test_evaluate_run_inspection(tmp_path: Path, capsys) -> None:
    workspace = ingested_workspace(tmp_path)
    capsys.readouterr()
    assert main(evaluate_args(workspace) + ["--json"]) == EXIT_OK
    run_id = json.loads(capsys.readouterr().out)["run_id"]

    assert main(["evaluate", str(workspace), "--run", run_id]) == EXIT_OK
    out = capsys.readouterr().out
    assert run_id in out
    assert "baseline: median 9 days" in out

    assert (
        main(["evaluate", str(workspace), "--run", run_id, "--json"])
        == EXIT_OK
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["run"]["run_id"] == run_id
    assert payload["run"]["report"]["cohorts"]["after"]["aggregate"] == 3.0


def test_evaluate_unknown_run(tmp_path: Path, capsys) -> None:
    workspace = ingested_workspace(tmp_path)
    capsys.readouterr()

    assert (
        main(["evaluate", str(workspace), "--run", "evl-nope"])
        == EXIT_INVALID
    )
    assert "EVALUATION.UNKNOWN_RUN" in capsys.readouterr().out

    assert (
        main(["evaluate", str(workspace), "--run", "evl-nope", "--json"])
        == EXIT_INVALID
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "EVALUATION.UNKNOWN_RUN"


def test_evaluate_run_is_read_only_on_fresh_workspace(
    tmp_path: Path, capsys
) -> None:
    workspace = copy_example(tmp_path)
    # --run on a workspace with no canon.db must not create runtime artifacts.
    assert (
        main(["evaluate", str(workspace), "--run", "evl-nope"])
        == EXIT_INVALID
    )
    capsys.readouterr()
    assert not (workspace / ".ontology" / "canon.db").exists()


def test_evaluate_run_conflicts_with_compute_args(
    tmp_path: Path, capsys
) -> None:
    workspace = ingested_workspace(tmp_path)
    capsys.readouterr()

    assert (
        main(
            [
                "evaluate",
                str(workspace),
                "--run",
                "evl-x",
                "--as-of",
                AS_OF,
            ]
        )
        == EXIT_INVALID
    )
    assert "EVALUATION.CONFLICTING_ARGS" in capsys.readouterr().out

    assert (
        main(
            [
                "evaluate",
                str(workspace),
                "--run",
                "evl-x",
                "--boundary",
                BOUNDARY,
            ]
        )
        == EXIT_INVALID
    )
    assert "EVALUATION.CONFLICTING_ARGS" in capsys.readouterr().out


def test_evaluate_requires_boundary_and_as_of(
    tmp_path: Path, capsys
) -> None:
    workspace = ingested_workspace(tmp_path)

    assert main(["evaluate", str(workspace)]) == EXIT_INVALID
    assert "EVALUATION.REQUIRED_ARGS" in capsys.readouterr().out

    assert (
        main(["evaluate", str(workspace), "--as-of", AS_OF]) == EXIT_INVALID
    )
    out = capsys.readouterr().out
    assert "EVALUATION.REQUIRED_ARGS" in out
    assert "--boundary" in out


def test_evaluate_times_require_explicit_offset(
    tmp_path: Path, capsys
) -> None:
    workspace = ingested_workspace(tmp_path)

    for flag, bad in (
        ("--as-of", "2026-10-15"),
        ("--as-of", "2026-10-15 12:00"),
        ("--as-of", "not-a-date"),
        ("--boundary", "2026-10-05"),
    ):
        args = evaluate_args(workspace)
        args[args.index(flag) + 1] = bad
        assert main(args) == EXIT_INVALID
        assert "EVALUATION.INVALID_TIME" in capsys.readouterr().out


def test_evaluate_json_on_error_paths(tmp_path: Path, capsys) -> None:
    workspace = ingested_workspace(tmp_path)
    capsys.readouterr()

    assert main(["evaluate", str(workspace), "--json"]) == EXIT_INVALID
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "EVALUATION.REQUIRED_ARGS"

    # An invalid workspace reports JSON too.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["evaluate", str(empty), "--json"]) == EXIT_INVALID
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["diagnostics"]


def test_evaluate_reports_store_error(tmp_path: Path, capsys) -> None:
    workspace = copy_example(tmp_path)
    runtime = workspace / ".ontology"
    runtime.mkdir()
    (runtime / "canon.db").write_bytes(b"not a database")

    # A broken canon.db must surface as a diagnostic, not a traceback — and
    # stay JSON-parseable under --json.
    assert main(evaluate_args(workspace) + ["--json"]) == EXIT_INVALID
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "EVALUATION.STORE_ERROR"


def test_evaluate_not_met_still_exits_ok(tmp_path: Path, capsys) -> None:
    # FIND-004: the exit code reports operational success only — a
    # missed target is a `not_met` report, still inspectable on stdout.
    workspace = copy_example(tmp_path)
    snapshot = write_snapshot(
        tmp_path,
        "jobs.csv",
        "id,status,assignee,completed_at,invoiced_at\n"
        "B1,completed,taro,2026-09-21,2026-09-30\n"
        "A1,completed,hanako,2026-10-06,2026-10-10\n",
    )
    assert main(["ingest", str(workspace), str(snapshot)]) == EXIT_OK
    capsys.readouterr()

    assert main(evaluate_args(workspace)) == EXIT_OK
    out = capsys.readouterr().out
    assert "target lt 4 days: missed" in out
    assert "overall: not_met" in out


def test_evaluate_record_failure_does_not_label_run(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    # FIND-002: a run whose freeze fails must never present its run id
    # as inspectable — `evaluate --run` could not find it.
    workspace = ingested_workspace(tmp_path)
    capsys.readouterr()

    def boom(self, **kwargs):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(
        SqliteCanonicalStore, "record_evaluation_run", boom
    )

    assert main(evaluate_args(workspace)) == EXIT_INVALID
    out = capsys.readouterr().out
    assert "EVALUATION.STORE_ERROR" in out
    assert "report not frozen (run not recorded)" in out
    assert "run evl-" not in out


def test_evaluate_boundary_after_as_of(tmp_path: Path, capsys) -> None:
    workspace = ingested_workspace(tmp_path)
    capsys.readouterr()

    assert (
        main(
            [
                "evaluate",
                str(workspace),
                "--boundary",
                AS_OF,
                "--as-of",
                BOUNDARY,
                "--json",
            ]
        )
        == EXIT_INVALID
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == (
        "EVALUATION.BOUNDARY_AFTER_AS_OF"
    )
    assert payload["recorded"] is False
