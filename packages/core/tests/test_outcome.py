"""Outcome evaluation: cohort pairing, aggregates, target, guardrails."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import support

from ontology_core import (
    Observation,
    ResolvedEntity,
    SqliteCanonicalStore,
    get_evaluation_run,
    ingest_snapshot,
    load_workspace,
    run_evaluation,
)
from ontology_core.evidence import RUNTIME_DIR
from ontology_core.ingest import ACCEPTED

FIXED_NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
BOUNDARY = datetime(2026, 10, 5, 0, 0, 0, tzinfo=timezone.utc)
AS_OF = datetime(2026, 10, 15, 0, 0, 0, tzinfo=timezone.utc)

# Baseline cohort (completed before the 2026-10-05 boundary): durations
# 9, 8, 10 days -> median 9. After cohort: 3, 3, 4 -> median 3.
CSV_SAMPLE = (
    "id,status,assignee,amount,completed_at,invoiced_at\n"
    "B1,completed,taro,1,2026-09-21,2026-09-30\n"
    "B2,completed,taro,1,2026-09-22,2026-09-30\n"
    "B3,completed,taro,1,2026-09-20,2026-09-30\n"
    "A1,completed,hanako,1,2026-10-06,2026-10-09\n"
    "A2,completed,hanako,1,2026-10-07,2026-10-10\n"
    "A3,completed,hanako,1,2026-10-06,2026-10-10\n"
)


def make_ws(tmp_path: Path):
    ws_dir = support.make_workspace(tmp_path / "ws")
    result = load_workspace(ws_dir)
    assert result.ok, [d.format() for d in result.diagnostics]
    assert result.workspace is not None
    return result.workspace


def write_snapshot(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def make_evaluated_ws(tmp_path: Path, csv_text: str = CSV_SAMPLE):
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", csv_text)
    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)
    assert result.ok, [d.format() for d in result.diagnostics]
    return workspace


def add_invoices(store, invoices: list[tuple[str, str, bool]]) -> None:
    """Record Invoice entities directly, as ingest would after a snapshot
    carrying invoice error evidence."""
    observations = [
        Observation(
            observation_id=f"obs-inv-{index}",
            ingest_id="ing-inv",
            source="invoices",
            row=index,
            status=ACCEPTED,
            observed_at=FIXED_NOW,
            content_digest="sha256:test",
            mapping_digest="sha256:test",
            record={},
            entities=[
                ResolvedEntity(
                    entity="Invoice",
                    id=invoice_id,
                    properties={"job": job, "error": error},
                )
            ],
            events=[],
            diagnostics=[],
        )
        for index, (invoice_id, job, error) in enumerate(invoices)
    ]
    store.apply("ing-inv", observations)


def test_reference_outcome(tmp_path: Path) -> None:
    # INV-001/INV-003: baseline 9/8/10 -> median 9; after 3/3/4 -> median
    # 3; improvement of 6 days and strict <4 target attained. Without
    # invoice-error evidence the guardrail is unavailable and overall is
    # unverified — not a success claim.
    workspace = make_evaluated_ws(tmp_path)

    result = run_evaluation(workspace, as_of=AS_OF, boundary=BOUNDARY)

    assert result.ok, [d.format() for d in result.diagnostics]
    assert result.recorded
    report = result.report
    assert report["cohorts"]["baseline"]["aggregate"] == 9.0
    assert report["cohorts"]["baseline"]["sample"] == 3
    assert report["cohorts"]["after"]["aggregate"] == 3.0
    assert report["cohorts"]["after"]["sample"] == 3
    assert report["change"] == {
        "status": "measured",
        "value": 6.0,
        "direction": "improvement",
    }
    assert report["target"]["status"] == "attained"
    assert report["guardrails"][0]["status"] == "unavailable"
    assert report["overall"]["status"] == "unverified"
    # Pairing evidence: every pair names its start and end fact keys.
    for pair in report["cohorts"]["baseline"]["paired"]:
        assert pair["start_fact_key"].startswith("fact-")
        assert pair["end_fact_key"].startswith("fact-")


def test_median_on_target_is_a_miss(tmp_path: Path) -> None:
    # INV-003: an after median of exactly 4 improves on 9 but misses the
    # strict < 4 target.
    workspace = make_evaluated_ws(
        tmp_path,
        "id,status,assignee,amount,completed_at,invoiced_at\n"
        "B1,completed,taro,1,2026-09-21,2026-09-30\n"
        "A1,completed,hanako,1,2026-10-06,2026-10-10\n",
    )

    result = run_evaluation(workspace, as_of=AS_OF, boundary=BOUNDARY)

    assert result.ok
    report = result.report
    assert report["cohorts"]["after"]["aggregate"] == 4.0
    assert report["change"]["direction"] == "improvement"
    assert report["target"]["status"] == "missed"
    assert report["overall"]["status"] == "not_met"


def test_pending_negative_and_ambiguous_intervals(tmp_path: Path) -> None:
    # INV-004/INV-010: pending jobs are counted, and negative/ambiguous
    # pairings produce diagnostics instead of silently vanishing.
    csv_text = (
        "id,status,assignee,amount,completed_at,invoiced_at\n"
        "B1,completed,taro,1,2026-09-21,2026-09-30\n"
        "A1,completed,hanako,1,2026-10-06,2026-10-09\n"
        "P1,completed,hanako,1,2026-10-08,\n"
        "N1,completed,hanako,1,2026-10-07,2026-10-06\n"
        "M1,completed,hanako,1,2026-10-06,\n"
        "M1,completed,hanako,1,2026-10-07,\n"
        "S1,completed,hanako,1,2026-10-07,2026-10-06\n"
        "S1,in_progress,hanako,1,,2026-10-09\n"
        "E1,completed,hanako,1,2026-10-06,2026-10-09\n"
        "E1,in_progress,hanako,1,,2026-10-11\n"
        "X1,in_progress,hanako,1,,2026-10-09\n"
    )
    workspace = make_evaluated_ws(tmp_path, csv_text)

    result = run_evaluation(workspace, as_of=AS_OF, boundary=BOUNDARY)

    assert result.ok, [d.format() for d in result.diagnostics]
    codes = [d.code for d in result.diagnostics]
    assert "EVALUATION.NEGATIVE_INTERVAL" in codes
    assert "EVALUATION.AMBIGUOUS_INTERVAL" in codes
    assert "EVALUATION.MULTIPLE_ENDS" in codes
    assert "EVALUATION.END_WITHOUT_START" in codes

    after = result.report["cohorts"]["after"]
    assert after["counts"]["pending"] == 1
    assert after["pending"][0]["subject_id"] == "P1"
    assert after["counts"]["invalid"] == 1
    assert after["invalid"][0]["subject_id"] == "N1"
    # M1 (two starts) and S1 (ends on both sides) are ambiguous.
    assert after["counts"]["ambiguous"] == 2
    assert {a["subject_id"] for a in after["ambiguous"]} == {"M1", "S1"}
    # E1 still pairs with the earliest end (3 days) despite a second one.
    paired = {p["subject_id"]: p for p in after["paired"]}
    assert paired["E1"]["duration"] == 3.0
    assert paired["E1"]["extra_end_fact_keys"]
    assert {p["subject_id"] for p in after["paired"]} == {"A1", "E1"}
    # The invoice-only job is reported as an unpaired end, not a cohort.
    assert [u["subject_id"] for u in result.report["unpaired_ends"]] == ["X1"]


def test_events_after_as_of_are_excluded(tmp_path: Path) -> None:
    # INV-009: an invoice dated after as_of does not close the interval;
    # a completion after as_of is a future-dated exclusion.
    csv_text = (
        "id,status,assignee,amount,completed_at,invoiced_at\n"
        "F1,completed,hanako,1,2026-10-08,2026-10-25\n"
        "G1,completed,hanako,1,2026-10-18,\n"
    )
    workspace = make_evaluated_ws(tmp_path, csv_text)

    result = run_evaluation(workspace, as_of=AS_OF, boundary=BOUNDARY)

    assert result.ok
    codes = [d.code for d in result.diagnostics]
    assert "EVALUATION.FUTURE_EVENT" in codes
    after = result.report["cohorts"]["after"]
    # F1 is pending at as_of even though an invoice is dated later.
    assert after["counts"] == {
        "paired": 0,
        "pending": 1,
        "invalid": 0,
        "ambiguous": 0,
    }
    assert after["pending"][0]["subject_id"] == "F1"
    inputs = {i["subject_id"]: i for i in result.report["inputs"]}
    assert inputs["F1"]["future_end_fact_keys"]
    assert inputs["G1"]["excluded"] == "future-dated start event"


def test_baseline_pair_closing_after_boundary_is_flagged(tmp_path: Path) -> None:
    # A job completed before the boundary but invoiced after it belongs
    # to the baseline cohort by the start-event rule — the report flags
    # the span rather than hiding it.
    workspace = make_evaluated_ws(
        tmp_path,
        "id,status,assignee,amount,completed_at,invoiced_at\n"
        "B1,completed,taro,1,2026-10-01,2026-10-07\n",
    )

    result = run_evaluation(workspace, as_of=AS_OF, boundary=BOUNDARY)

    assert result.ok
    pair = result.report["cohorts"]["baseline"]["paired"][0]
    assert pair["closes_after_boundary"] is True


def test_guardrail_unavailable_passed_and_failed(tmp_path: Path) -> None:
    # INV-005: explicit error counts and denominators decide the
    # guardrail; missing evidence is unavailable, never passed.
    workspace = make_evaluated_ws(tmp_path)
    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)

    # No invoice entities at all -> unavailable, overall unverified.
    result = run_evaluation(workspace, as_of=AS_OF, boundary=BOUNDARY, store=store)
    assert result.ok
    guardrail = result.report["guardrails"][0]
    assert guardrail["status"] == "unavailable"
    assert "baseline" in guardrail["reason"]
    assert result.report["overall"]["status"] == "unverified"
    assert "EVALUATION.GUARDRAIL_UNAVAILABLE" in [
        d.code for d in result.diagnostics
    ]

    # Errors 0/3 baseline, 0/3 after -> passed, overall success.
    workspace2 = make_evaluated_ws(tmp_path / "passed")
    store2 = SqliteCanonicalStore(Path(workspace2.path) / RUNTIME_DIR)
    add_invoices(
        store2,
        [
            ("inv-b1", "B1", False),
            ("inv-b2", "B2", False),
            ("inv-b3", "B3", False),
            ("inv-a1", "A1", False),
            ("inv-a2", "A2", False),
            ("inv-a3", "A3", False),
        ],
    )
    result2 = run_evaluation(
        workspace2, as_of=AS_OF, boundary=BOUNDARY, store=store2
    )
    assert result2.ok
    guardrail2 = result2.report["guardrails"][0]
    assert guardrail2["status"] == "passed"
    assert guardrail2["evidence"]["baseline"]["rate"] == 0.0
    assert guardrail2["evidence"]["after"] == {
        "invoices": 3,
        "errors": 0,
        "rate": 0.0,
        "invoices_missing_error": 0,
        "entity_ids": ["inv-a1", "inv-a2", "inv-a3"],
    }
    assert result2.report["overall"]["status"] == "success"

    # 1/3 errors after vs 0/3 baseline -> failed, overall not_met.
    workspace3 = make_evaluated_ws(tmp_path / "failed")
    store3 = SqliteCanonicalStore(Path(workspace3.path) / RUNTIME_DIR)
    add_invoices(
        store3,
        [
            ("inv-b1", "B1", False),
            ("inv-b2", "B2", False),
            ("inv-a1", "A1", False),
            ("inv-a2", "A2", True),
            ("inv-a3", "A3", False),
        ],
    )
    result3 = run_evaluation(
        workspace3, as_of=AS_OF, boundary=BOUNDARY, store=store3
    )
    assert result3.ok
    guardrail3 = result3.report["guardrails"][0]
    assert guardrail3["status"] == "failed"
    assert guardrail3["evidence"]["after"]["errors"] == 1
    assert guardrail3["evidence"]["after"]["rate"] == 1 / 3
    assert result3.report["overall"]["status"] == "not_met"


def test_guardrail_unsupported_metric(tmp_path: Path) -> None:
    docs = support.base_docs()
    docs["evaluation"]["evaluation"]["guardrails"][0]["metric"] = "other"
    ws_dir = support.make_workspace(tmp_path / "ws", docs)
    loaded = load_workspace(ws_dir)
    assert loaded.ok and loaded.workspace is not None
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)
    assert ingest_snapshot(
        loaded.workspace, snapshot, observed_at=FIXED_NOW
    ).ok

    result = run_evaluation(
        loaded.workspace, as_of=AS_OF, boundary=BOUNDARY
    )

    assert result.ok
    assert result.report["guardrails"][0]["status"] == "unavailable"
    assert "EVALUATION.UNSUPPORTED_GUARDRAIL" in [
        d.code for d in result.diagnostics
    ]


def test_count_aggregation_never_fabricates_an_aggregate(
    tmp_path: Path,
) -> None:
    # FIND-001: `count` returned 0.0 on an empty cohort because the
    # count branch ran before the empty guard — a fabricated aggregate
    # that made `change` measured and could attain the target on zero
    # evidence. No measured intervals means no aggregate, for every
    # aggregation.
    docs = support.base_docs()
    docs["evaluation"]["evaluation"]["aggregation"] = "count"
    ws_dir = support.make_workspace(tmp_path / "ws", docs)
    loaded = load_workspace(ws_dir)
    assert loaded.ok and loaded.workspace is not None
    workspace = loaded.workspace
    snapshot = write_snapshot(
        tmp_path,
        "jobs.csv",
        "id,status,assignee,amount,completed_at,invoiced_at\n"
        "B1,completed,taro,1,2026-09-21,2026-09-30\n"
        "A1,completed,hanako,1,2026-10-06,2026-10-09\n",
    )
    assert ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW).ok

    result = run_evaluation(workspace, as_of=AS_OF, boundary=BOUNDARY)

    assert result.ok
    report = result.report
    # Non-empty cohorts report the interval count as the aggregate…
    assert report["cohorts"]["baseline"]["aggregate"] == 1.0
    assert report["cohorts"]["after"]["aggregate"] == 1.0
    # …and a count delta is movement, not a duration verdict.
    assert report["change"] == {
        "status": "measured",
        "value": 0.0,
        "direction": "unchanged",
    }

    # An empty after cohort stays unavailable even under `count`.
    workspace2_dir = support.make_workspace(tmp_path / "ws2", docs)
    loaded2 = load_workspace(workspace2_dir)
    assert loaded2.ok and loaded2.workspace is not None
    snapshot2 = write_snapshot(
        tmp_path,
        "jobs2.csv",
        "id,status,assignee,amount,completed_at,invoiced_at\n"
        "B1,completed,taro,1,2026-09-21,2026-09-30\n",
    )
    assert ingest_snapshot(
        loaded2.workspace, snapshot2, observed_at=FIXED_NOW
    ).ok

    result2 = run_evaluation(
        loaded2.workspace, as_of=AS_OF, boundary=BOUNDARY
    )

    assert result2.ok
    report2 = result2.report
    assert report2["cohorts"]["after"]["aggregate"] is None
    assert report2["cohorts"]["after"]["counts"]["paired"] == 0
    assert report2["change"]["status"] == "unavailable"
    assert report2["target"]["status"] == "unverified"
    assert report2["overall"]["status"] == "unverified"


def test_subject_with_only_future_events_is_documented(
    tmp_path: Path,
) -> None:
    # FIND-003: a subject whose only interval facts are all after as_of
    # is `excluded` — inspectable in the inputs with its future fact
    # keys, emitting no diagnostic because it asserts nothing about the
    # as-of state.
    csv_text = (
        "id,status,assignee,amount,completed_at,invoiced_at\n"
        "I1,in_progress,hanako,1,,2026-10-25\n"
    )
    workspace = make_evaluated_ws(tmp_path, csv_text)

    result = run_evaluation(workspace, as_of=AS_OF, boundary=BOUNDARY)

    assert result.ok
    entry = result.report["inputs"][0]
    assert entry["subject_id"] == "I1"
    assert entry["disposition"] == "excluded"
    assert entry["excluded"] == "no events at or before as_of"
    assert entry["future_end_fact_keys"]
    assert result.report["unpaired_ends"] == []
    # No interval diagnostic fires for an all-future subject — the only
    # diagnostic is the unrelated missing guardrail evidence.
    assert [d.code for d in result.diagnostics] == [
        "EVALUATION.GUARDRAIL_UNAVAILABLE"
    ]


def test_record_failure_reports_but_does_not_freeze(
    tmp_path: Path,
) -> None:
    # FIND-002: a run whose freeze fails still returns the computed
    # report — but recorded stays False so no consumer can mistake it
    # for an inspectable run.
    workspace = make_evaluated_ws(tmp_path)

    class UnrecordableStore:
        def __init__(self, inner):
            self._inner = inner

        def subject_ids_with_event(self, event, subject):
            return self._inner.subject_ids_with_event(event, subject)

        def events_for_subject(self, entity, subject_id):
            return self._inner.events_for_subject(entity, subject_id)

        def entities_of_type(self, entity_type):
            return self._inner.entities_of_type(entity_type)

        def record_evaluation_run(self, **kwargs):
            raise sqlite3.OperationalError("disk full")

    inner = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    result = run_evaluation(
        workspace,
        as_of=AS_OF,
        boundary=BOUNDARY,
        store=UnrecordableStore(inner),
    )

    assert not result.ok
    assert result.report is not None
    assert result.recorded is False
    assert "EVALUATION.STORE_ERROR" in [
        d.code for d in result.diagnostics
    ]
    assert get_evaluation_run(workspace, result.run_id) is None


def test_empty_and_partial_cohorts(tmp_path: Path) -> None:
    # An empty after cohort leaves target unverified; both empty leaves
    # change unavailable — honest unavailability, not a zero claim.
    workspace = make_evaluated_ws(
        tmp_path,
        "id,status,assignee,amount,completed_at,invoiced_at\n"
        "B1,completed,taro,1,2026-09-21,2026-09-30\n",
    )

    result = run_evaluation(workspace, as_of=AS_OF, boundary=BOUNDARY)

    assert result.ok
    report = result.report
    assert report["cohorts"]["baseline"]["aggregate"] == 9.0
    assert report["cohorts"]["after"]["aggregate"] is None
    assert report["change"]["status"] == "unavailable"
    assert report["target"]["status"] == "unverified"
    assert report["overall"]["status"] == "unverified"


def test_naive_as_of_and_boundary_are_diagnostics(tmp_path: Path) -> None:
    # INV-002: both times must be explicit and aware — never guessed.
    workspace = make_evaluated_ws(tmp_path)

    naive = datetime(2026, 10, 15)
    for kwargs in (
        {"as_of": naive, "boundary": BOUNDARY},
        {"as_of": AS_OF, "boundary": naive},
    ):
        result = run_evaluation(workspace, **kwargs)
        assert not result.ok
        assert result.run_id is None
        assert not result.recorded

    codes = [
        d.code
        for d in run_evaluation(
            workspace, as_of=naive, boundary=BOUNDARY
        ).diagnostics
    ]
    assert "EVALUATION.NAIVE_AS_OF" in codes
    codes = [
        d.code
        for d in run_evaluation(
            workspace, as_of=AS_OF, boundary=naive
        ).diagnostics
    ]
    assert "EVALUATION.NAIVE_BOUNDARY" in codes


def test_boundary_after_as_of_is_an_error(tmp_path: Path) -> None:
    workspace = make_evaluated_ws(tmp_path)

    result = run_evaluation(
        workspace,
        as_of=BOUNDARY,
        boundary=AS_OF,
    )

    assert not result.ok
    assert "EVALUATION.BOUNDARY_AFTER_AS_OF" in [
        d.code for d in result.diagnostics
    ]
    assert not result.recorded


def test_frozen_report_is_stable(tmp_path: Path) -> None:
    # INV-007: a recorded run is inspectable and unchanged by later
    # ingestion; a failed run is never recorded.
    workspace = make_evaluated_ws(tmp_path)
    first = run_evaluation(workspace, as_of=AS_OF, boundary=BOUNDARY)
    assert first.ok and first.recorded

    later = write_snapshot(
        tmp_path,
        "jobs-later.csv",
        "id,status,assignee,amount,completed_at,invoiced_at\n"
        "A4,completed,hanako,1,2026-10-08,2026-10-10\n",
    )
    assert ingest_snapshot(workspace, later, observed_at=FIXED_NOW).ok

    frozen = get_evaluation_run(workspace, first.run_id)
    assert frozen is not None
    assert frozen["report"] == first.report
    assert frozen["contract_digest"] == first.contract_digest
    assert frozen["boundary"] == "2026-10-05T00:00:00Z"

    # The append-only triggers protect the frozen report.
    db = sqlite3.connect(Path(workspace.path) / RUNTIME_DIR / "canon.db")
    try:
        try:
            db.execute(
                "UPDATE evaluation_runs SET report = '{}' WHERE run_id = ?",
                (first.run_id,),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("evaluation_runs accepted an UPDATE")
        try:
            db.execute(
                "DELETE FROM evaluation_runs WHERE run_id = ?",
                (first.run_id,),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("evaluation_runs accepted a DELETE")
    finally:
        db.close()


def test_store_and_trace_failures_become_diagnostics(tmp_path: Path) -> None:
    # INV-008: store failures are diagnostics and failed runs are never
    # recorded; a trace failure degrades to a warning.
    workspace = make_evaluated_ws(tmp_path)

    class LockedStore:
        def subject_ids_with_event(self, event, subject):
            raise sqlite3.OperationalError("database is locked")

    result = run_evaluation(
        workspace, as_of=AS_OF, boundary=BOUNDARY, store=LockedStore()
    )
    assert not result.ok
    assert "EVALUATION.STORE_ERROR" in [
        d.code for d in result.diagnostics
    ]
    assert not result.recorded
    assert get_evaluation_run(workspace, result.run_id) is None

    class FailingTrace:
        def emit(self, stage, kind, data):
            raise OSError("no space left")

    traced = run_evaluation(
        workspace,
        as_of=AS_OF,
        boundary=BOUNDARY,
        trace=FailingTrace(),
    )
    assert traced.ok
    assert "EVALUATION.TRACE_ERROR" in [
        d.code for d in traced.diagnostics
    ]
    assert traced.recorded


def test_evaluation_is_deterministic(tmp_path: Path) -> None:
    # INV-001: identical inputs produce identical reports — the only
    # per-run values live outside the report payload (run id, created_at).
    workspace = make_evaluated_ws(tmp_path)

    first = run_evaluation(workspace, as_of=AS_OF, boundary=BOUNDARY)
    second = run_evaluation(workspace, as_of=AS_OF, boundary=BOUNDARY)

    assert first.ok and second.ok
    assert first.report == second.report
    assert first.contract_digest == second.contract_digest
    assert first.run_id != second.run_id


def test_report_has_no_causal_claim(tmp_path: Path) -> None:
    # INV-006: the report describes time-based cohorts and never names a
    # proposal or intervention as the cause of the observed change.
    workspace = make_evaluated_ws(tmp_path)

    result = run_evaluation(workspace, as_of=AS_OF, boundary=BOUNDARY)

    assert result.ok
    assert "does not establish that any intervention caused it" in (
        result.report["note"]
    )
    serialized = str(result.report)
    assert "proposal" not in serialized
    assert "reminder" not in serialized
    assert "caused the observed change" not in serialized
