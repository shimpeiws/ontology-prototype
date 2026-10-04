"""Agent task definition validation."""

from __future__ import annotations

from pathlib import Path

from support import base_docs, codes, make_workspace

from ontology_core import load_workspace


def test_unknown_projection_with_hint(tmp_path: Path) -> None:
    docs = base_docs()
    docs["agent"]["agent"]["projection"] = "CompletedButNotInvoice"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    diag = next(d for d in result.diagnostics if d.code == "AGENT.UNKNOWN_PROJECTION")
    assert diag.hint == "did you mean 'CompletedButNotInvoiced'?"


def test_unknown_deliverable(tmp_path: Path) -> None:
    docs = base_docs()
    docs["agent"]["agent"]["task"]["deliverables"].append("magic")
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "AGENT.UNKNOWN_DELIVERABLE" in codes(result)


def test_action_proposals_requires_permitted_actions(tmp_path: Path) -> None:
    docs = base_docs()
    docs["agent"]["agent"]["permitted_actions"] = {}
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "AGENT.NO_ACTIONS" in codes(result)


def test_actions_without_proposals_warns(tmp_path: Path) -> None:
    docs = base_docs()
    docs["agent"]["agent"]["task"]["deliverables"] = ["explanation"]
    result = load_workspace(make_workspace(tmp_path, docs))
    assert result.ok
    assert "AGENT.ACTIONS_UNREACHABLE" in [
        d.code for d in result.diagnostics if d.severity == "warning"
    ]


def test_action_param_ref_to_event_rejected(tmp_path: Path) -> None:
    docs = base_docs()
    params = docs["agent"]["agent"]["permitted_actions"]["SendReminder"]["params"]
    params["when_event"] = {"type": "ref", "ref": "JobCompleted"}
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "AGENT.UNKNOWN_REF" in codes(result)


def test_action_param_invalid_spec(tmp_path: Path) -> None:
    docs = base_docs()
    params = docs["agent"]["agent"]["permitted_actions"]["SendReminder"]["params"]
    params["reason"] = {"type": "mystery"}
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "SCHEMA.INVALID_PROPERTY_TYPE" in codes(result)


def test_typed_agent_model(valid_workspace: Path) -> None:
    result = load_workspace(valid_workspace)
    assert result.ok
    agent = result.workspace.agent
    assert agent.projection == "CompletedButNotInvoiced"
    assert "action_proposals" in agent.task.deliverables
    action = agent.permitted_actions["SendReminder"]
    assert action.params["target"].type == "ref"
    assert action.params["target"].ref == "Person"
    assert action.params["reason"].required is True
