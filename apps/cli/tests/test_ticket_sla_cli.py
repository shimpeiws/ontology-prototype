"""Deterministic checks for the second workflow workspace (ticket-sla).

Pins the documented state of examples/ticket-sla (#54, #55): the four
definitions validate, the committed snapshots ingest cleanly, the
declaratively-selected projection and the stub diagnosis run, and
evaluation reports the recorded reference outcome — while the
reopen-rate guardrail reports unavailable (#56).
"""

from __future__ import annotations

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
    return {
        name: shutil.copy(
            EXAMPLE_DATA / f"tickets-{name}.csv", tmp_path / f"tickets-{name}.csv"
        )
        for name in ("baseline", "current", "followup")
    }


def run_json(capsys, args: list[str]) -> dict:
    code = main(args)
    out = capsys.readouterr().out
    assert code == 0, f"{args} exited {code}: {out}"
    return json.loads(out)


def test_ticket_sla_check_ingest_evaluate(tmp_path, capsys) -> None:
    workspace = copy_example(tmp_path)
    snapshots = copy_snapshots(tmp_path)

    # The four definitions validate cleanly.
    assert main(["check", str(workspace)]) == 0
    capsys.readouterr()

    baseline = run_json(
        capsys, ["ingest", str(workspace), str(snapshots["baseline"]), "--json"]
    )
    assert baseline["accepted"] == 3
    assert baseline["rejected"] == 0
    assert baseline["canonical"]["events_appended"] == 9

    current = run_json(
        capsys, ["ingest", str(workspace), str(snapshots["current"]), "--json"]
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

    followup = run_json(
        capsys, ["ingest", str(workspace), str(snapshots["followup"]), "--json"]
    )
    assert followup["accepted"] == 6
    assert followup["rejected"] == 0
    assert followup["canonical"]["events_appended"] == 7
    assert followup["canonical"]["events_reobserved"] == 9

    # Re-ingesting the same snapshot is idempotent: every row is
    # re-observed and no new events are appended.
    repeat = run_json(
        capsys, ["ingest", str(workspace), str(snapshots["followup"]), "--json"]
    )
    assert repeat["accepted"] == 6
    assert repeat["rejected"] == 0
    assert repeat["canonical"]["events_appended"] == 0
    assert repeat["canonical"]["events_reobserved"] == 16

    # Evaluation of the declared interval works end to end; only the
    # unregistered reopen_rate guardrail stays unavailable (#56).
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
    assert report["guardrails"][0]["metric"] == "reopen_rate"
    assert report["guardrails"][0]["status"] == "unavailable"
    assert report["overall"]["status"] == "unverified"
    pending = {p["subject_id"] for p in report["cohorts"]["after"]["pending"]}
    assert pending == {"T-C05"}
