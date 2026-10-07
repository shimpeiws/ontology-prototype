"""End-to-end authoring loop: API drafts -> save -> CLI operational loop.

Issue #20 acceptance: the FDE can complete the job-invoicing authoring flow
through the API (open, edit all four definitions, validate, preview, save)
and the saved YAML drives the v0.1 operational loop without hand-editing.
"""

from __future__ import annotations

import json
from pathlib import Path

import apisupport
import yaml
from fastapi.testclient import TestClient
from ontology_api import ApiConfig, create_app
from ontology_cli.main import main as cli_main

DEMO_DATA = apisupport.REPO_ROOT / "examples" / "job-invoicing" / "data"

SAMPLE = (
    "id,status,assignee,completed_at,invoiced_at\n"
    "C01,completed,taro,2026-10-17,\n"
)


def make_client(*roots: Path) -> TestClient:
    return TestClient(create_app(ApiConfig(workspace_roots=tuple(roots))))


def apply_ui_edits(files: dict[str, str]) -> dict[str, str]:
    """Edits the way the structured form panels produce them: CST-preserving
    YAML mutations over the four definitions."""

    def mutate(name: str, fn) -> None:
        doc = yaml.safe_load(files[name])
        fn(doc)
        files[name] = yaml.safe_dump(doc, sort_keys=False)

    mutate("domain.yaml", lambda d: d["domain"]["entities"]["Job"]["properties"].update(
        {"notes": {"type": "string", "required": False}}
    ))
    mutate("mapping.yaml", lambda d: d["mapping"].update(
        description="Maps jobs.csv rows to canonical entities and business events (edited in UI)."
    ))
    mutate("evaluation.yaml", lambda d: d["evaluation"]["target"].update(
        description="Strict target; a median of exactly 4 days is a miss. (edited in UI)"
    ))
    mutate("agent.yaml", lambda d: d["agent"]["task"].update(
        goal="Identify likely causes of delayed invoicing and suggest an intervention. (edited in UI)"
    ))
    return files


def cli(capsys, *args: str) -> str:
    code = cli_main(list(args))
    out = capsys.readouterr().out
    assert code == 0, f"ontology {' '.join(args)} exited {code}: {out}"
    return out


def test_saved_bundle_runs_operational_loop(tmp_path, capsys, roots, workspace) -> None:
    client = make_client(roots)

    # Open the workspace — the web app's entry point.
    opened = client.post(
        "/api/v1/workspaces/open", json={"path": str(workspace)}
    ).json()
    assert opened["valid"] is True
    base_digest = opened["workspace"]["digest"]

    # The FDE edits all four definitions through the structured forms and
    # validates the draft bundle without touching the active files.
    draft = apply_ui_edits(apisupport.workspace_texts(workspace))
    validated = client.post(
        "/api/v1/workspaces/drafts/validate",
        json={"path": str(workspace), "files": draft},
    ).json()
    assert validated["valid"] is True, validated["diagnostics"]

    # Mapping preview runs the draft bundle against an in-memory sample.
    preview = client.post(
        "/api/v1/workspaces/drafts/preview",
        json={
            "path": str(workspace),
            "files": draft,
            "sample": {"text": SAMPLE},
        },
    ).json()
    assert preview["valid"] is True
    obs = preview["preview"]["observations"][0]
    assert obs["status"] == "accepted"
    assert {e["entity"] for e in obs["entities"]} == {"Job", "Person"}

    # Save activates atomically; the four files on disk are the sent bytes.
    saved = client.post(
        "/api/v1/workspaces/save",
        json={
            "path": str(workspace),
            "base_digest": base_digest,
            "files": draft,
        },
    ).json()
    assert saved["outcome"] == "saved", saved["diagnostics"]
    assert (workspace / "domain.yaml").read_text() == draft["domain.yaml"]

    # A stale digest cannot overwrite: concurrent edits report conflict.
    stale = client.post(
        "/api/v1/workspaces/save",
        json={
            "path": str(workspace),
            "base_digest": base_digest,
            "files": draft,
        },
    ).json()
    assert stale["outcome"] == "conflict"

    # The saved definitions drive the CLI operational loop unchanged.
    cli(capsys, "check", str(workspace))
    cli(capsys, "ingest", str(workspace), str(DEMO_DATA / "jobs-baseline.csv"))
    cli(capsys, "ingest", str(workspace), str(DEMO_DATA / "jobs-current.csv"))
    project = json.loads(
        cli(capsys, "project", str(workspace), "--as-of", "2026-10-21T00:00:00Z", "--json")
    )
    assert {c["subject"]["id"] for c in project["candidates"]} == {"C01", "C02"}

    diagnosis = json.loads(
        cli(
            capsys,
            "diagnose", str(workspace), "--as-of", "2026-10-21T00:00:00Z",
            "--provider", "stub", "--json",
        )
    )
    assert diagnosis["candidate_count"] == 2
    pid = json.loads(
        cli(capsys, "proposals", str(workspace), "--json")
    )["proposals"][0]["proposal_id"]

    # Human approval gate holds inside the operational loop, not the UI.
    cli(
        capsys, "decide", str(workspace), pid, "--approve",
        "--reviewer", "fde-sato",
    )
    cli(capsys, "execute", str(workspace), pid)

    cli(capsys, "ingest", str(workspace), str(DEMO_DATA / "jobs-followup.csv"))
    report = json.loads(
        cli(
            capsys, "evaluate", str(workspace),
            "--boundary", "2026-10-15T00:00:00Z",
            "--as-of", "2026-10-26T00:00:00Z",
            "--json",
        )
    )["report"]
    assert report["cohorts"]["baseline"]["aggregate"] == 9.0
    assert report["cohorts"]["after"]["aggregate"] == 3.0
    assert report["target"]["status"] == "attained"


def test_contract_exposes_no_action_authority(roots) -> None:
    """The authoring API grants no approval or write-back capability."""
    body = make_client(roots).get("/api/v1/contract").json()
    operations = set(body["operations"])
    assert operations == {"open", "validate_draft", "save", "preview_draft"}
    forbidden = {"decide", "execute", "diagnose", "proposals", "approve"}
    assert not (operations & forbidden)
