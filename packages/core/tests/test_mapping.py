"""Mapping definition validation."""

from __future__ import annotations

from pathlib import Path

from support import base_docs, codes, make_workspace

from ontology_core import load_workspace


def test_unknown_entity_with_hint(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["entities"][0]["entity"] = "Jbo"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    diag = next(d for d in result.diagnostics if d.code == "MAPPING.UNKNOWN_ENTITY")
    assert diag.hint == "did you mean 'Job'?"


def test_duplicate_entity_binding(tmp_path: Path) -> None:
    docs = base_docs()
    dup = {"entity": "Job", "id": {"field": "id"}}
    docs["mapping"]["mapping"]["entities"].append(dup)
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "MAPPING.DUPLICATE_ENTITY" in codes(result)


def test_unknown_property(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["entities"][0]["properties"]["statsu"] = {"field": "status"}
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    diag = next(d for d in result.diagnostics if d.code == "MAPPING.UNKNOWN_PROPERTY")
    assert diag.hint == "did you mean 'status'?"


def test_incompatible_cast(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["entities"][0]["properties"]["status"] = {
        "field": "status",
        "as": "integer",
    }
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "MAPPING.INCOMPATIBLE_CAST" in codes(result)


def test_cast_forbidden_on_ref_property(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["entities"][0]["properties"]["assignee"] = {
        "field": "assignee",
        "as": "string",
    }
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "MAPPING.INCOMPATIBLE_CAST" in codes(result)


def test_cast_required_for_non_string_property(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["entities"][0]["properties"]["amount"] = {"field": "amount"}
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "MAPPING.MISSING_CAST" in codes(result)


def test_cast_forbidden_on_entity_id(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["entities"][0]["id"] = {"field": "id", "as": "integer"}
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "MAPPING.INVALID_CAST" in codes(result)


def test_assignment_requires_exactly_one_source(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["entities"][0]["properties"]["status"] = {
        "field": "status",
        "literal": "x",
    }
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "MAPPING.INVALID_ASSIGNMENT" in codes(result)


def test_literal_type_mismatch(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["entities"][0]["properties"]["amount"] = {
        "literal": "lots",
        "as": "number",
    }
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "MAPPING.INCOMPATIBLE_LITERAL" in codes(result)


def test_unknown_event_with_hint(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["events"][0]["event"] = "JobComplete"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    diag = next(d for d in result.diagnostics if d.code == "MAPPING.UNKNOWN_EVENT")
    assert diag.hint == "did you mean 'JobCompleted'?"


def test_subject_mismatch_with_domain(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["events"][0]["subject"]["entity"] = "Person"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "MAPPING.SUBJECT_MISMATCH" in codes(result)


def test_invalid_condition_rejected(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["events"][0]["when"] = {
        "op": "shell",
        "field": "status",
        "value": "rm -rf /",
    }
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "CONDITION.UNKNOWN_OP" in codes(result)


def test_unknown_source_format(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["source"]["format"] = "xml"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "MAPPING.UNKNOWN_FORMAT" in codes(result)


def test_occurred_at_requires_time_cast(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["events"][0]["occurred_at"] = {"field": "completed_at"}
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "MAPPING.MISSING_CAST" in codes(result)


def test_occurred_at_rejects_non_time_cast(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["events"][0]["occurred_at"] = {
        "field": "completed_at",
        "as": "integer",
    }
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "MAPPING.INVALID_CAST" in codes(result)


def test_entity_id_literal_must_be_string(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["entities"][0]["id"] = {"literal": 101}
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "MAPPING.INCOMPATIBLE_LITERAL" in codes(result)


def test_subject_id_literal_must_be_string(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["events"][0]["subject"]["id"] = {"literal": 101}
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "MAPPING.INCOMPATIBLE_LITERAL" in codes(result)


def test_occurred_at_literal_must_be_string(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["events"][0]["occurred_at"] = {
        "literal": 123,
        "as": "date",
    }
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "MAPPING.INCOMPATIBLE_LITERAL" in codes(result)


def test_observed_at_binding(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["events"][0]["observed_at"] = {
        "field": "seen_at",
        "as": "datetime",
    }
    result = load_workspace(make_workspace(tmp_path, docs))
    assert result.ok, [d.format() for d in result.diagnostics]
    binding = result.workspace.mapping.events[0]
    assert binding.observed_at is not None
    assert binding.observed_at.field == "seen_at"


def test_observed_at_rejects_non_time_cast(tmp_path: Path) -> None:
    docs = base_docs()
    docs["mapping"]["mapping"]["events"][0]["observed_at"] = {
        "field": "seen_at",
        "as": "string",
    }
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "MAPPING.INVALID_CAST" in codes(result)


def test_typed_mapping_model(valid_workspace: Path) -> None:
    result = load_workspace(valid_workspace)
    assert result.ok
    mapping = result.workspace.mapping
    job = next(b for b in mapping.entities if b.entity == "Job")
    assert job.id.field == "id"
    assert job.properties["amount"].cast == "number"
    completed = next(b for b in mapping.events if b.event == "JobCompleted")
    assert completed.when is not None
    assert completed.when.raw["op"] == "eq"
    assert completed.occurred_at.cast == "date"
