"""Deterministic end-to-end checks for the second workflow (ticket-sla).

Mirrors test_loop_cli.py for v0.3 (#59): the committed
examples/ticket-sla workspace runs the complete loop — check, ingest,
declared-selection projection, stub-backed diagnosis, human decision,
write-back, re-observation, evaluation, and trace — through the same
core with definitions alone, asserting the documented reference outcome
(docs/ticket-sla-walkthrough.md) including the declared reopen-rate
guardrail, which needs no registered Python semantics.
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import clisupport
from ontology_cli.main import main

EXAMPLE = clisupport.REPO_ROOT / "examples" / "ticket-sla"
EXAMPLE_DATA = EXAMPLE / "data"

AS_OF = "2026-10-24T00:00:00Z"
BOUNDARY = "2026-10-20T00:00:00Z"
EVAL_AS_OF = "2026-10-30T00:00:00Z"


def copy_example(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    for name in ("domain", "mapping", "evaluation", "agent"):
        shutil.copy(EXAMPLE / f"{name}.yaml", workspace / f"{name}.yaml")
    return workspace


def copy_snapshots(tmp_path: Path) -> dict[str, Path]:
    snapshots = {
        name: shutil.copy(
            EXAMPLE_DATA / f"tickets-{name}.csv", tmp_path / f"tickets-{name}.csv"
        )
        for name in ("baseline", "current", "followup")
    }
    for name in ("baseline", "followup"):
        snapshots[f"resolutions-{name}"] = shutil.copy(
            EXAMPLE_DATA / f"resolutions-{name}.csv",
            tmp_path / f"resolutions-{name}.csv",
        )
    return snapshots


def run_json(capsys, args: list[str]) -> dict:
    code = main(args)
    out = capsys.readouterr().out
    assert code == 0, f"{args} exited {code}: {out}"
    return json.loads(out)


def proposal_id(workspace: Path, capsys) -> str:
    payload = run_json(capsys, ["proposals", str(workspace), "--json"])
    assert len(payload["proposals"]) == 1
    return payload["proposals"][0]["proposal_id"]


def test_ticket_sla_complete_loop(tmp_path, capsys) -> None:
    workspace = copy_example(tmp_path)
    snapshots = copy_snapshots(tmp_path)

    # The four definitions validate cleanly.
    assert main(["check", str(workspace)]) == 0
    capsys.readouterr()

    baseline = run_json(
        capsys,
        [
            "ingest",
            str(workspace),
            str(snapshots["baseline"]),
            "--source",
            "tickets",
            "--json",
        ],
    )
    assert baseline["accepted"] == 3
    assert baseline["rejected"] == 0
    assert baseline["canonical"]["events_appended"] == 9

    current = run_json(
        capsys,
        [
            "ingest",
            str(workspace),
            str(snapshots["current"]),
            "--source",
            "tickets",
            "--json",
        ],
    )
    assert current["accepted"] == 5
    assert current["rejected"] == 0
    assert current["canonical"]["events_appended"] == 9

    # The selection is declared on the projection itself (#55): T-C00 is
    # opened 16h without a first response (> 4h SLA); T-C03 is 3h old and
    # stays inside it.
    projection = run_json(
        capsys, ["project", str(workspace), "--as-of", AS_OF, "--json"]
    )
    assert projection["ok"] is True
    assert projection["recorded"] is True
    assert [c["subject"]["id"] for c in projection["candidates"]] == ["T-C00"]
    assert projection["candidates"][0]["elapsed_seconds"] == 16 * 3600
    inputs = {i["subject_id"]: i for i in projection["inputs"]}
    assert inputs["T-C03"]["excluded"] == "elapsed within threshold"
    assert inputs["T-C01"]["excluded"] == "interval end event exists"

    # Diagnosis proceeds past the projection stage with the stub provider.
    diagnosis = run_json(
        capsys,
        [
            "diagnose",
            str(workspace),
            "--as-of",
            AS_OF,
            "--provider",
            "stub",
            "--json",
        ],
    )
    assert diagnosis["ok"] is True
    assert diagnosis["candidate_count"] == 1
    pid = proposal_id(workspace, capsys)

    # Execution is refused before a human decision.
    assert main(["execute", str(workspace), pid]) != 0
    assert "refused" in capsys.readouterr().out

    # Explicit human approval, then write-back with a receipt. The stub
    # proposal fills SendReminder's target from the candidate's declared
    # assignee ref (Agent "akira") and related_entity from the subject.
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
    assert "succeeded" in capsys.readouterr().out

    reminders = workspace / ".ontology" / "reminders.csv"
    with reminders.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["proposal_id"] == pid
    assert rows[0]["target"] == "akira"
    assert rows[0]["related_entity"] == "T-C00"

    # Repeating the write-back replays the receipt without duplicating.
    assert main(["execute", str(workspace), pid]) == 0
    assert "already executed" in capsys.readouterr().out
    with reminders.open("r", newline="", encoding="utf-8") as f:
        assert len(list(csv.DictReader(f))) == 1

    followup = run_json(
        capsys,
        [
            "ingest",
            str(workspace),
            str(snapshots["followup"]),
            "--source",
            "tickets",
            "--json",
        ],
    )
    assert followup["accepted"] == 6
    assert followup["rejected"] == 0
    assert followup["canonical"]["events_appended"] == 7
    assert followup["canonical"]["events_reobserved"] == 9

    # Re-ingesting the same snapshot is idempotent: every row is
    # re-observed and no new events are appended.
    repeat = run_json(
        capsys,
        [
            "ingest",
            str(workspace),
            str(snapshots["followup"]),
            "--source",
            "tickets",
            "--json",
        ],
    )
    assert repeat["accepted"] == 6
    assert repeat["rejected"] == 0
    assert repeat["canonical"]["events_appended"] == 0
    assert repeat["canonical"]["events_reobserved"] == 16

    # The second source feeds the Resolution entities the declared
    # reopen_rate metric counts (#56, #57).
    for name in ("resolutions-baseline", "resolutions-followup"):
        resolutions = run_json(
            capsys,
            [
                "ingest",
                str(workspace),
                str(snapshots[name]),
                "--source",
                "resolutions",
                "--json",
            ],
        )
        assert resolutions["rejected"] == 0
    assert resolutions["canonical"]["entities_upserted"] == 5

    # Evaluation of the declared interval works end to end, and the
    # reopen-rate guardrail evaluates from its declared metric with no
    # registered semantics: baseline 1/3 reopens, after 0/5.
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
    assert report["cohorts"]["baseline"]["aggregate"] == 26.0
    assert report["cohorts"]["after"]["aggregate"] == 3.0
    assert report["change"] == {
        "status": "measured",
        "value": 23.0,
        "direction": "improvement",
    }
    assert report["target"] == {
        "comparator": "lt",
        "value": 4.0,
        "status": "attained",
    }
    guardrail = report["guardrails"][0]
    assert guardrail["metric"] == "reopen_rate"
    assert guardrail["status"] == "passed"
    evidence = guardrail["evidence"]
    assert evidence["convention"] == {
        "entity": "Resolution",
        "ticket_ref": "ticket",
        "reopened_flag": "reopened",
        "subject_entity": "Ticket",
    }
    assert evidence["baseline"]["resolutions"] == 3
    assert evidence["baseline"]["reopens"] == 1
    assert evidence["baseline"]["rate"] == 1 / 3
    assert evidence["after"] == {
        "resolutions": 5,
        "reopens": 0,
        "rate": 0.0,
        "resolutions_missing_reopened": 0,
        "postdating_as_of": 0,
        "entity_ids": ["R-C00", "R-C01", "R-C02", "R-C03", "R-C04"],
    }
    assert report["overall"]["status"] == "success"
    pending = {p["subject_id"] for p in report["cohorts"]["after"]["pending"]}
    assert pending == {"T-C05"}

    # The trace links every stage for one subject and exports cleanly.
    subject = run_json(
        capsys, ["trace", str(workspace), "--subject", "Ticket/T-C00", "--json"]
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


def test_ticket_sla_resolutions_source(tmp_path, capsys) -> None:
    """The second `resolutions` source ingests reopen evidence (#57).

    Resolution rows bind `Resolution` entities and assert TicketResolved
    subjects that resolve cross-source against canonical Tickets by
    exact stable id. The exports report the same resolved_at values as
    the tickets snapshots, so every event is re-observed, not appended.
    """
    workspace = copy_example(tmp_path)
    snapshots = copy_snapshots(tmp_path)

    for name in ("baseline", "followup"):
        run_json(
            capsys,
            [
                "ingest",
                str(workspace),
                str(snapshots[name]),
                "--source",
                "tickets",
                "--json",
            ],
        )

    baseline = run_json(
        capsys,
        [
            "ingest",
            str(workspace),
            str(snapshots["resolutions-baseline"]),
            "--source",
            "resolutions",
            "--json",
        ],
    )
    assert baseline["accepted"] == 3
    assert baseline["rejected"] == 0
    assert baseline["canonical"]["entities_upserted"] == 3
    assert baseline["canonical"]["events_appended"] == 0
    assert baseline["canonical"]["events_reobserved"] == 3
    assert not [
        d for d in baseline["diagnostics"] if "UNRESOLVED_REF" in d["code"]
    ]

    followup = run_json(
        capsys,
        [
            "ingest",
            str(workspace),
            str(snapshots["resolutions-followup"]),
            "--source",
            "resolutions",
            "--json",
        ],
    )
    assert followup["accepted"] == 5
    assert followup["rejected"] == 0
    assert followup["canonical"]["entities_upserted"] == 5
    assert followup["canonical"]["events_appended"] == 0
    assert followup["canonical"]["events_reobserved"] == 5

    # Re-ingesting an unchanged resolutions snapshot is idempotent.
    repeat = run_json(
        capsys,
        [
            "ingest",
            str(workspace),
            str(snapshots["resolutions-followup"]),
            "--source",
            "resolutions",
            "--json",
        ],
    )
    assert repeat["accepted"] == 5
    assert repeat["canonical"]["events_appended"] == 0
    assert repeat["canonical"]["events_reobserved"] == 5
