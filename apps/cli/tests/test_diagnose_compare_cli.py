"""CLI tests for the diagnose-compare command (issue #35).

The comparison entry point is exercised end to end with the offline
``stub`` double — no credentials, no network. An ``openrouter`` subject
whose key is unset proves the unavailable-candidate slot through the
same real path a live run takes.
"""

from __future__ import annotations

import json
from pathlib import Path

from ontology_cli import providers
from ontology_cli.main import main


def test_compare_command_through_cli(tmp_path: Path, capsys, monkeypatch) -> None:
    """INV-001/INV-002/INV-007: the real entry point runs stub subjects
    against one shared input, writes the artifact, and records the
    credential-less openrouter subject as unavailable."""
    monkeypatch.delenv(providers.OPENROUTER_KEY_ENV, raising=False)
    scratch = tmp_path / "scratch"
    out = tmp_path / "comparison-report.json"

    code = main(
        [
            "diagnose-compare",
            str(scratch),
            "--subject",
            "stub:eval/stub-a",
            "--subject",
            "stub:eval/stub-b",
            "--subject",
            "openrouter:openai/gpt-4o-mini",
            "--out",
            str(out),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["report_path"] == str(out)
    assert payload["input_identical"] is True
    assert payload["input_digest"]
    assert len(payload["subjects"]) == 2
    assert all(s["status"] == "ok" for s in payload["subjects"])
    # Every evaluated subject records the identical shared input.
    assert {s["input_digest"] for s in payload["subjects"]} == {
        payload["input_digest"]
    }
    assert {s["projection_run_id"] for s in payload["subjects"]} == {
        payload["projection_run_id"]
    }
    # The credential-less subject is recorded explicitly, not dropped.
    assert [u["provider"] for u in payload["unavailable"]] == ["openrouter"]
    assert (
        providers.OPENROUTER_KEY_ENV in payload["unavailable"][0]["reason"]
    )

    artifact = json.loads(out.read_text())
    assert artifact["input_identical"] is True
    assert "report_path" not in artifact  # the artifact is path-independent


def test_compare_all_unavailable_still_writes_artifact(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """Even with nothing evaluated, the artifact records every spec as
    unavailable — the run is invalid (exit 1) but never silent."""
    monkeypatch.delenv(providers.OPENROUTER_KEY_ENV, raising=False)
    out = tmp_path / "report.json"

    code = main(
        [
            "diagnose-compare",
            str(tmp_path / "scratch"),
            "--subject",
            "openrouter:openai/gpt-4o-mini",
            "--out",
            str(out),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["subjects"] == []
    assert len(payload["unavailable"]) == 1
    assert json.loads(out.read_text())["unavailable"]


def test_subject_split_on_first_colon(tmp_path: Path, capsys) -> None:
    """INV-004: model ids may carry ':' suffixes; only the first colon
    separates provider from model."""
    code = main(
        [
            "diagnose-compare",
            str(tmp_path / "scratch"),
            "--subject",
            "stub:eval/model:free",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["subjects"][0]["model"] == "eval/model:free"


def test_compare_human_output(tmp_path: Path, capsys) -> None:
    """The non-JSON output names the artifact and the audit workspace."""
    out = tmp_path / "report.json"
    code = main(
        [
            "diagnose-compare",
            str(tmp_path / "scratch"),
            "--subject",
            "stub:eval/a",
            "--out",
            str(out),
        ]
    )
    text = capsys.readouterr().out
    assert code == 0
    assert str(out) in text
    assert ".ontology store" in text
    assert "shared input" in text
    assert "[ok] stub/eval/a" in text


def test_invalid_subject_specs_rejected(tmp_path: Path, capsys) -> None:
    """INV-004: malformed --subject values are rejected before any run."""
    for raw in ("nomodelsep", ":missing-provider", "missing-model:"):
        code = main(
            [
                "diagnose-compare",
                str(tmp_path / "scratch"),
                "--subject",
                raw,
                "--json",
            ]
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == 1
        assert (
            payload["diagnostics"][0]["code"] == "DIAGNOSIS.INVALID_SUBJECT"
        )


def test_invalid_budget_rejected(tmp_path: Path, capsys) -> None:
    code = main(
        [
            "diagnose-compare",
            str(tmp_path / "scratch"),
            "--subject",
            "stub:eval/a",
            "--repeats",
            "0",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["diagnostics"][0]["code"] == "DIAGNOSIS.INVALID_BUDGET"
