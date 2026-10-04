"""Domain definition validation."""

from __future__ import annotations

from pathlib import Path

from support import base_docs, codes, make_workspace

from ontology_core import load_workspace


def test_entity_id_policy_required(tmp_path: Path) -> None:
    docs = base_docs()
    del docs["domain"]["domain"]["policies"]["entity_id_type"]
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "SCHEMA.MISSING_FIELD" in codes(result)


def test_entity_id_policy_must_be_string(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["domain"]["policies"]["entity_id_type"] = "uuid"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "DOMAIN.INVALID_ID_POLICY" in codes(result)


def test_time_policy_fields_required(tmp_path: Path) -> None:
    docs = base_docs()
    del docs["domain"]["domain"]["policies"]["event_times"]["observation_field"]
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "SCHEMA.MISSING_FIELD" in codes(result)


def test_time_policy_names_fixed_in_v1(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["domain"]["policies"]["event_times"]["occurrence_field"] = "happened_at"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "DOMAIN.INVALID_TIME_POLICY" in codes(result)

    docs = base_docs()
    docs["domain"]["domain"]["policies"]["event_times"]["observation_field"] = "seen_at"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "DOMAIN.INVALID_TIME_POLICY" in codes(result)


def test_date_only_timezone_must_be_supported(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["domain"]["policies"]["event_times"]["date_only_timezone"] = "local"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "DOMAIN.UNSUPPORTED_TIMEZONE" in codes(result)


def test_unknown_property_type(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["domain"]["entities"]["Job"]["properties"]["status"] = {"type": "text"}
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "SCHEMA.INVALID_PROPERTY_TYPE" in codes(result)


def test_unknown_ref_target_with_hint(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["domain"]["entities"]["Job"]["properties"]["assignee"]["ref"] = "Persn"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    diag = next(d for d in result.diagnostics if d.code == "DOMAIN.UNKNOWN_REF")
    assert "Persn" in diag.message
    assert diag.hint == "did you mean 'Person'?"


def test_ref_target_missing(tmp_path: Path) -> None:
    docs = base_docs()
    del docs["domain"]["domain"]["entities"]["Job"]["properties"]["assignee"]["ref"]
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "DOMAIN.REF_TARGET_MISSING" in codes(result)


def test_ref_on_non_ref_type(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["domain"]["entities"]["Person"]["properties"]["name"]["ref"] = "Job"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "DOMAIN.UNEXPECTED_REF" in codes(result)


def test_unknown_event_subject(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["domain"]["events"]["JobCompleted"]["subject"] = "Jbo"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    diag = next(d for d in result.diagnostics if d.code == "DOMAIN.UNKNOWN_SUBJECT")
    assert diag.hint == "did you mean 'Job'?"


def test_unknown_projection_subject(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["domain"]["projections"]["CompletedButNotInvoiced"]["subject"] = "Ghost"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "DOMAIN.UNKNOWN_SUBJECT" in codes(result)


def test_event_requires_subject(tmp_path: Path) -> None:
    docs = base_docs()
    del docs["domain"]["domain"]["events"]["JobCompleted"]["subject"]
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "SCHEMA.MISSING_FIELD" in codes(result)


def test_invalid_entity_name(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["domain"]["entities"]["bad name!"] = {"properties": {}}
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "SCHEMA.INVALID_NAME" in codes(result)


def test_missing_policies(tmp_path: Path) -> None:
    docs = base_docs()
    del docs["domain"]["domain"]["policies"]
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "SCHEMA.MISSING_FIELD" in codes(result)


def test_broken_domain_skips_cross_validation(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["domain"]["entities"]["Job"]["properties"]["status"] = {"type": "bogus"}
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "WORKSPACE.CROSS_VALIDATION_SKIPPED" in codes(result)
    # Mapping references are not double-reported against a failed domain.
    assert "MAPPING.UNKNOWN_ENTITY" not in codes(result)


def test_typed_domain_model(tmp_path: Path, valid_workspace: Path) -> None:
    result = load_workspace(valid_workspace)
    assert result.ok
    domain = result.workspace.domain
    assert domain.policies.entity_id_type == "string"
    assert domain.policies.event_times.date_only_timezone == "UTC"
    assert domain.entities["Job"].properties["assignee"].type == "ref"
    assert domain.entities["Job"].properties["assignee"].ref == "Person"
    assert domain.events["InvoiceIssued"].subject == "Job"
    assert "CompletedButNotInvoiced" in domain.projections
