"""Deterministic checks for the second workflow workspace (ticket-sla).

Pins the documented state of examples/ticket-sla (#54): the four
definitions validate, the committed snapshots ingest cleanly, and
evaluation reports the recorded reference outcome — while the projection
and diagnosis stages stay blocked on the code-registered projection
semantics (#55) and the reopen-rate guardrail reports unavailable (#56).
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


def test_ticket_sla_check_ingest_evaluate(tmp_path, capsys) -> None:
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

    # The projection name has no registered semantics: project and the
    # diagnosis that depends on it both refuse (documented gap, #55).
    assert main(["project", str(workspace), "--as-of", AS_OF]) != 0
    assert "UNSUPPORTED_PROJECTION" in capsys.readouterr().out
    assert (
        main(
            [
                "diagnose",
                str(workspace),
                "--as-of",
                AS_OF,
                "--provider",
                "stub",
            ]
        )
        != 0
    )
    assert "UNSUPPORTED_PROJECTION" in capsys.readouterr().out

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
