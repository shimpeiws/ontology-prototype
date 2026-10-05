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
    assert main(["ingest", str(workspace), str(snapshot)]) == EXIT_OK
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
