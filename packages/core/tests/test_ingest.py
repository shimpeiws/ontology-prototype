"""Ingestion tests: normalization boundary, evidence, diagnostics, trace."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import support

from ontology_core import ingest_snapshot, load_workspace
from ontology_core.evidence import RUNTIME_DIR

FIXED_NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)

CSV_SAMPLE = (
    "id,status,assignee,amount,completed_at,invoiced_at\n"
    "0101,completed,taro,42.5,2026-10-01,\n"
    "102,in_progress,hanako,,2026-10-02,\n"
)

JSON_SAMPLE = json.dumps(
    [
        {
            "id": "0101",
            "status": "completed",
            "assignee": "taro",
            "amount": 42.5,
            "completed_at": "2026-10-01",
            "invoiced_at": None,
        },
        {
            "id": "102",
            "status": "in_progress",
            "assignee": "hanako",
            "amount": None,
            "completed_at": "2026-10-02",
            "invoiced_at": None,
        },
    ]
)


def make_ws(tmp_path: Path, *, source_format: str = "csv") -> Path:
    docs = support.base_docs()
    docs["mapping"]["mapping"]["source"]["format"] = source_format
    ws_dir = support.make_workspace(tmp_path / "ws", docs)
    result = load_workspace(ws_dir)
    assert result.ok, [d.format() for d in result.diagnostics]
    assert result.workspace is not None
    return result.workspace


def write_snapshot(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def codes_of(observation) -> list[str]:
    return [d.code for d in observation.diagnostics]


def test_csv_snapshot_normalizes_records(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert result.ok, [d.format() for d in result.diagnostics]
    assert len(result.observations) == 2
    assert all(o.status == "accepted" for o in result.observations)

    first = result.observations[0]
    # INV-001: leading zeros are preserved; ids are opaque strings.
    job = next(e for e in first.entities if e.entity == "Job")
    assert job.id == "0101"
    assert job.properties["status"] == "completed"
    # INV-002: a blank CSV cell normalizes to null.
    assert first.record["invoiced_at"] is None
    # casts produce typed values
    assert job.properties["amount"] == 42.5

    # INV-003: a date-only occurred_at means midnight in date_only_timezone.
    completed = next(e for e in first.events if e.event == "JobCompleted")
    assert completed.occurred_at == datetime(2026, 10, 1, tzinfo=timezone.utc)
    # The event's observed_at falls back to the run timestamp.
    assert completed.observed_at == FIXED_NOW
    # A blank gated field means the event simply did not happen.
    assert not any(e.event == "InvoiceIssued" for e in first.events)

    second = result.observations[1]
    assert not any(e.event == "JobCompleted" for e in second.events)


def test_csv_and_json_records_normalize_identically(tmp_path: Path) -> None:
    csv_ws = make_ws(tmp_path / "csv", source_format="csv")
    json_ws = make_ws(tmp_path / "json", source_format="json")
    csv_snapshot = write_snapshot(tmp_path / "csv", "jobs.csv", CSV_SAMPLE)
    json_snapshot = write_snapshot(tmp_path / "json", "jobs.json", JSON_SAMPLE)

    csv_result = ingest_snapshot(csv_ws, csv_snapshot, observed_at=FIXED_NOW)
    json_result = ingest_snapshot(json_ws, json_snapshot, observed_at=FIXED_NOW)

    assert csv_result.ok and json_result.ok
    for csv_obs, json_obs in zip(csv_result.observations, json_result.observations):
        # INV-002: equivalent records produce identical normalized outputs.
        # The raw normalized record keeps format-specific scalars (CSV strings
        # vs typed JSON values); resolved entities/events must be identical.
        assert set(csv_obs.record) == set(json_obs.record)
        assert csv_obs.record["invoiced_at"] is json_obs.record["invoiced_at"] is None
        assert csv_obs.entities == json_obs.entities
        assert csv_obs.events == json_obs.events


def test_missing_id_rejects_record(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(
        tmp_path, "jobs.csv", "id,status,assignee\n,completed,taro\n"
    )

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert not result.ok
    (obs,) = result.observations
    assert obs.status == "rejected"
    assert obs.entities == []
    assert "INGEST.MISSING_ID" in codes_of(obs)
    diagnostic = next(d for d in obs.diagnostics if d.code == "INGEST.MISSING_ID")
    # INV-004: diagnostics carry source file, record index, and field.
    assert diagnostic.file == "jobs.csv"
    assert diagnostic.path == "record[1].id"


def test_json_numeric_id_rejected(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path, source_format="json")
    snapshot = write_snapshot(
        tmp_path,
        "jobs.json",
        json.dumps([{"id": 101, "status": "completed", "assignee": "taro"}]),
    )

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert not result.ok
    assert "INGEST.INVALID_ID" in codes_of(result.observations[0])


def test_missing_required_property_rejects_record(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(
        tmp_path, "jobs.csv", "id,status,assignee\n101,,taro\n"
    )

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert not result.ok
    assert "INGEST.MISSING_REQUIRED" in codes_of(result.observations[0])


def test_malformed_timestamp_rejects_record(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(
        tmp_path,
        "jobs.csv",
        "id,status,assignee,completed_at,invoiced_at\n"
        "101,completed,taro,2026-10-01,not-a-date\n",
    )

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert not result.ok
    (obs,) = result.observations
    assert obs.status == "rejected"
    diagnostic = next(
        d for d in obs.diagnostics if d.code == "INGEST.INVALID_TIMESTAMP"
    )
    assert diagnostic.path == "record[1].invoiced_at"


def test_naive_datetime_rejected(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(
        tmp_path,
        "jobs.csv",
        "id,status,assignee,completed_at\n101,completed,taro,2026-10-01 09:30:00\n",
    )

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert not result.ok
    diagnostic = next(
        d
        for d in result.observations[0].diagnostics
        if d.code == "INGEST.INVALID_TIMESTAMP"
    )
    assert diagnostic.path == "record[1].completed_at"


def test_offset_datetime_normalizes_to_utc(tmp_path: Path) -> None:
    docs = support.base_docs()
    docs["mapping"]["mapping"]["events"][0]["occurred_at"]["as"] = "datetime"
    ws_dir = support.make_workspace(tmp_path / "ws", docs)
    workspace = load_workspace(ws_dir).workspace
    assert workspace is not None
    snapshot = write_snapshot(
        tmp_path,
        "jobs.csv",
        "id,status,assignee,completed_at\n101,completed,taro,2026-10-01T09:30:00+09:00\n",
    )

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert result.ok, [d.format() for d in result.diagnostics]
    completed = result.observations[0].events[0]
    assert completed.occurred_at == datetime(2026, 10, 1, 0, 30, tzinfo=timezone.utc)


def test_ungated_event_with_null_occurred_at_rejects(tmp_path: Path) -> None:
    docs = support.base_docs()
    del docs["mapping"]["mapping"]["events"][1]["when"]
    ws_dir = support.make_workspace(tmp_path / "ws", docs)
    workspace = load_workspace(ws_dir).workspace
    assert workspace is not None
    snapshot = write_snapshot(
        tmp_path, "jobs.csv", "id,status,assignee,invoiced_at\n101,completed,taro,\n"
    )

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert not result.ok
    assert "INGEST.MISSING_TIME" in codes_of(result.observations[0])


def test_invalid_row_shapes_rejected(tmp_path: Path) -> None:
    csv_ws = make_ws(tmp_path / "csv", source_format="csv")
    csv_snapshot = write_snapshot(
        tmp_path / "csv", "jobs.csv", "id,status\n101,completed,extra-cell\n"
    )
    csv_result = ingest_snapshot(csv_ws, csv_snapshot, observed_at=FIXED_NOW)
    assert "INGEST.INVALID_ROW" in codes_of(csv_result.observations[0])

    json_ws = make_ws(tmp_path / "json", source_format="json")
    json_snapshot = write_snapshot(tmp_path / "json", "jobs.json", '[{"id": "1"}, 42]')
    json_result = ingest_snapshot(json_ws, json_snapshot, observed_at=FIXED_NOW)
    assert "INGEST.INVALID_ROW" in codes_of(json_result.observations[1])

    nested_ws = make_ws(tmp_path / "nested", source_format="json")
    nested_snapshot = write_snapshot(
        tmp_path / "nested",
        "jobs.json",
        json.dumps([{"id": "1", "status": {"bad": "shape"}}]),
    )
    nested_result = ingest_snapshot(nested_ws, nested_snapshot, observed_at=FIXED_NOW)
    assert "INGEST.NON_SCALAR" in codes_of(nested_result.observations[0])


def test_source_read_failures_become_diagnostics(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)

    missing = ingest_snapshot(workspace, tmp_path / "nope.csv", observed_at=FIXED_NOW)
    assert not missing.ok
    assert any(d.code == "INGEST.UNREADABLE_SOURCE" for d in missing.diagnostics)
    assert missing.observations == []

    bad_utf8 = tmp_path / "bad.csv"
    bad_utf8.write_bytes(b"id,status\n101,\xff\xfe\n")
    bad = ingest_snapshot(workspace, bad_utf8, observed_at=FIXED_NOW)
    assert not bad.ok
    assert any(d.code == "INGEST.ENCODING_ERROR" for d in bad.diagnostics)


def test_malformed_json_source(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path, source_format="json")
    snapshot = write_snapshot(tmp_path, "jobs.json", "not json {")

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert not result.ok
    assert any(d.code == "INGEST.INVALID_SOURCE" for d in result.diagnostics)


def test_duplicate_csv_columns_fail(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(
        tmp_path, "jobs.csv", "id,id,status\n101,101,completed\n"
    )

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert not result.ok
    assert any(d.code == "INGEST.DUPLICATE_COLUMN" for d in result.diagnostics)


def test_observation_carries_evidence_fields(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    # INV-006: every observation records the full evidence envelope.
    (obs,) = [o for o in result.observations if o.row == 1]
    assert obs.observation_id.startswith("obs-")
    assert obs.ingest_id == result.ingest_id
    assert obs.source == "jobs"
    assert obs.observed_at == FIXED_NOW
    assert obs.content_digest == result.content_digest
    assert obs.content_digest.startswith("sha256:")
    assert obs.mapping_digest == result.mapping_digest
    assert obs.mapping_digest.startswith("sha256:")


def test_evidence_survives_source_mutation(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)
    assert result.ok

    snapshot_path = result.snapshot_path
    observations_path = result.observations_path
    assert snapshot_path is not None and observations_path is not None
    assert snapshot_path.read_bytes() == CSV_SAMPLE.encode("utf-8")

    # INV-005: the external source changes; stored evidence does not.
    snapshot.write_text("id,status\n999,deleted\n", encoding="utf-8")
    assert snapshot_path.read_bytes() == CSV_SAMPLE.encode("utf-8")
    lines = observations_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    stored = json.loads(lines[0])
    assert stored["record"]["id"] == "0101"


def test_reingest_does_not_duplicate_snapshot(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)

    first = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)
    later = datetime(2026, 10, 6, tzinfo=timezone.utc)
    second = ingest_snapshot(workspace, snapshot, observed_at=later)

    # INV-009: a repeat run is a new ingest id with identical resolved content.
    assert first.ingest_id != second.ingest_id
    assert first.snapshot_path == second.snapshot_path
    snapshots = list((workspace.path / RUNTIME_DIR / "snapshots").iterdir())
    assert len(snapshots) == 1
    for a, b in zip(first.observations, second.observations):
        assert a.record == b.record
        assert a.entities == b.entities
        assert a.observation_id != b.observation_id


def test_trace_records_accepted_and_rejected(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(
        tmp_path,
        "jobs.csv",
        "id,status,assignee,completed_at\n"
        "101,completed,taro,2026-10-01\n"
        ",completed,hanako,\n",
    )

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    trace_path = workspace.path / RUNTIME_DIR / "trace.jsonl"
    events = [json.loads(line) for line in trace_path.read_text().splitlines()]
    kinds = [e["kind"] for e in events]
    assert kinds.count("observation.accepted") == 1
    assert kinds.count("observation.rejected") == 1
    assert kinds[-1] == "ingest.completed"
    rejected = next(e for e in events if e["kind"] == "observation.rejected")
    assert "INGEST.MISSING_ID" in rejected["data"]["codes"]
    completed = events[-1]
    assert completed["data"]["accepted"] == 1
    assert completed["data"]["rejected"] == 1
    assert result.observations_path is not None


def test_runtime_dir_is_gitignored() -> None:
    gitignore = (support.REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".ontology/" in gitignore.split()


def test_null_trace_sink_does_not_break_ingestion(tmp_path: Path) -> None:
    from ontology_core import NullTraceSink

    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)

    result = ingest_snapshot(
        workspace, snapshot, observed_at=FIXED_NOW, trace=NullTraceSink()
    )
    assert result.ok


def test_empty_snapshot_warns_not_fails(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", "id,status,assignee\n")

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert result.ok
    assert result.observations == []
    assert any(d.code == "INGEST.EMPTY" for d in result.diagnostics)


def test_huge_json_integer_is_rejected_not_raised(tmp_path: Path) -> None:
    # Regression: float(10**400) raises OverflowError, which used to escape
    # ingest_snapshot and lose the whole run's evidence.
    workspace = make_ws(tmp_path, source_format="json")
    snapshot = write_snapshot(
        tmp_path,
        "jobs.json",
        json.dumps(
            [{"id": "101", "status": "completed", "assignee": "taro", "amount": 10**400}]
        ),
    )

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert not result.ok
    (obs,) = result.observations
    assert obs.status == "rejected"
    assert "INGEST.INVALID_VALUE" in codes_of(obs)


def test_huge_integer_in_when_condition_does_not_raise(tmp_path: Path) -> None:
    # The same OverflowError escaped through the numeric coercion used by
    # `when` comparisons.
    docs = support.base_docs()
    docs["mapping"]["mapping"]["source"]["format"] = "json"
    del docs["mapping"]["mapping"]["entities"][0]["properties"]["amount"]
    docs["mapping"]["mapping"]["events"][1]["when"] = {
        "op": "gt",
        "field": "amount",
        "value": 0,
    }
    workspace = load_workspace(support.make_workspace(tmp_path / "ws", docs)).workspace
    assert workspace is not None
    snapshot = write_snapshot(
        tmp_path,
        "jobs.json",
        json.dumps(
            [{"id": "101", "status": "ok", "assignee": "taro", "amount": 10**400}]
        ),
    )

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert result.ok, [d.format() for d in result.diagnostics]
    (obs,) = result.observations
    assert obs.status == "accepted"
    # The huge value is non-numeric for comparisons; the gated event is off.
    assert not any(e.event == "InvoiceIssued" for e in obs.events)


def test_long_digit_string_integer_cast_is_rejected(tmp_path: Path) -> None:
    # int() on digit strings beyond the platform limit raises ValueError;
    # it must surface as INVALID_VALUE, not crash the run.
    docs = support.base_docs()
    docs["domain"]["domain"]["entities"]["Job"]["properties"]["seq"] = {
        "type": "integer"
    }
    docs["mapping"]["mapping"]["entities"][0]["properties"]["seq"] = {
        "field": "seq",
        "as": "integer",
    }
    workspace = load_workspace(support.make_workspace(tmp_path / "ws", docs)).workspace
    assert workspace is not None
    snapshot = write_snapshot(
        tmp_path,
        "jobs.csv",
        f"id,status,assignee,seq\n101,completed,taro,{'9' * 5000}\n",
    )

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert not result.ok
    assert "INGEST.INVALID_VALUE" in codes_of(result.observations[0])


def test_date_cast_rejects_non_extended_formats(tmp_path: Path) -> None:
    # `as: date` accepts strict YYYY-MM-DD only: datetime strings, ISO basic
    # format, and week dates all produce INVALID_TIMESTAMP diagnostics.
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(
        tmp_path,
        "jobs.csv",
        "id,status,assignee,completed_at\n"
        "101,completed,taro,2026-10-01T09:30:00Z\n"
        "102,completed,taro,20261001\n"
        "103,completed,taro,2026-W40-1\n",
    )

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert not result.ok
    assert all(o.status == "rejected" for o in result.observations)
    for obs in result.observations:
        assert "INGEST.INVALID_TIMESTAMP" in codes_of(obs)


def test_id_with_surrounding_whitespace_is_preserved_with_warning(
    tmp_path: Path,
) -> None:
    # Ids stay verbatim (opaque strings); padding emits a warning so the
    # likely-duplicate entity is visible without fabricating a trimmed id.
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(
        tmp_path,
        "jobs.csv",
        "id,status,assignee,completed_at\n101 ,completed,taro,2026-10-01\n",
    )

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert result.ok, [d.format() for d in result.diagnostics]
    (obs,) = result.observations
    assert obs.status == "accepted"
    job = next(e for e in obs.entities if e.entity == "Job")
    assert job.id == "101 "
    assert any(
        d.code == "INGEST.SUSPICIOUS_ID" and d.severity == "warning"
        for d in obs.diagnostics
    )


def test_same_observed_at_second_run_does_not_clobber_evidence(
    tmp_path: Path,
) -> None:
    # Replaying an identical snapshot with an identical observed_at must still
    # produce a distinct run: separate observation files and observation ids.
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)

    first = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)
    second = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert first.ok and second.ok
    assert first.ingest_id != second.ingest_id
    observations_dir = workspace.path / RUNTIME_DIR / "observations"
    assert len(list(observations_dir.iterdir())) == 2
    assert first.observations_path != second.observations_path
    for a, b in zip(first.observations, second.observations):
        assert a.observation_id != b.observation_id


def test_observation_write_never_overwrites(tmp_path: Path) -> None:
    from ontology_core.evidence import FileEvidenceStore

    store = FileEvidenceStore(tmp_path / "rt")
    store.write_observations("run-1", ["{}"])
    with pytest.raises(FileExistsError):
        store.write_observations("run-1", ["{}"])


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)
    assert result.ok

    leftovers = [
        p
        for p in (workspace.path / RUNTIME_DIR).rglob("*")
        if p.is_file() and p.suffix == ".tmp"
    ]
    assert leftovers == []


def test_preexisting_tmp_decoy_is_not_touched(tmp_path: Path) -> None:
    # A pre-planted temp-named file must not be consumed or corrupted by the
    # atomic write path (the temp name is now unpredictable).
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)
    snapshots_dir = workspace.path / RUNTIME_DIR / "snapshots"
    snapshots_dir.mkdir(parents=True)
    decoy = snapshots_dir / "sha256-decoy.csv.tmp-1"
    decoy.write_text("attacker controlled", encoding="utf-8")

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert result.ok
    assert decoy.read_text(encoding="utf-8") == "attacker controlled"


def test_trace_write_failure_is_reported_as_diagnostic(tmp_path: Path) -> None:
    class FailingSink:
        def emit(self, stage, kind, data):
            raise OSError("read-only trace file")

    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)

    result = ingest_snapshot(
        workspace, snapshot, observed_at=FIXED_NOW, trace=FailingSink()
    )

    # No exception escapes; the run completes and reports the store error.
    assert not result.ok
    assert len(result.observations) == 2
    store_errors = [
        d for d in result.diagnostics if d.code == "INGEST.STORE_ERROR"
    ]
    assert len(store_errors) == 1  # reported once, not once per record
