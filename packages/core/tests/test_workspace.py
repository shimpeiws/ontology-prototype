"""End-to-end workspace loading against the committed example."""

from __future__ import annotations

from pathlib import Path

from support import EXAMPLE_WORKSPACE, base_docs, make_workspace

from ontology_core import Workspace, load_workspace


def test_example_workspace_valid() -> None:
    result = load_workspace(EXAMPLE_WORKSPACE)
    assert result.ok, [d.format() for d in result.diagnostics]
    assert result.diagnostics == []
    assert isinstance(result.workspace, Workspace)


def test_example_workspace_has_data_fixture() -> None:
    assert (EXAMPLE_WORKSPACE / "data" / "jobs.csv").is_file()


def test_diagnostics_are_deterministic(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["entities"][0]["entity"] = "Jbo"
    docs["evaluation"]["evaluation"]["aggregation"] = "bogus"
    first = load_workspace(make_workspace(tmp_path / "a", docs))
    second = load_workspace(make_workspace(tmp_path / "b", docs))
    assert [(d.code, d.path, d.message) for d in first.diagnostics] == [
        (d.code, d.path, d.message) for d in second.diagnostics
    ]


def test_missing_domain_warns_distinctly(tmp_path: Path) -> None:
    docs = base_docs()
    workspace = make_workspace(tmp_path, docs)
    (workspace / "domain.yaml").unlink()
    result = load_workspace(workspace)
    assert not result.ok
    warning = next(
        d for d in result.diagnostics if d.code == "WORKSPACE.CROSS_VALIDATION_SKIPPED"
    )
    assert warning.file == "domain.yaml"
    assert "missing or invalid" in warning.message


def test_each_error_class_is_rejected(tmp_path: Path) -> None:
    # Representative case per acceptance-criterion rejection class.
    cases = {
        "unknown_types": lambda d: d["mapping"]["mapping"]["entities"][0].update(
            entity="Alien"
        ),
        "missing_required_fields": lambda d: d["domain"]["domain"].__delitem__("name"),
        "invalid_references": lambda d: d["domain"]["domain"]["entities"]["Job"][
            "properties"
        ]["assignee"].update(ref="Nobody"),
        "incompatible_properties": lambda d: d["mapping"]["mapping"]["entities"][0][
            "properties"
        ].update(status={"field": "status", "as": "integer"}),
        "inconsistent_contracts": lambda d: d["evaluation"]["evaluation"]["interval"].update(
            end_event="JobCompleted"
        ),
    }
    for index, (label, mutate) in enumerate(cases.items()):
        docs = base_docs()
        mutate(docs)
        workspace_dir = make_workspace(tmp_path / label, docs)
        result = load_workspace(workspace_dir)
        assert not result.ok, label
        assert result.errors, label
