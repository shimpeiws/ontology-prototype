"""Projection tests: threshold boundaries, frozen runs, diagnostics."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import support

from ontology_core import (
    SqliteCanonicalStore,
    get_projection_run,
    ingest_snapshot,
    load_workspace,
    run_projection,
)
from ontology_core.evidence import RUNTIME_DIR

FIXED_NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
AS_OF = datetime(2026, 10, 5, 0, 0, 0, tzinfo=timezone.utc)

# A01 completed 4 days before as_of (candidate), B02 exactly 2 days
# (excluded, strict >), C03 1 day (excluded), D04 invoiced (excluded),
# E05 in progress (no start event), F06 completed after as_of (future).
CSV_SAMPLE = (
    "id,status,assignee,amount,completed_at,invoiced_at\n"
    "A01,completed,taro,1,2026-10-01,\n"
    "B02,completed,taro,1,2026-10-03,\n"
    "C03,completed,taro,1,2026-10-04,\n"
    "D04,completed,hanako,1,2026-10-01,2026-10-04\n"
    "E05,in_progress,hanako,1,,\n"
    "F06,completed,taro,1,2026-10-06,\n"
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


def make_projected_ws(tmp_path: Path):
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)
    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)
    assert result.ok, [d.format() for d in result.diagnostics]
    return workspace


def test_projection_threshold_boundaries(tmp_path: Path) -> None:
    workspace = make_projected_ws(tmp_path)

    result = run_projection(
        workspace, "CompletedButNotInvoiced", as_of=AS_OF
    )

    # INV-007: strict two-day threshold — A01 (4d) in, B02 (exactly 2d) out,
    # C03 (1d) out, D04 (invoiced) out, F06 (future-dated) out with a
    # diagnostic.
    assert result.ok, [d.format() for d in result.diagnostics]
    assert [c.subject_id for c in result.candidates] == ["A01"]
    codes = [d.code for d in result.diagnostics]
    assert "PROJECTION.FUTURE_EVENT" in codes

    candidate = result.candidates[0]
    assert candidate.occurred_at == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert candidate.elapsed_seconds == 4 * 86400
    assert candidate.properties["assignee"] == "taro"
    assert candidate.evidence["fact_key"].startswith("fact-")
    assert candidate.evidence["observation_ids"]
    assert candidate.evidence["ingest_ids"]

    # Inputs record every subject examined and why it was excluded.
    inputs = {i["subject_id"]: i for i in result.inputs}
    assert set(inputs) == {"A01", "B02", "C03", "D04", "F06"}
    assert inputs["B02"]["excluded"] == "elapsed within threshold"
    assert inputs["D04"]["excluded"] == "interval end event exists"
    assert inputs["F06"]["excluded"] == "future-dated start event"


def test_frozen_run_is_stable_after_later_ingestion(tmp_path: Path) -> None:
    workspace = make_projected_ws(tmp_path)
    first = run_projection(workspace, "CompletedButNotInvoiced", as_of=AS_OF)
    assert first.run_id is not None and first.recorded

    # INV-009: later ingestion must not rewrite a recorded run.
    later = write_snapshot(
        tmp_path,
        "jobs-later.csv",
        "id,status,assignee,amount,completed_at,invoiced_at\n"
        "A01,completed,taro,1,2026-10-01,2026-10-05\n",
    )
    assert ingest_snapshot(workspace, later, observed_at=FIXED_NOW).ok

    frozen = get_projection_run(workspace, first.run_id)
    assert frozen is not None
    assert [c["subject"]["id"] for c in frozen["candidates"]] == ["A01"]
    assert frozen["definition_digest"] == first.definition_digest

    # A fresh run now sees the invoice and selects nothing.
    second = run_projection(workspace, "CompletedButNotInvoiced", as_of=AS_OF)
    assert second.candidates == []


def test_end_event_after_as_of_does_not_exclude(tmp_path: Path) -> None:
    # An invoice issued *after* as_of does not change what was true at as_of:
    # the job was still completed-but-uninvoiced then.
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(
        tmp_path,
        "jobs.csv",
        "id,status,assignee,amount,completed_at,invoiced_at\n"
        "J01,completed,taro,1,2026-10-01,2026-10-10\n",
    )
    assert ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW).ok

    result = run_projection(workspace, "CompletedButNotInvoiced", as_of=AS_OF)
    assert result.ok
    assert [c.subject_id for c in result.candidates] == ["J01"]
    inputs = {i["subject_id"]: i for i in result.inputs}
    assert inputs["J01"]["future_end_fact_keys"]

    # Past as_of after the invoice date excludes the job again.
    later = run_projection(
        workspace,
        "CompletedButNotInvoiced",
        as_of=datetime(2026, 10, 12, tzinfo=timezone.utc),
    )
    assert later.ok
    assert later.candidates == []
    later_inputs = {i["subject_id"]: i for i in later.inputs}
    assert later_inputs["J01"]["excluded"] == "interval end event exists"


def test_candidate_marks_property_provenance(tmp_path: Path) -> None:
    workspace = make_projected_ws(tmp_path)
    result = run_projection(workspace, "CompletedButNotInvoiced", as_of=AS_OF)

    assert result.ok
    candidate = result.candidates[0]
    # Properties are the latest observed state, stamped with its update time
    # so consumers can tell it may postdate as_of.
    assert candidate.evidence["entity_updated_at"] == "2026-10-05T12:00:00Z"


def test_projection_requires_aware_as_of(tmp_path: Path) -> None:
    workspace = make_projected_ws(tmp_path)

    result = run_projection(
        workspace,
        "CompletedButNotInvoiced",
        as_of=datetime(2026, 10, 5, 0, 0, 0),
    )

    assert not result.ok
    assert "PROJECTION.NAIVE_AS_OF" in [d.code for d in result.diagnostics]
    assert result.run_id is None


def test_unknown_and_unsupported_projections(tmp_path: Path) -> None:
    workspace = make_projected_ws(tmp_path)

    unknown = run_projection(workspace, "Nope", as_of=AS_OF)
    assert "PROJECTION.UNKNOWN_PROJECTION" in [
        d.code for d in unknown.diagnostics
    ]

    # A declared projection without registered semantics is rejected too.
    ws_dir = Path(workspace.path)
    docs = support.base_docs()
    docs["domain"]["domain"]["projections"]["Other"] = {"subject": "Job"}
    other_dir = support.make_workspace(tmp_path / "other", docs)
    loaded = load_workspace(other_dir)
    assert loaded.ok
    result = run_projection(loaded.workspace, "Other", as_of=AS_OF)
    assert "PROJECTION.UNSUPPORTED_PROJECTION" in [
        d.code for d in result.diagnostics
    ]
    assert ws_dir != other_dir


def test_get_unknown_run_returns_none(tmp_path: Path) -> None:
    workspace = make_projected_ws(tmp_path)
    assert get_projection_run(workspace, "prj-nonexistent") is None


def test_projection_uses_explicit_store(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    store = SqliteCanonicalStore(tmp_path / "elsewhere")
    result = run_projection(
        workspace, "CompletedButNotInvoiced", as_of=AS_OF, store=store
    )
    assert result.ok
    # A clean run on an empty store emits no diagnostics at all — a silently
    # swallowed warning would show up here.
    assert result.diagnostics == []
    assert result.candidates == []
    assert result.recorded
    assert (tmp_path / "elsewhere" / "canon.db").exists()
    assert not (Path(workspace.path) / RUNTIME_DIR / "canon.db").exists()


def test_projection_read_failure_becomes_diagnostic(tmp_path: Path) -> None:
    workspace = make_projected_ws(tmp_path)

    class LockedStore:
        def subject_ids_with_event(self, event, subject):
            raise sqlite3.OperationalError("database is locked")

    result = run_projection(
        workspace,
        "CompletedButNotInvoiced",
        as_of=AS_OF,
        store=LockedStore(),
    )

    # Store read failures are diagnostics, not tracebacks through the CLI.
    assert not result.ok
    assert "PROJECTION.STORE_ERROR" in [d.code for d in result.diagnostics]
    assert not result.recorded
    assert get_projection_run(workspace, result.run_id) is None


def test_subject_mismatch_run_is_not_recorded(tmp_path: Path) -> None:
    # A projection subject that disagrees with the interval event subjects
    # fails the run — and a failed run is never frozen, so a recorded run
    # always means "evaluated successfully".
    docs = support.base_docs()
    docs["domain"]["domain"]["projections"]["CompletedButNotInvoiced"][
        "subject"
    ] = "Person"
    ws_dir = support.make_workspace(tmp_path / "ws", docs)
    loaded = load_workspace(ws_dir)
    assert loaded.ok, [d.format() for d in loaded.diagnostics]
    assert loaded.workspace is not None

    result = run_projection(
        loaded.workspace, "CompletedButNotInvoiced", as_of=AS_OF
    )

    assert not result.ok
    assert "PROJECTION.SUBJECT_MISMATCH" in [
        d.code for d in result.diagnostics
    ]
    assert not result.recorded
    assert get_projection_run(loaded.workspace, result.run_id) is None


def test_projection_record_failure_becomes_diagnostic(tmp_path: Path) -> None:
    workspace = make_projected_ws(tmp_path)

    class RecordingFailsStore:
        def __init__(self, inner):
            self.inner = inner

        def subject_ids_with_event(self, event, subject):
            return self.inner.subject_ids_with_event(event, subject)

        def events_for_subject(self, entity, entity_id):
            return self.inner.events_for_subject(entity, entity_id)

        def event_links(self, fact_key):
            return self.inner.event_links(fact_key)

        def entity_state(self, entity, entity_id):
            return self.inner.entity_state(entity, entity_id)

        def record_projection_run(self, **kwargs):
            raise sqlite3.OperationalError("readonly database")

    store = RecordingFailsStore(
        SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    )
    result = run_projection(
        workspace, "CompletedButNotInvoiced", as_of=AS_OF, store=store
    )

    assert not result.ok
    assert "PROJECTION.STORE_ERROR" in [d.code for d in result.diagnostics]
    assert not result.recorded


def test_projection_trace_error_is_warning(tmp_path: Path) -> None:
    workspace = make_projected_ws(tmp_path)

    class FailingTrace:
        def emit(self, stage, kind, data):
            raise OSError("no space left")

    result = run_projection(
        workspace, "CompletedButNotInvoiced", as_of=AS_OF, trace=FailingTrace()
    )

    # A trace failure degrades to a warning; the run still succeeds.
    assert result.ok
    assert "PROJECTION.TRACE_ERROR" in [d.code for d in result.diagnostics]
    assert result.recorded


def test_definition_digest_changes_with_contract(tmp_path: Path) -> None:
    # INV-009: a contract edit changes the digest even when the candidate
    # list is identical — frozen runs record which definition produced them.
    workspace = make_projected_ws(tmp_path)
    first = run_projection(workspace, "CompletedButNotInvoiced", as_of=AS_OF)
    assert first.ok

    docs = support.base_docs()
    docs["evaluation"]["evaluation"]["baseline"] = {"value": 8}
    other_dir = support.make_workspace(tmp_path / "other", docs)
    loaded = load_workspace(other_dir)
    assert loaded.ok and loaded.workspace is not None
    snapshot = write_snapshot(tmp_path, "jobs-2.csv", CSV_SAMPLE)
    assert ingest_snapshot(
        loaded.workspace, snapshot, observed_at=FIXED_NOW
    ).ok

    second = run_projection(
        loaded.workspace, "CompletedButNotInvoiced", as_of=AS_OF
    )
    assert second.ok
    assert second.definition_digest != first.definition_digest
    assert [c.subject_id for c in second.candidates] == ["A01"]
