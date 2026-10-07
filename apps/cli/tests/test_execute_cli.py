"""CLI tests for the execute command (write-back through the CSV adapter)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import clisupport
from ontology_cli.main import main

CSV_SAMPLE = (
    "id,status,assignee,completed_at,invoiced_at\n"
    "101,completed,taro,2026-10-01,\n"
)


def setup_workspace(tmp_path: Path) -> Path:
    workspace = clisupport.copy_example(tmp_path)
    (tmp_path / "jobs.csv").write_text(CSV_SAMPLE, encoding="utf-8")
    assert (
        main(
            [
                "ingest",
                str(workspace),
                str(tmp_path / "jobs.csv"),
                "--source",
                "jobs",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "diagnose",
                str(workspace),
                "--as-of",
                "2026-10-05T00:00:00Z",
                "--provider",
                "stub",
            ]
        )
        == 0
    )
    return workspace


def proposal_id(workspace: Path, capsys) -> str:
    assert main(["proposals", str(workspace), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"]
    assert len(payload["proposals"]) == 1
    return payload["proposals"][0]["proposal_id"]


def approve(workspace: Path, pid: str) -> None:
    assert (
        main(
            [
                "decide",
                str(workspace),
                pid,
                "--approve",
                "--reviewer",
                "fde-sato",
            ]
        )
        == 0
    )


def reminders_file(workspace: Path) -> Path:
    return workspace / ".ontology" / "reminders.csv"


def test_execute_approved_writes_reminder(tmp_path, capsys) -> None:
    workspace = setup_workspace(tmp_path)
    capsys.readouterr()
    pid = proposal_id(workspace, capsys)
    approve(workspace, pid)
    capsys.readouterr()

    code = main(["execute", str(workspace), pid])
    out = capsys.readouterr().out
    assert code == 0
    assert "succeeded" in out
    assert pid in out
    assert "reminder rem-" in out

    path = reminders_file(workspace)
    assert path.exists()
    with path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["proposal_id"] == pid
    assert rows[0]["target"] == "taro"
    assert rows[0]["related_entity"] == "101"

    # The execution is inspectable via proposals --show.
    assert main(["proposals", str(workspace), "--show", pid]) == 0
    out = capsys.readouterr().out
    assert "execution:" in out
    assert "succeeded" in out
    assert "csv-reminders" in out


def test_execute_repeated_does_not_duplicate(tmp_path, capsys) -> None:
    workspace = setup_workspace(tmp_path)
    capsys.readouterr()
    pid = proposal_id(workspace, capsys)
    approve(workspace, pid)
    capsys.readouterr()

    assert main(["execute", str(workspace), pid]) == 0
    capsys.readouterr()
    code = main(["execute", str(workspace), pid, "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"]
    assert payload["status"] == "succeeded"
    assert payload["replayed"] is True
    assert payload["receipt"]["deduplicated"] is False
    assert payload["error"] is None

    with reminders_file(workspace).open(
        "r", newline="", encoding="utf-8"
    ) as f:
        assert len(list(csv.DictReader(f))) == 1


def test_execute_refuses_unapproved_and_unknown(tmp_path, capsys) -> None:
    workspace = setup_workspace(tmp_path)
    capsys.readouterr()
    pid = proposal_id(workspace, capsys)

    # Still proposed — never reaches the adapter.
    code = main(["execute", str(workspace), pid, "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert not payload["ok"]
    assert payload["status"] == "refused"
    assert payload["error"] is None
    assert {
        d["code"] for d in payload["diagnostics"]
    } == {"DECISION.NOT_APPROVED"}
    assert not reminders_file(workspace).exists()

    # Unknown proposal id.
    code = main(["execute", str(workspace), "prp-nope"])
    out = capsys.readouterr().out
    assert code == 1
    assert "EXECUTION.UNKNOWN_PROPOSAL" in out

    # Rejected after approval also refuses.
    approve(workspace, pid)
    assert (
        main(
            [
                "decide",
                str(workspace),
                pid,
                "--reject",
                "--reviewer",
                "fde-ueda",
            ]
        )
        == 0
    )
    capsys.readouterr()
    code = main(["execute", str(workspace), pid])
    out = capsys.readouterr().out
    assert code == 1
    assert "refused" in out
    assert not reminders_file(workspace).exists()
