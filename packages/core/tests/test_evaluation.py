"""Evaluation contract validation."""

from __future__ import annotations

from pathlib import Path

from support import EXAMPLE_WORKSPACE, base_docs, codes, make_workspace

from ontology_core import load_workspace


def test_unknown_start_event(tmp_path: Path) -> None:
    docs = base_docs()
    docs["evaluation"]["evaluation"]["interval"]["start_event"] = "JobComplete"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    diag = next(d for d in result.diagnostics if d.code == "EVAL.UNKNOWN_EVENT")
    assert diag.hint == "did you mean 'JobCompleted'?"


def test_same_start_and_end_event(tmp_path: Path) -> None:
    docs = base_docs()
    docs["evaluation"]["evaluation"]["interval"]["end_event"] = "JobCompleted"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "EVAL.SAME_EVENT" in codes(result)


def test_interval_subject_mismatch(tmp_path: Path) -> None:
    docs = base_docs()
    docs["domain"]["domain"]["events"]["InvoiceIssued"]["subject"] = "Invoice"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "EVAL.SUBJECT_MISMATCH" in codes(result)


def test_invalid_aggregation(tmp_path: Path) -> None:
    docs = base_docs()
    docs["evaluation"]["evaluation"]["aggregation"] = "p99"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "EVAL.INVALID_AGGREGATION" in codes(result)


def test_invalid_unit(tmp_path: Path) -> None:
    docs = base_docs()
    docs["evaluation"]["evaluation"]["unit"] = "weeks"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "EVAL.INVALID_UNIT" in codes(result)


def test_invalid_metric(tmp_path: Path) -> None:
    docs = base_docs()
    docs["evaluation"]["evaluation"]["metric"] = "speed"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "EVAL.INVALID_METRIC" in codes(result)


def test_invalid_comparator(tmp_path: Path) -> None:
    docs = base_docs()
    docs["evaluation"]["evaluation"]["target"]["comparator"] = "under"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "EVAL.INVALID_COMPARATOR" in codes(result)


def test_negative_baseline(tmp_path: Path) -> None:
    docs = base_docs()
    docs["evaluation"]["evaluation"]["baseline"]["value"] = -1
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "EVAL.INVALID_VALUE" in codes(result)


def test_non_numeric_target(tmp_path: Path) -> None:
    docs = base_docs()
    docs["evaluation"]["evaluation"]["target"]["value"] = "four"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "SCHEMA.INVALID_TYPE" in codes(result)


def test_guardrail_not_increase_takes_no_value(tmp_path: Path) -> None:
    docs = base_docs()
    docs["evaluation"]["evaluation"]["guardrails"][0]["value"] = 0.05
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "EVAL.INVALID_GUARDRAIL" in codes(result)


def test_guardrail_comparator_requires_value(tmp_path: Path) -> None:
    docs = base_docs()
    docs["evaluation"]["evaluation"]["guardrails"][0]["comparator"] = "lte"
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "SCHEMA.MISSING_FIELD" in codes(result)


def test_duplicate_guardrail_name(tmp_path: Path) -> None:
    docs = base_docs()
    docs["evaluation"]["evaluation"]["guardrails"].append(
        {"name": "invoice-error-rate", "metric": "other", "comparator": "lte", "value": 1}
    )
    result = load_workspace(make_workspace(tmp_path, docs))
    assert not result.ok
    assert "EVAL.DUPLICATE_GUARDRAIL" in codes(result)


def test_reference_contract() -> None:
    # The committed workspace preserves baseline median 9, strict target < 4,
    # and an explicit invoice-error guardrail.
    result = load_workspace(EXAMPLE_WORKSPACE)
    assert result.ok, [d.format() for d in result.diagnostics]
    evaluation = result.workspace.evaluation
    assert evaluation.baseline == 9
    assert evaluation.aggregation == "median"
    assert evaluation.unit == "days"
    assert evaluation.target.comparator == "lt"
    assert evaluation.target.value == 4
    guardrail = next(g for g in evaluation.guardrails if g.name == "invoice-error-rate")
    assert guardrail.metric == "invoice_error_rate"
    assert guardrail.comparator == "not_increase"
    assert evaluation.interval.start_event == "JobCompleted"
    assert evaluation.interval.end_event == "InvoiceIssued"


def test_strict_target_is_lt_not_lte() -> None:
    # comparator "lt" is strict: under it a 4-day median remains a miss,
    # which "lte" would accept.
    result = load_workspace(EXAMPLE_WORKSPACE)
    target = result.workspace.evaluation.target
    assert target.comparator == "lt"
    assert target.value == 4
    assert not (4 < 4)
