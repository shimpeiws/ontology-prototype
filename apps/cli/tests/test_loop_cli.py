"""Deterministic end-to-end test for the complete v0.1 operational loop.

Drives the committed examples/job-invoicing demo snapshots through every
stage — ingest, projection, stub-backed diagnosis, human decision,
write-back, re-observation, evaluation, and trace — asserting the
documented reference outcome (docs/walkthrough.md).
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import clisupport
from ontology_cli.main import main

EXAMPLE_DATA = clisupport.REPO_ROOT / "examples" / "job-invoicing" / "data"

AS_OF = "2026-10-21T00:00:00Z"
BOUNDARY = "2026-10-15T00:00:00Z"
EVAL_AS_OF = "2026-10-26T00:00:00Z"


def run_json(capsys, args: list[str]) -> dict:
    code = main(args)
    out = capsys.readouterr().out
    assert code == 0, f"{args} exited {code}: {out}"
    return json.loads(out)


def copy_demo_snapshots(tmp_path: Path) -> dict[str, Path]:
    snapshots = {
        name: shutil.copy(
            EXAMPLE_DATA / f"jobs-{name}.csv", tmp_path / f"jobs-{name}.csv"
        )
        for name in ("baseline", "current", "followup")
    }
    snapshots.update(
        {
            f"invoices-{name}": shutil.copy(
                EXAMPLE_DATA / f"invoices-{name}.csv",
                tmp_path / f"invoices-{name}.csv",
            )
            for name in ("baseline", "followup")
        }
    )
    return snapshots


def proposal_id(workspace: Path, capsys) -> str:
    payload = run_json(capsys, ["proposals", str(workspace), "--json"])
    assert len(payload["proposals"]) == 1
    return payload["proposals"][0]["proposal_id"]


def test_complete_loop_stub_provider(tmp_path, capsys) -> None:
    workspace = clisupport.copy_example(tmp_path)
    snapshots = copy_demo_snapshots(tmp_path)

    # Stage 1: workspace definitions validate cleanly.
    assert main(["check", str(workspace)]) == 0
    capsys.readouterr()

    # Stage 2: baseline and current snapshots ingest without rejection.
    baseline = run_json(
        capsys,
        [
            "ingest",
            str(workspace),
            str(snapshots["baseline"]),
            "--source",
            "jobs",
            "--json",
        ],
    )
    assert baseline["accepted"] == 3
    assert baseline["canonical"]["events_appended"] == 6

    current = run_json(
        capsys,
        [
            "ingest",
            str(workspace),
            str(snapshots["current"]),
            "--source",
            "jobs",
            "--json",
        ],
    )
    assert current["accepted"] == 4
    assert current["canonical"]["events_appended"] == 3

    # Stage 3: projection selects only jobs completed >2 days before as-of.
    projection = run_json(
        capsys, ["project", str(workspace), "--as-of", AS_OF, "--json"]
    )
    candidates = {c["subject"]["id"] for c in projection["candidates"]}
    assert candidates == {"C01", "C02"}  # C03 is exactly 2 days old

    # Stage 4: the stub model double produces a validated, persisted proposal.
    diagnosis = run_json(
        capsys,
        ["diagnose", str(workspace), "--as-of", AS_OF, "--provider", "stub", "--json"],
    )
    assert diagnosis["provider"] == "stub"
    assert diagnosis["candidate_count"] == 2
    assert diagnosis["deliverables"]["explanation"]
    assert diagnosis["deliverables"]["hypotheses"]
    pid = proposal_id(workspace, capsys)

    # Stage 5: execution is refused before a human decision.
    assert main(["execute", str(workspace), pid]) != 0
    refused = capsys.readouterr().out
    assert "refused" in refused

    # Stage 6: explicit human approval, then write-back with a receipt.
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
                "demo approval",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["execute", str(workspace), pid]) == 0
    out = capsys.readouterr().out
    assert "succeeded" in out

    reminders = workspace / ".ontology" / "reminders.csv"
    with reminders.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["proposal_id"] == pid
    assert rows[0]["target"] == "taro"
    assert rows[0]["related_entity"] == "C01"

    # Repeating the write-back does not duplicate the reminder row.
    assert main(["execute", str(workspace), pid]) == 0
    assert "already executed" in capsys.readouterr().out
    with reminders.open("r", newline="", encoding="utf-8") as f:
        assert len(list(csv.DictReader(f))) == 1

    # Re-ingesting an identical snapshot appends no duplicate events.
    reingest = run_json(
        capsys,
        [
            "ingest",
            str(workspace),
            str(snapshots["current"]),
            "--source",
            "jobs",
            "--json",
        ],
    )
    assert reingest["canonical"]["events_appended"] == 0
    assert reingest["canonical"]["events_reobserved"] == 3

    # Stage 7: the follow-up snapshot records the invoiced outcomes.
    followup = run_json(
        capsys,
        [
            "ingest",
            str(workspace),
            str(snapshots["followup"]),
            "--source",
            "jobs",
            "--json",
        ],
    )
    assert followup["accepted"] == 5
    assert followup["canonical"]["events_appended"] == 4

    # Stage 7b: the second source feeds Invoice entities (with the
    # invoice-error flag) into the same canonical model. InvoiceIssued
    # events that jobs snapshots already recorded are re-observed.
    for name in ("invoices-baseline", "invoices-followup"):
        invoices = run_json(
            capsys,
            [
                "ingest",
                str(workspace),
                str(snapshots[name]),
                "--source",
                "invoices",
                "--json",
            ],
        )
        assert invoices["accepted"] == 3
        assert invoices["rejected"] == 0
    assert invoices["canonical"]["events_appended"] == 0
    assert invoices["canonical"]["events_reobserved"] == 3

    # Re-ingesting an unchanged snapshot from the second source also
    # appends nothing.
    invoice_repeat = run_json(
        capsys,
        [
            "ingest",
            str(workspace),
            str(snapshots["invoices-followup"]),
            "--source",
            "invoices",
            "--json",
        ],
    )
    assert invoice_repeat["canonical"]["events_appended"] == 0
    assert invoice_repeat["canonical"]["events_reobserved"] == 3

    # Stage 8: evaluation reports the documented reference outcome.
    evaluation = run_json(
        capsys,
        [
            "evaluate",
            str(workspace),
            "--boundary",
            BOUNDARY,
            "--as-of",
            EVAL_AS_OF,
            "--json",
        ],
    )
    report = evaluation["report"]
    assert report["cohorts"]["baseline"]["aggregate"] == 9.0
    assert report["cohorts"]["after"]["aggregate"] == 3.0
    assert report["change"] == {
        "status": "measured",
        "value": 6.0,
        "direction": "improvement",
    }
    assert report["target"] == {
        "comparator": "lt",
        "value": 4.0,
        "status": "attained",
    }
    # The invoices source supplies invoice-error evidence: the guardrail
    # is evaluated and passes with no errored invoices.
    assert report["guardrails"][0]["status"] == "passed"
    assert report["overall"]["status"] == "success"
    # C05 completed after the follow-up snapshot and stays pending.
    pending = {p["subject_id"] for p in report["cohorts"]["after"]["pending"]}
    assert pending == {"C05"}

    # Stage 9: the trace links every stage for one subject and exports cleanly.
    subject = run_json(
        capsys, ["trace", str(workspace), "--subject", "Job/C01", "--json"]
    )["trace"]
    assert subject["observations"]
    assert subject["facts"]
    assert subject["proposals"][0]["proposal_id"] == pid
    assert subject["decisions"]
    assert subject["executions"]
    assert subject["evaluations"]
    assert subject["unresolved"] == []

    export = tmp_path / "trace-export.json"
    assert main(["trace", str(workspace), "--export", str(export)]) == 0
    capsys.readouterr()
    bundle = json.loads(export.read_text(encoding="utf-8"))
    assert bundle["format"] == "ontology-trace"
    assert bundle["unresolved"] == []
    assert len(bundle["proposals"]) >= 1
    assert len(bundle["evaluation_runs"]) >= 1


def test_loop_rejected_proposal_never_executes(tmp_path, capsys) -> None:
    workspace = clisupport.copy_example(tmp_path)
    snapshots = copy_demo_snapshots(tmp_path)
    for name in ("baseline", "current"):
        assert (
            main(
                [
                    "ingest",
                    str(workspace),
                    str(snapshots[name]),
                    "--source",
                    "jobs",
                ]
            )
            == 0
        )
    capsys.readouterr()
    run_json(
        capsys,
        ["diagnose", str(workspace), "--as-of", AS_OF, "--provider", "stub", "--json"],
    )
    pid = proposal_id(workspace, capsys)

    assert (
        main(
            ["decide", str(workspace), pid, "--reject", "--reviewer", "fde-sato"]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["execute", str(workspace), pid]) != 0
    assert "refused" in capsys.readouterr().out
    assert not (workspace / ".ontology" / "reminders.csv").exists()


def test_loop_median_exactly_at_target_is_a_miss(tmp_path, capsys) -> None:
    """A 4-day after median does not meet the strict lt-4 target."""
    workspace = clisupport.copy_example(tmp_path)
    snapshots = copy_demo_snapshots(tmp_path)
    # After durations become 3, 4, 5 -> median exactly 4.
    followup = tmp_path / "jobs-followup-late.csv"
    followup.write_text(
        "id,status,assignee,completed_at,invoiced_at\n"
        "C01,completed,taro,2026-10-17,2026-10-20\n"
        "C02,completed,taro,2026-10-18,2026-10-22\n"
        "C03,completed,hanako,2026-10-19,2026-10-24\n",
        encoding="utf-8",
    )
    for name in ("baseline", "current"):
        assert (
            main(
                [
                    "ingest",
                    str(workspace),
                    str(snapshots[name]),
                    "--source",
                    "jobs",
                ]
            )
            == 0
        )
    assert (
        main(["ingest", str(workspace), str(followup), "--source", "jobs"])
        == 0
    )
    capsys.readouterr()

    report = run_json(
        capsys,
        [
            "evaluate",
            str(workspace),
            "--boundary",
            BOUNDARY,
            "--as-of",
            EVAL_AS_OF,
            "--json",
        ],
    )["report"]
    assert report["cohorts"]["after"]["aggregate"] == 4.0
    assert report["target"]["status"] == "missed"
    assert report["overall"]["status"] == "not_met"
