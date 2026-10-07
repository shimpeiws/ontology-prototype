"""CLI tests: the app presents exactly what the core decides."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from clisupport import EXAMPLE_WORKSPACE, copy_example, rewrite

from ontology_cli.main import EXIT_INVALID, EXIT_OK, main


def test_check_valid_workspace(capsys) -> None:
    assert main(["check", str(EXAMPLE_WORKSPACE)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "workspace valid" in out


def test_check_invalid_workspace_exits_1(tmp_path: Path, capsys) -> None:
    workspace = copy_example(tmp_path)
    rewrite(workspace, "domain", lambda d: d["domain"]["entities"]["Job"]["properties"]["status"].update(type="bogus"))
    assert main(["check", str(workspace)]) == EXIT_INVALID
    out = capsys.readouterr().out
    assert "SCHEMA.INVALID_PROPERTY_TYPE" in out
    assert "workspace invalid" in out


def test_check_not_a_directory(tmp_path: Path, capsys) -> None:
    assert main(["check", str(tmp_path / "nope")]) == EXIT_INVALID
    assert "WORKSPACE.NOT_A_DIRECTORY" in capsys.readouterr().out


def test_json_output_matches_core(tmp_path: Path, capsys) -> None:
    from ontology_core import load_workspace

    workspace = copy_example(tmp_path)
    rewrite(
        workspace,
        "mapping",
        lambda d: d["mapping"]["sources"][0]["entities"][0].update(
            entity="Alien"
        ),
    )
    assert main(["check", str(workspace), "--json"]) == EXIT_INVALID
    payload = json.loads(capsys.readouterr().out)

    result = load_workspace(workspace)
    assert payload["valid"] == result.ok
    assert payload["diagnostics"] == [d.to_dict() for d in result.diagnostics]


def test_parity_core_and_cli(tmp_path: Path, capsys) -> None:
    """For the same fixtures the CLI verdict always equals the core verdict."""
    from ontology_core import load_workspace

    mutations = [
        ("domain", lambda d: d["domain"]["entities"]["Job"]["properties"]["assignee"].update(ref="Nobody")),
        ("evaluation", lambda d: d["evaluation"]["interval"].update(end_event="JobCompleted")),
        ("agent", lambda d: d["agent"].update(projection="Missing")),
    ]
    for index, (name, mutate) in enumerate(mutations):
        workspace = copy_example(tmp_path / f"case-{index}")
        rewrite(workspace, name, mutate)
        result = load_workspace(workspace)
        capsys.readouterr()
        exit_code = main(["check", str(workspace)])
        expected = EXIT_OK if result.ok else EXIT_INVALID
        assert exit_code == expected, name


def test_check_accepts_a_workspace_saved_via_core(tmp_path: Path, capsys) -> None:
    """Issue #19 round trip: a bundle activated by core persistence (the
    path the API's save endpoint drives) is exactly what `ontology check`
    loads — UI saves and CLI loads share one format and one validator."""
    from ontology_core import bundle_digest, save_workspace

    workspace = copy_example(tmp_path)
    texts = {
        f"{name}.yaml": (workspace / f"{name}.yaml").read_text(
            encoding="utf-8"
        )
        for name in ("domain", "mapping", "evaluation", "agent")
    }
    base = bundle_digest(texts)
    texts["domain.yaml"] += "\n# saved through the authoring UI\n"
    result = save_workspace(workspace, texts, base_digest=base)
    assert result.status == "saved"
    capsys.readouterr()
    assert main(["check", str(workspace)]) == EXIT_OK
    assert "workspace valid" in capsys.readouterr().out


def test_module_entry_point(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "ontology_cli", "check", str(EXAMPLE_WORKSPACE)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert completed.returncode == 0
    assert "workspace valid" in completed.stdout


def test_console_script_entry_point() -> None:
    script = Path(sys.executable).with_name("ontology")
    if not script.exists():
        pytest.skip("ontology console script not installed")
    completed = subprocess.run(
        [str(script), "check", str(EXAMPLE_WORKSPACE)],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "workspace valid" in completed.stdout


def test_version_flag(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "ontology-core" in capsys.readouterr().out
