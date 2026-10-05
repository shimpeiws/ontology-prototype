"""CLI tests for the diagnose command and provider adapters."""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import clisupport
import pytest
from ontology_cli import providers
from ontology_cli.main import main

CSV_SAMPLE = (
    "id,status,assignee,completed_at,invoiced_at\n"
    "101,completed,taro,2026-10-01,\n"
)


def setup_workspace(tmp_path: Path) -> Path:
    workspace = clisupport.copy_example(tmp_path)
    (tmp_path / "jobs.csv").write_text(CSV_SAMPLE, encoding="utf-8")
    assert main(["ingest", str(workspace), str(tmp_path / "jobs.csv")]) == 0
    return workspace


def test_diagnose_stub_provider(tmp_path, capsys) -> None:
    workspace = setup_workspace(tmp_path)
    capsys.readouterr()

    code = main(
        [
            "diagnose",
            str(workspace),
            "--as-of",
            "2026-10-05T00:00:00Z",
            "--provider",
            "stub",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "stub/" in out
    assert "SendReminder" in out


def test_diagnose_json_and_inspect(tmp_path, capsys) -> None:
    workspace = setup_workspace(tmp_path)
    capsys.readouterr()

    code = main(
        [
            "diagnose",
            str(workspace),
            "--as-of",
            "2026-10-05T00:00:00Z",
            "--provider",
            "stub",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"]
    assert payload["provider"] == "stub"
    assert payload["candidate_count"] == 1
    assert payload["proposals"][0]["action"] == "SendReminder"
    assert payload["recorded"]

    run_id = payload["run_id"]
    code = main(["diagnose", str(workspace), "--inspect", run_id])
    out = capsys.readouterr().out
    assert code == 0
    assert run_id in out
    assert "input digest" in out

    code = main(
        ["diagnose", str(workspace), "--inspect", run_id, "--json"]
    )
    record = json.loads(capsys.readouterr().out)["run"]
    assert code == 0
    assert record["provider"] == "stub"
    assert record["prompt_version"] == "diagnosis.v1"
    assert record["proposals"][0]["params"]["related_entity"] == "101"


def test_diagnose_requires_candidate_source(tmp_path, capsys) -> None:
    workspace = setup_workspace(tmp_path)
    capsys.readouterr()
    code = main(
        ["diagnose", str(workspace), "--provider", "stub", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["diagnostics"][0]["code"] == "DIAGNOSIS.NO_INPUT"


def test_diagnose_inspect_conflicts_and_unknown(tmp_path, capsys) -> None:
    workspace = setup_workspace(tmp_path)
    code = main(
        [
            "diagnose",
            str(workspace),
            "--inspect",
            "dgx-x",
            "--as-of",
            "2026-10-05T00:00:00Z",
        ]
    )
    assert code == 1
    assert "CONFLICTING_ARGS" in capsys.readouterr().out

    code = main(["diagnose", str(workspace), "--inspect", "dgx-x", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["diagnostics"][0]["code"] == "DIAGNOSIS.UNKNOWN_RUN"


def test_diagnose_invalid_as_of(tmp_path, capsys) -> None:
    workspace = setup_workspace(tmp_path)
    code = main(
        [
            "diagnose",
            str(workspace),
            "--as-of",
            "2026-10-05",
            "--provider",
            "stub",
        ]
    )
    assert code == 1
    assert "INVALID_AS_OF" in capsys.readouterr().out


def test_no_input_reported_before_provider_resolution(
    tmp_path, capsys, monkeypatch
) -> None:
    """FIND-010: a missing candidate source is reported even when the
    provider could not be resolved (key unset)."""
    workspace = setup_workspace(tmp_path)
    capsys.readouterr()
    monkeypatch.delenv(providers.OPENROUTER_KEY_ENV, raising=False)
    code = main(
        ["diagnose", str(workspace), "--provider", "openrouter", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["diagnostics"][0]["code"] == "DIAGNOSIS.NO_INPUT"


def test_openrouter_without_key_is_a_diagnostic(
    tmp_path, capsys, monkeypatch
) -> None:
    workspace = setup_workspace(tmp_path)
    capsys.readouterr()
    monkeypatch.delenv(providers.OPENROUTER_KEY_ENV, raising=False)
    code = main(
        [
            "diagnose",
            str(workspace),
            "--as-of",
            "2026-10-05T00:00:00Z",
            "--provider",
            "openrouter",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["diagnostics"][0]["code"] == "DIAGNOSIS.PROVIDER_ERROR"
    assert providers.OPENROUTER_KEY_ENV in payload["diagnostics"][0]["message"]


def test_provider_error_never_leaks_api_key(tmp_path, monkeypatch) -> None:
    secret = "sk-or-test-secret-abc123"

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            url=providers.OPENROUTER_URL,
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error": {"message": "bad key %s"}}' % secret.encode()),
        )

    monkeypatch.setattr(
        providers.urllib.request, "urlopen", fake_urlopen
    )
    provider = providers.OpenRouterProvider(api_key=secret)
    from ontology_core import ProviderRequest

    with pytest.raises(Exception) as excinfo:
        provider.complete(ProviderRequest(prompt="p", model="m"))
    assert secret not in str(excinfo.value)
    assert "401" in str(excinfo.value)
