"""CLI tests for the proposals and decide commands."""

from __future__ import annotations

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
    assert main(["ingest", str(workspace), str(tmp_path / "jobs.csv")]) == 0
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


def proposal_id(tmp_path: Path, workspace: Path, capsys) -> str:
    assert main(["proposals", str(workspace), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"]
    assert len(payload["proposals"]) == 1
    return payload["proposals"][0]["proposal_id"]


def test_proposals_list_show_and_decide(tmp_path, capsys) -> None:
    workspace = setup_workspace(tmp_path)
    capsys.readouterr()

    pid = proposal_id(tmp_path, workspace, capsys)
    assert pid.endswith("-p0")

    # List shows status proposed.
    assert main(["proposals", str(workspace)]) == 0
    out = capsys.readouterr().out
    assert pid in out
    assert "proposed" in out
    assert "SendReminder" in out

    # Show detail includes payload, related entity, evidence.
    assert main(["proposals", str(workspace), "--show", pid]) == 0
    out = capsys.readouterr().out
    assert "digest: sha256:" in out
    assert "taro" in out
    assert "evidence.fact_key" in out

    # Approve.
    assert (
        main(
            [
                "decide",
                str(workspace),
                pid,
                "--approve",
                "--reviewer",
                "fde-sato",
                "--reason",
                "correct target",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "approved" in out

    # Status is now approved in list/show.
    assert main(["proposals", str(workspace), "--show", pid]) == 0
    out = capsys.readouterr().out
    assert "approved" in out
    assert "fde-sato" in out
    assert "correct target" in out


def test_decide_reject_and_json(tmp_path, capsys) -> None:
    workspace = setup_workspace(tmp_path)
    capsys.readouterr()
    pid = proposal_id(tmp_path, workspace, capsys)

    code = main(
        [
            "decide",
            str(workspace),
            pid,
            "--reject",
            "--reviewer",
            "fde-ueda",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"]
    assert payload["decision"] == "rejected"
    assert payload["recorded"]


def test_decide_unknown_and_repeated(tmp_path, capsys) -> None:
    workspace = setup_workspace(tmp_path)
    capsys.readouterr()
    pid = proposal_id(tmp_path, workspace, capsys)

    # Unknown proposal id fails.
    code = main(
        [
            "decide",
            str(workspace),
            "prp-nope",
            "--approve",
            "--reviewer",
            "fde-sato",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["diagnostics"][0]["code"] == "DECISION.UNKNOWN_PROPOSAL"

    # Approve then reject — the latest decision wins, history stays.
    for flag in ("--approve", "--reject"):
        assert (
            main(
                [
                    "decide",
                    str(workspace),
                    pid,
                    flag,
                    "--reviewer",
                    "fde-sato",
                ]
            )
            == 0
        )
    assert main(["proposals", str(workspace), "--show", pid]) == 0
    out = capsys.readouterr().out
    assert "rejected" in out
    assert out.count("decision:") == 2


def test_proposals_empty_workspace(tmp_path, capsys) -> None:
    workspace = clisupport.copy_example(tmp_path)
    code = main(["proposals", str(workspace)])
    out = capsys.readouterr().out
    assert code == 0
    assert "no persisted proposals" in out
