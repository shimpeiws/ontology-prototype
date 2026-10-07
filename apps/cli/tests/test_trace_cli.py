"""CLI tests for `ontology trace` — inspection and export of the
linked run trace."""

from __future__ import annotations

import json
from pathlib import Path

import clisupport
from ontology_cli.main import EXIT_INVALID, EXIT_OK, main

CSV_SAMPLE = (
    "id,status,assignee,completed_at,invoiced_at\n"
    "101,completed,taro,2026-10-01,\n"
)

AS_OF = "2026-10-05T00:00:00Z"


def write_snapshot(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def full_loop(tmp_path: Path, capsys) -> tuple[Path, str]:
    """ingest -> diagnose (stub) -> approve -> execute -> evaluate."""
    workspace = clisupport.copy_example(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)
    assert (
        main(["ingest", str(workspace), str(snapshot), "--source", "jobs"])
        == EXIT_OK
    )
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
        == EXIT_OK
    )
    capsys.readouterr()  # drop non-JSON output from ingest/diagnose
    assert main(["proposals", str(workspace), "--json"]) == EXIT_OK
    pid = json.loads(capsys.readouterr().out)["proposals"][0]["proposal_id"]
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
        == EXIT_OK
    )
    assert main(["execute", str(workspace), pid]) == EXIT_OK
    assert (
        main(
            [
                "evaluate",
                str(workspace),
                "--boundary",
                "2026-10-05T00:00:00Z",
                "--as-of",
                "2026-10-15T00:00:00Z",
            ]
        )
        == EXIT_OK
    )
    capsys.readouterr()
    return workspace, pid


def test_trace_overview_counts_stages(tmp_path, capsys) -> None:
    workspace, _ = full_loop(tmp_path, capsys)

    assert main(["trace", str(workspace)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "journal:" in out
    assert "evidence:" in out
    assert "proposals: 1" in out
    assert "approved" in out
    assert "succeeded" in out
    assert "unresolved" in out


def test_trace_overview_json(tmp_path, capsys) -> None:
    workspace, pid = full_loop(tmp_path, capsys)

    assert main(["trace", str(workspace), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["proposals"]["by_status"] == {"approved": 1}
    assert payload["links"]["unresolved"] == 0
    stages = {
        e["stage"] for e in payload.get("journal_events", [])
    } or payload["journal"]["events"]
    assert stages  # events were emitted across stages


def test_trace_run_anchor(tmp_path, capsys) -> None:
    workspace, pid = full_loop(tmp_path, capsys)

    assert main(["trace", str(workspace), "--run", pid]) == EXIT_OK
    out = capsys.readouterr().out
    assert f"proposal {pid}" in out
    assert "approved" in out
    assert "links:" in out


def test_trace_run_anchor_json(tmp_path, capsys) -> None:
    workspace, pid = full_loop(tmp_path, capsys)

    assert (
        main(["trace", str(workspace), "--run", pid, "--json"]) == EXIT_OK
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    trace = payload["trace"]
    assert trace["kind"] == "proposal"
    assert trace["nature"] == "ai"
    assert trace["record"]["status"] == "approved"
    assert trace["record"]["executions"]
    assert trace["links"]


def test_trace_subject_chain(tmp_path, capsys) -> None:
    workspace, _ = full_loop(tmp_path, capsys)

    assert (
        main(["trace", str(workspace), "--subject", "Job/101"]) == EXIT_OK
    )
    out = capsys.readouterr().out
    assert "trace Job/101:" in out
    assert "[observed]" in out
    assert "[ai]" in out
    assert "[human]" in out
    assert "[writeback]" in out
    assert "[evaluated]" in out


def test_trace_unknown_id_is_a_diagnostic(tmp_path, capsys) -> None:
    workspace, _ = full_loop(tmp_path, capsys)

    assert (
        main(["trace", str(workspace), "--run", "dgx-nope"])
        == EXIT_INVALID
    )
    out = capsys.readouterr().out
    assert "TRACE.UNKNOWN_ID" in out

    assert (
        main(
            ["trace", str(workspace), "--run", "dgx-nope", "--json"]
        )
        == EXIT_INVALID
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "TRACE.UNKNOWN_ID"


def test_trace_conflicting_args(tmp_path, capsys) -> None:
    workspace, _ = full_loop(tmp_path, capsys)

    assert (
        main(
            [
                "trace",
                str(workspace),
                "--run",
                "dgx-x",
                "--subject",
                "Job/101",
            ]
        )
        == EXIT_INVALID
    )
    assert "TRACE.CONFLICTING_ARGS" in capsys.readouterr().out

    assert (
        main(
            [
                "trace",
                str(workspace),
                "--export",
                str(tmp_path / "out.json"),
                "--run",
                "dgx-x",
            ]
        )
        == EXIT_INVALID
    )
    assert "TRACE.CONFLICTING_ARGS" in capsys.readouterr().out


def test_trace_invalid_subject_arg(tmp_path, capsys) -> None:
    workspace, _ = full_loop(tmp_path, capsys)

    assert (
        main(["trace", str(workspace), "--subject", "101"])
        == EXIT_INVALID
    )
    assert "TRACE.INVALID_SUBJECT" in capsys.readouterr().out


def test_trace_is_read_only_on_fresh_workspace(tmp_path, capsys) -> None:
    # A never-ingested workspace has no runtime data; inspection must
    # not create canon.db just to report an unknown run.
    workspace = clisupport.copy_example(tmp_path)

    assert (
        main(["trace", str(workspace), "--run", "dgx-nope"])
        == EXIT_INVALID
    )
    assert "TRACE.UNKNOWN_ID" in capsys.readouterr().out
    assert not (workspace / ".ontology" / "canon.db").exists()

    assert main(["trace", str(workspace)]) == EXIT_OK
    capsys.readouterr()
    assert not (workspace / ".ontology" / "canon.db").exists()


def test_trace_export_writes_bundle(tmp_path, capsys) -> None:
    workspace, pid = full_loop(tmp_path, capsys)
    target = tmp_path / "trace-export.json"

    assert (
        main(["trace", str(workspace), "--export", str(target)])
        == EXIT_OK
    )
    out = capsys.readouterr().out
    assert "trace exported" in out

    bundle = json.loads(target.read_text(encoding="utf-8"))
    assert bundle["format"] == "ontology-trace"
    assert bundle["format_version"] == 1
    assert bundle["proposals"][0]["proposal_id"] == pid
    assert bundle["snapshots"]
    assert bundle["journal"]
    assert bundle["links"]

    # A second export to the same path is refused, not overwritten.
    assert (
        main(["trace", str(workspace), "--export", str(target)])
        == EXIT_INVALID
    )
    assert "TRACE.EXPORT_EXISTS" in capsys.readouterr().out


def test_trace_export_json_mode(tmp_path, capsys) -> None:
    workspace, _ = full_loop(tmp_path, capsys)
    target = tmp_path / "trace.json"

    assert (
        main(
            [
                "trace",
                str(workspace),
                "--export",
                str(target),
                "--json",
            ]
        )
        == EXIT_OK
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["export"] == str(target)
    assert payload["artifacts"]["journal_events"] > 0


def test_trace_survives_broken_definitions(tmp_path, capsys) -> None:
    # History inspection does not load the workspace definitions — a
    # later edit that breaks them cannot hide the recorded run.
    workspace, pid = full_loop(tmp_path, capsys)
    (workspace / "domain.yaml").write_text("not: [valid\n")

    assert main(["trace", str(workspace), "--run", pid]) == EXIT_OK
    assert "approved" in capsys.readouterr().out
