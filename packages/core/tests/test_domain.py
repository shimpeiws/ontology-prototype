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
    spec = domain.projections["CompletedButNotInvoiced"]
    assert spec.select.required_events == ("JobCompleted",)
    assert spec.select.absent_events == ("InvoiceIssued",)
    assert spec.select.elapsed is not None
    assert spec.select.elapsed.event == "JobCompleted"
    assert spec.select.elapsed.comparator == "gt"
    assert spec.select.elapsed.value == 2
    assert spec.select.elapsed.unit == "days"
    assert spec.select.where is None


def test_projection_requires_select(tmp_path: Path) -> None:
    docs = base_docs()
    del docs["domain"]["domain"]["projections"]["CompletedButNotInvoiced"]["select"]
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    diag = next(d for d in result.diagnostics if d.code == "SCHEMA.MISSING_FIELD")
    assert diag.file == "domain.yaml"
    assert diag.path == "domain.projections.CompletedButNotInvoiced.select"


def test_select_rejects_unknown_keys(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["domain"]["projections"]["CompletedButNotInvoiced"]["select"][
        "python"
    ] = "lambda s: True"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "SCHEMA.UNEXPECTED_KEY" in codes(result)


def test_select_required_events_nonempty(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["domain"]["projections"]["CompletedButNotInvoiced"]["select"][
        "required_events"
    ] = []
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "SCHEMA.INVALID_VALUE" in codes(result)


def test_select_unknown_event(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["domain"]["projections"]["CompletedButNotInvoiced"]["select"][
        "required_events"
    ] = ["JobCompletd"]
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    diag = next(d for d in result.diagnostics if d.code == "DOMAIN.UNKNOWN_EVENT")
    assert (
        diag.path
        == "domain.projections.CompletedButNotInvoiced.select.required_events[0]"
    )
    assert diag.hint == "did you mean 'JobCompleted'?"


def test_select_event_subject_mismatch(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["domain"]["events"]["PersonRegistered"] = {"subject": "Person"}
    docs["domain"]["domain"]["projections"]["CompletedButNotInvoiced"]["select"][
        "absent_events"
    ] = ["PersonRegistered"]
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    diag = next(
        d for d in result.diagnostics if d.code == "DOMAIN.EVENT_SUBJECT_MISMATCH"
    )
    assert diag.file == "domain.yaml"
    assert diag.path == (
        "domain.projections.CompletedButNotInvoiced.select.absent_events[0]"
    )


def test_select_overlapping_events(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["domain"]["projections"]["CompletedButNotInvoiced"]["select"][
        "absent_events"
    ] = ["JobCompleted"]
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "DOMAIN.OVERLAPPING_EVENTS" in codes(result)


def test_select_elapsed_validation(tmp_path: Path) -> None:
    select = base_docs()["domain"]["domain"]["projections"][
        "CompletedButNotInvoiced"
    ]["select"]

    docs = base_docs()
    docs["domain"]["domain"]["projections"]["CompletedButNotInvoiced"]["select"] = {
        **select,
        "elapsed": {"comparator": "eq", "value": 2, "unit": "days"},
    }
    result = load_workspace(make_workspace(tmp_path / "cmp", docs))
    assert not result.ok
    assert "DOMAIN.INVALID_COMPARATOR" in codes(result)

    docs = base_docs()
    docs["domain"]["domain"]["projections"]["CompletedButNotInvoiced"]["select"] = {
        **select,
        "elapsed": {"comparator": "gt", "value": -1, "unit": "days"},
    }
    result = load_workspace(make_workspace(tmp_path / "neg", docs))
    assert not result.ok
    assert "DOMAIN.INVALID_VALUE" in codes(result)

    docs = base_docs()
    docs["domain"]["domain"]["projections"]["CompletedButNotInvoiced"]["select"] = {
        **select,
        "elapsed": {"comparator": "gt", "value": 2, "unit": "weeks"},
    }
    result = load_workspace(make_workspace(tmp_path / "unit", docs))
    assert not result.ok
    assert "DOMAIN.INVALID_UNIT" in codes(result)

    docs = base_docs()
    docs["domain"]["domain"]["projections"]["CompletedButNotInvoiced"]["select"] = {
        **select,
        "elapsed": {
            "event": "InvoiceIssued",
            "comparator": "gt",
            "value": 2,
            "unit": "days",
        },
    }
    result = load_workspace(make_workspace(tmp_path / "evt", docs))
    assert not result.ok
    assert "DOMAIN.ELAPSED_EVENT" in codes(result)


def test_select_elapsed_null_is_an_error(tmp_path: Path) -> None:
    # An explicit `elapsed:` key with a null value must fail loudly —
    # silently treating it as "no elapsed" would drop the projection
    # from an otherwise valid workspace.
    docs = base_docs()
    docs["domain"]["domain"]["projections"]["CompletedButNotInvoiced"]["select"][
        "elapsed"
    ] = None
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    diag = next(d for d in result.diagnostics if d.code == "SCHEMA.INVALID_TYPE")
    assert diag.file == "domain.yaml"
    assert diag.path == "domain.projections.CompletedButNotInvoiced.select.elapsed"


def test_select_elapsed_value_must_be_finite(tmp_path: Path) -> None:
    # YAML `.nan`/`.inf` parse as float; a non-finite threshold would make
    # every elapsed comparison silently true or false at run time.
    select = base_docs()["domain"]["domain"]["projections"][
        "CompletedButNotInvoiced"
    ]["select"]

    docs = base_docs()
    docs["domain"]["domain"]["projections"]["CompletedButNotInvoiced"]["select"] = {
        **select,
        "elapsed": {"comparator": "gt", "value": float("nan"), "unit": "days"},
    }
    result = load_workspace(make_workspace(tmp_path / "nan", docs))
    assert not result.ok
    assert "DOMAIN.INVALID_VALUE" in codes(result)

    docs = base_docs()
    docs["domain"]["domain"]["projections"]["CompletedButNotInvoiced"]["select"] = {
        **select,
        "elapsed": {"comparator": "gt", "value": float("inf"), "unit": "days"},
    }
    result = load_workspace(make_workspace(tmp_path / "inf", docs))
    assert not result.ok
    assert "DOMAIN.INVALID_VALUE" in codes(result)


def test_select_where_must_be_a_condition(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["domain"]["projections"]["CompletedButNotInvoiced"]["select"][
        "where"
    ] = {"op": "exec", "field": "status"}
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "CONDITION.UNKNOWN_OP" in codes(result)


def test_select_where_fields_are_subject_properties(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["domain"]["projections"]["CompletedButNotInvoiced"]["select"][
        "where"
    ] = {"op": "eq", "field": "assigne", "value": "taro"}
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    diag = next(d for d in result.diagnostics if d.code == "DOMAIN.UNKNOWN_FIELD")
    assert diag.file == "domain.yaml"
    assert diag.path == "domain.projections.CompletedButNotInvoiced.select.where"
    assert diag.hint == "did you mean 'assignee'?"


def test_select_where_fields_skip_when_subject_undeclared(
    tmp_path: Path,
) -> None:
    # With an undeclared subject there is no property vocabulary to check
    # `where` fields against: UNKNOWN_SUBJECT alone reports the problem and
    # subject-dependent checks (field names, event subjects) do not cascade.
    docs = base_docs()
    projection = docs["domain"]["domain"]["projections"]["CompletedButNotInvoiced"]
    projection["subject"] = "NoSuchEntity"
    projection["select"]["where"] = {"op": "eq", "field": "anything", "value": 1}
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "DOMAIN.UNKNOWN_SUBJECT" in codes(result)
    assert "DOMAIN.UNKNOWN_FIELD" not in codes(result)
    assert "DOMAIN.EVENT_SUBJECT_MISMATCH" not in codes(result)
