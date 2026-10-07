"""CLI tests for `ontology project`."""

from __future__ import annotations

import json
from pathlib import Path

from clisupport import copy_example

from ontology_cli.main import EXIT_INVALID, EXIT_OK, main

JOBS_CSV = (
    "id,status,assignee,completed_at,invoiced_at\n"
    "101,completed,taro,2026-10-01,\n"
    "102,in_progress,hanako,,\n"
)


def write_snapshot(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def ingested_workspace(tmp_path: Path) -> Path:
    workspace = copy_example(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", JOBS_CSV)
    assert (
        main(["ingest", str(workspace), str(snapshot), "--source", "jobs"])
        == EXIT_OK
    )
    return workspace


def test_project_selects_delayed_candidate(tmp_path: Path, capsys) -> None:
    workspace = ingested_workspace(tmp_path)

    assert (
        main(["project", str(workspace), "--as-of", "2026-10-05T00:00:00Z"])
        == EXIT_OK
    )
    out = capsys.readouterr().out
    assert "1 candidates" in out
    assert "Job/101" in out
    assert "assignee taro" in out


def test_project_json_output(tmp_path: Path, capsys) -> None:
    workspace = ingested_workspace(tmp_path)
    capsys.readouterr()

    assert (
        main(
            [
                "project",
                str(workspace),
                "CompletedButNotInvoiced",
                "--as-of",
                "2026-10-05T00:00:00Z",
                "--json",
            ]
        )
        == EXIT_OK
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["recorded"] is True
    assert payload["definition_digest"].startswith("sha256:")
    assert [c["subject"]["id"] for c in payload["candidates"]] == ["101"]


def test_project_run_inspection(tmp_path: Path, capsys) -> None:
    workspace = ingested_workspace(tmp_path)
    capsys.readouterr()
    assert (
        main(
            [
                "project",
                str(workspace),
                "--as-of",
                "2026-10-05T00:00:00Z",
                "--json",
            ]
        )
        == EXIT_OK
    )
    run_id = json.loads(capsys.readouterr().out)["run_id"]

    assert main(["project", str(workspace), "--run", run_id]) == EXIT_OK
    out = capsys.readouterr().out
    assert run_id in out
    assert "Job/101" in out

    assert (
        main(["project", str(workspace), "--run", "prj-nope"]) == EXIT_INVALID
    )
    assert "PROJECTION.UNKNOWN_RUN" in capsys.readouterr().out


def test_project_json_on_error_paths(tmp_path: Path, capsys) -> None:
    workspace = ingested_workspace(tmp_path)
    capsys.readouterr()

    # --json must produce parseable JSON on every error path, like check and
    # ingest already do.
    assert (
        main(["project", str(workspace), "--as-of", "bogus", "--json"])
        == EXIT_INVALID
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "PROJECTION.INVALID_AS_OF"

    assert main(["project", str(workspace), "--json"]) == EXIT_INVALID
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "PROJECTION.AS_OF_REQUIRED"

    assert (
        main(["project", str(workspace), "--run", "prj-nope", "--json"])
        == EXIT_INVALID
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "PROJECTION.UNKNOWN_RUN"

    # An invalid workspace reports JSON too.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert (
        main(["project", str(empty), "--as-of", "2026-10-05T00:00:00Z", "--json"])
        == EXIT_INVALID
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["diagnostics"]


def test_project_run_conflicts_with_compute_args(
    tmp_path: Path, capsys
) -> None:
    workspace = ingested_workspace(tmp_path)
    capsys.readouterr()

    assert (
        main(["project", str(workspace), "CompletedButNotInvoiced", "--run", "prj-x"])
        == EXIT_INVALID
    )
    assert "PROJECTION.CONFLICTING_ARGS" in capsys.readouterr().out

    assert (
        main(
            [
                "project",
                str(workspace),
                "--run",
                "prj-x",
                "--as-of",
                "2026-10-05T00:00:00Z",
            ]
        )
        == EXIT_INVALID
    )
    assert "PROJECTION.CONFLICTING_ARGS" in capsys.readouterr().out


def test_project_run_json_is_enveloped(tmp_path: Path, capsys) -> None:
    workspace = ingested_workspace(tmp_path)
    capsys.readouterr()
    assert (
        main(
            [
                "project",
                str(workspace),
                "--as-of",
                "2026-10-05T00:00:00Z",
                "--json",
            ]
        )
        == EXIT_OK
    )
    run_id = json.loads(capsys.readouterr().out)["run_id"]

    assert (
        main(["project", str(workspace), "--run", run_id, "--json"]) == EXIT_OK
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["run"]["run_id"] == run_id
    assert payload["run"]["candidates"]


def test_project_run_reports_store_error(tmp_path: Path, capsys) -> None:
    workspace = copy_example(tmp_path)
    runtime = workspace / ".ontology"
    runtime.mkdir()
    (runtime / "canon.db").write_bytes(b"not a database")

    # A broken canon.db must surface as a diagnostic, not a traceback — and
    # stay JSON-parseable under --json.
    assert (
        main(["project", str(workspace), "--run", "prj-x", "--json"])
        == EXIT_INVALID
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "PROJECTION.STORE_ERROR"


def test_project_run_is_read_only_on_fresh_workspace(
    tmp_path: Path, capsys
) -> None:
    workspace = copy_example(tmp_path)
    # --run on a workspace with no canon.db must not create runtime artifacts.
    assert (
        main(["project", str(workspace), "--run", "prj-nope"]) == EXIT_INVALID
    )
    capsys.readouterr()
    assert not (workspace / ".ontology" / "canon.db").exists()


def test_as_of_requires_explicit_offset(tmp_path: Path, capsys) -> None:
    workspace = ingested_workspace(tmp_path)

    # INV-008: an explicit as-of is required; naive or malformed values are
    # rejected, never guessed.
    assert main(["project", str(workspace)]) == EXIT_INVALID
    assert "--as-of is required" in capsys.readouterr().out

    assert (
        main(["project", str(workspace), "--as-of", "2026-10-05"])
        == EXIT_INVALID
    )
    assert "invalid --as-of" in capsys.readouterr().out

    assert (
        main(["project", str(workspace), "--as-of", "2026-10-05 12:00"])
        == EXIT_INVALID
    )
    assert "invalid --as-of" in capsys.readouterr().out

    assert (
        main(["project", str(workspace), "--as-of", "not-a-date"])
        == EXIT_INVALID
    )


def test_project_unknown_projection(tmp_path: Path, capsys) -> None:
    workspace = ingested_workspace(tmp_path)

    assert (
        main(
            [
                "project",
                str(workspace),
                "Nope",
                "--as-of",
                "2026-10-05T00:00:00Z",
            ]
        )
        == EXIT_INVALID
    )
    assert "PROJECTION.UNKNOWN_PROJECTION" in capsys.readouterr().out
