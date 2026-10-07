"""CLI tests for `ontology ingest`."""

from __future__ import annotations

import json
from pathlib import Path

from clisupport import EXAMPLE_WORKSPACE, copy_example

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


def test_ingest_example_workspace(tmp_path: Path, capsys) -> None:
    workspace = copy_example(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", JOBS_CSV)

    assert (
        main(["ingest", str(workspace), str(snapshot), "--source", "jobs"])
        == EXIT_OK
    )
    out = capsys.readouterr().out
    assert "2 accepted" in out
    assert "0 rejected" in out
    assert "evidence:" in out


def test_ingest_json_output(tmp_path: Path, capsys) -> None:
    workspace = copy_example(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", JOBS_CSV)

    assert (
        main(
            [
                "ingest",
                str(workspace),
                str(snapshot),
                "--source",
                "jobs",
                "--json",
            ]
        )
        == EXIT_OK
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["accepted"] == 2
    assert payload["rejected"] == 0
    assert payload["content_digest"].startswith("sha256:")
    assert payload["mapping_digest"].startswith("sha256:")
    evidence = Path(payload["evidence"]["observations"])
    assert evidence.exists()
    lines = evidence.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_ingest_multi_source_requires_source_flag(tmp_path: Path, capsys) -> None:
    workspace = copy_example(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", JOBS_CSV)

    assert main(["ingest", str(workspace), str(snapshot)]) == EXIT_INVALID
    assert "INGEST.SOURCE_REQUIRED" in capsys.readouterr().out


def test_ingest_unknown_source_exit_1(tmp_path: Path, capsys) -> None:
    workspace = copy_example(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", JOBS_CSV)

    assert (
        main(
            [
                "ingest",
                str(workspace),
                str(snapshot),
                "--source",
                "bogus",
            ]
        )
        == EXIT_INVALID
    )
    assert "INGEST.UNKNOWN_SOURCE" in capsys.readouterr().out


def test_ingest_second_source(tmp_path: Path, capsys) -> None:
    # The invoices source resolves Invoice entities; the Job it references
    # has not been ingested, so the cross-source ref warns — never rejects.
    workspace = copy_example(tmp_path)
    snapshot = write_snapshot(
        tmp_path,
        "invoices.csv",
        "id,job,issued_at,error\nINV-1,101,2026-10-05,false\n",
    )

    assert (
        main(
            [
                "ingest",
                str(workspace),
                str(snapshot),
                "--source",
                "invoices",
                "--json",
            ]
        )
        == EXIT_OK
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "invoices"
    assert payload["accepted"] == 1
    assert payload["rejected"] == 0
    assert "INGEST.UNRESOLVED_REF" in [
        d["code"] for d in payload["diagnostics"]
    ]


def test_ingest_rejected_records_exit_1(tmp_path: Path, capsys) -> None:
    workspace = copy_example(tmp_path)
    snapshot = write_snapshot(
        tmp_path, "jobs.csv", "id,status,assignee\n,completed,taro\n"
    )

    assert (
        main(["ingest", str(workspace), str(snapshot), "--source", "jobs"])
        == EXIT_INVALID
    )
    out = capsys.readouterr().out
    assert "INGEST.MISSING_ID" in out
    assert "1 rejected" in out


def test_ingest_invalid_workspace_exit_1(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "nope"
    snapshot = write_snapshot(tmp_path, "jobs.csv", JOBS_CSV)

    assert main(["ingest", str(workspace), str(snapshot)]) == EXIT_INVALID
    assert "WORKSPACE.NOT_A_DIRECTORY" in capsys.readouterr().out


def test_ingest_missing_snapshot_exit_1(tmp_path: Path, capsys) -> None:
    workspace = copy_example(tmp_path)

    assert (
        main(
            [
                "ingest",
                str(workspace),
                str(tmp_path / "missing.csv"),
                "--source",
                "jobs",
            ]
        )
        == EXIT_INVALID
    )
    assert "INGEST.UNREADABLE_SOURCE" in capsys.readouterr().out


def test_ingest_writes_evidence_under_runtime_dir(tmp_path: Path, capsys) -> None:
    workspace = copy_example(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", JOBS_CSV)

    assert (
        main(["ingest", str(workspace), str(snapshot), "--source", "jobs"])
        == EXIT_OK
    )
    capsys.readouterr()

    runtime = workspace / ".ontology"
    assert runtime.is_dir()
    assert len(list((runtime / "snapshots").iterdir())) == 1
    assert len(list((runtime / "observations").iterdir())) == 1
    assert (runtime / "trace.jsonl").is_file()


def test_ingest_real_example_data(tmp_path: Path, capsys) -> None:
    workspace = copy_example(tmp_path)
    snapshot = EXAMPLE_WORKSPACE / "data" / "jobs.csv"

    assert (
        main(["ingest", str(workspace), str(snapshot), "--source", "jobs"])
        == EXIT_OK
    )
    out = capsys.readouterr().out
    assert "2 accepted" in out
