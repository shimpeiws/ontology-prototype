"""Shared-input live-model comparison runner tests (issue #35).

The runner evaluates N provider/model subjects against one shared
PreparedEval. These tests prove — through controlled doubles only, no
credentials and no network — that subjects see identical input, that
unavailable and failed candidates are recorded explicitly, that failure
reasons stay sanitized, and that the JSON artifact never overwrites.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ontology_core import (
    EvalError,
    ProviderError,
    RunBudget,
    SubjectSpec,
    compare_subjects,
    prepare,
    run_comparison,
    write_comparison_report,
)
from ontology_core.diagnosis_eval import FailingProvider, ScriptedProvider

from test_diagnosis_eval import _golden_text


def _specs(*pairs: tuple[str, str]) -> list[SubjectSpec]:
    return [SubjectSpec(provider=p, model=m) for p, m in pairs]


def _resolver(**providers):
    def resolve(name: str):
        try:
            return providers[name]
        except KeyError:
            raise ProviderError(f"unknown provider {name!r}") from None

    return resolve


def test_run_comparison_shared_input_and_artifact(tmp_path: Path) -> None:
    """INV-001/INV-008: N subjects share one frozen input — the artifact
    records the same input digest and projection run id per subject,
    plus status, request count, latency, and per-check results."""
    probe = prepare(tmp_path / "probe")
    golden = _golden_text(probe.candidates)
    resolve = _resolver(
        **{
            "double-a": ScriptedProvider("double-a", golden),
            "double-b": ScriptedProvider("double-b", golden),
        }
    )
    specs = _specs(
        ("double-a", "eval/model-a"),
        ("double-b", "eval/model-b"),
    )
    out = tmp_path / "report" / "comparison.json"

    report = run_comparison(
        tmp_path / "scratch", specs, resolve, report_path=out
    )

    assert report.input_identical
    assert report.input_digest
    assert report.candidates == 2
    assert report.candidate_ids == ["E01", "E02"]
    assert len(report.subjects) == 2
    assert report.unavailable == []
    assert report.provider_requests == 2
    for subject in report.subjects:
        assert subject.status == "ok"
        assert subject.passed
        assert subject.provider_requests == 1
        assert subject.latency_seconds >= 0
        assert subject.repeats_requested == 1
        assert subject.repeats_completed == 1
        assert subject.within_budget
        assert subject.projection_run_id == report.projection_run_id
        assert subject.input_digest == report.input_digest
        assert subject.runs[0]["checks"]

    payload = json.loads(out.read_text())
    assert payload["input_identical"] is True
    assert payload["input_digest"] == report.input_digest
    assert payload["eval_version"] == report.eval_version
    assert payload["prompt_version"] == "diagnosis.v1"
    assert {s["model"] for s in payload["subjects"]} == {
        "eval/model-a",
        "eval/model-b",
    }
    assert all(
        s["projection_run_id"] == report.projection_run_id
        and s["input_digest"] == report.input_digest
        for s in payload["subjects"]
    )


def test_unavailable_and_failed_subjects_recorded(tmp_path: Path) -> None:
    """INV-002: an unresolvable provider lands in ``unavailable``; a
    provider failing mid-run is a ``failed`` subject — both explicit."""
    probe = prepare(tmp_path / "probe")
    golden = _golden_text(probe.candidates)

    def resolve(name: str):
        if name == "double-missing":
            raise ProviderError("DOUBLE_API_KEY is not set")
        if name == "double-down":
            return FailingProvider("double-down")
        return ScriptedProvider(name, golden)

    specs = _specs(
        ("double-ok", "eval/ok"),
        ("double-down", "eval/down"),
        ("double-missing", "eval/missing"),
    )
    report = run_comparison(tmp_path / "scratch", specs, resolve)

    statuses = {s.model: s.status for s in report.subjects}
    assert statuses == {"eval/ok": "ok", "eval/down": "failed"}
    down = next(s for s in report.subjects if s.model == "eval/down")
    assert down.run_ids  # the failed run is still recorded
    assert down.provider_requests == 1
    assert [
        (u.provider, u.model) for u in report.unavailable
    ] == [("double-missing", "eval/missing")]
    assert "DOUBLE_API_KEY is not set" in report.unavailable[0].reason
    # Both evaluated subjects — including the failed one — shared input.
    assert report.input_identical


def test_unexpected_resolution_error_is_sanitized(tmp_path: Path) -> None:
    """INV-003: a non-ProviderError's message may carry credentials, so
    only its class reaches the report."""
    marker = "sk-super-secret-resolution-key"
    probe = prepare(tmp_path / "probe")
    golden = _golden_text(probe.candidates)

    def resolve(name: str):
        if name == "double-bad":
            raise RuntimeError(f"config exploded: {marker}")
        return ScriptedProvider(name, golden)

    specs = _specs(("double-ok", "eval/ok"), ("double-bad", "eval/bad"))
    report = run_comparison(tmp_path / "scratch", specs, resolve)

    assert report.unavailable[0].reason == "unexpected RuntimeError"
    assert marker not in json.dumps(report.to_dict())


def test_report_artifact_never_overwrites(tmp_path: Path) -> None:
    """INV-005: writing to an existing artifact path fails rather than
    silently replacing a previous run's evidence."""
    probe = prepare(tmp_path / "probe")
    golden = _golden_text(probe.candidates)
    report = run_comparison(
        tmp_path / "scratch",
        _specs(("double-a", "eval/a")),
        _resolver(**{"double-a": ScriptedProvider("double-a", golden)}),
    )
    out = tmp_path / "report.json"
    write_comparison_report(report, out)
    with pytest.raises(EvalError, match="already exists"):
        write_comparison_report(report, out)


def test_run_comparison_requires_a_subject(tmp_path: Path) -> None:
    with pytest.raises(EvalError):
        run_comparison(tmp_path / "scratch", [], _resolver())


def test_compare_subjects_repeats_and_budget(tmp_path: Path) -> None:
    """INV-008: repeats and the request cap flow through to each
    subject's evaluation unchanged."""
    probe = prepare(tmp_path / "probe")
    golden = _golden_text(probe.candidates)
    prepared = prepare(tmp_path / "shared")
    report = compare_subjects(
        prepared,
        _specs(("double-a", "eval/a")),
        _resolver(**{"double-a": ScriptedProvider("double-a", golden)}),
        repeats=2,
        budget=RunBudget(repeats=2, max_provider_requests=4),
    )
    subject = report.subjects[0]
    assert subject.repeats_requested == 2
    assert subject.repeats_completed == 2
    assert not subject.repeats_clamped
    assert subject.provider_requests == 2
    assert len(subject.run_ids) == 2
    assert subject.input_digest == report.input_digest


def test_zero_repeat_budget_marks_skipped_not_identical(
    tmp_path: Path,
) -> None:
    """A subject whose budget allowed zero runs is ``skipped`` with no
    digest — it must not count toward an identical-input claim."""
    probe = prepare(tmp_path / "probe")
    golden = _golden_text(probe.candidates)
    prepared = prepare(tmp_path / "shared")
    report = compare_subjects(
        prepared,
        _specs(("double-a", "eval/a")),
        _resolver(**{"double-a": ScriptedProvider("double-a", golden)}),
        budget=RunBudget(repeats=0, max_provider_requests=4),
    )
    assert report.subjects[0].status == "skipped"
    assert report.subjects[0].input_digest is None
    assert report.input_digest is None
    assert not report.input_identical
