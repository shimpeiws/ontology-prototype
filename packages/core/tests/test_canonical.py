"""Canonical store tests: append-only events, fact keys, atomic apply."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
import support

from ontology_core import (
    FileEvidenceStore,
    Observation,
    ResolvedEntity,
    ResolvedEvent,
    SqliteCanonicalStore,
    ingest_snapshot,
    load_workspace,
)
from ontology_core.evidence import RUNTIME_DIR

FIXED_NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)

CSV_SAMPLE = (
    "id,status,assignee,amount,completed_at,invoiced_at\n"
    "0101,completed,taro,42.5,2026-10-01,\n"
    "102,in_progress,hanako,,2026-10-02,\n"
)


def make_ws(tmp_path: Path) -> "object":
    ws_dir = support.make_workspace(tmp_path / "ws")
    result = load_workspace(ws_dir)
    assert result.ok, [d.format() for d in result.diagnostics]
    assert result.workspace is not None
    return result.workspace


def write_snapshot(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def open_store(workspace) -> SqliteCanonicalStore:
    return SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)


def count_rows(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_ingest_persists_canonical_state(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    assert result.ok, [d.format() for d in result.diagnostics]
    assert result.canonical is not None
    assert result.canonical.events_appended == 1
    assert result.canonical.events_reobserved == 0

    store = open_store(workspace)
    job = store.entity_state("Job", "0101")
    assert job is not None
    assert job["properties"]["status"] == "completed"
    assert job["properties"]["assignee"] == "taro"
    assert job["properties"]["amount"] == 42.5
    assert store.entity_state("Person", "taro")["properties"]["name"] == "taro"
    # Job 102 is in progress: entity exists, no events.
    assert store.entity_state("Job", "102") is not None
    assert store.events_for_subject("Job", "102") == []

    facts = store.events_for_subject("Job", "0101")
    assert [f.event for f in facts] == ["JobCompleted"]
    # INV-005: occurrence and observation times stay distinct; the event
    # occurred at midnight UTC (as: date) and was observed at ingest time.
    assert facts[0].occurred_at == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert facts[0].observed_at == FIXED_NOW


def test_reingest_does_not_duplicate_events(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)

    first = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)
    second = ingest_snapshot(workspace, snapshot, observed_at=LATER)

    assert second.ok
    # INV-002: the fact key depends on event/subject/occurred_at only, so the
    # second run appends nothing.
    assert second.canonical is not None
    assert second.canonical.events_appended == 0
    assert second.canonical.events_reobserved == 1
    assert len(store_facts(workspace, "Job", "0101")) == 1

    # INV-003: the repeat observation links to the existing fact.
    store = open_store(workspace)
    fact = store_facts(workspace, "Job", "0101")[0]
    links = store.event_links(fact.fact_key)
    assert len(links) == 2
    assert {obs for obs, _ in links} == {
        first.observations[0].observation_id,
        second.observations[0].observation_id,
    }
    # And the fact keeps its first observation metadata.
    assert fact.ingest_id == first.ingest_id


def store_facts(workspace, entity: str, entity_id: str):
    return open_store(workspace).events_for_subject(entity, entity_id)


def test_events_are_append_only(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)
    assert ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW).ok

    store = open_store(workspace)
    fact = store_facts(workspace, "Job", "0101")[0]

    # INV-001: UPDATE/DELETE on events is structurally refused.
    conn = sqlite3.connect(store.db_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE events SET occurred_at = ? WHERE fact_key = ?",
            ("2027-01-01T00:00:00Z", fact.fact_key),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM events WHERE fact_key = ?", (fact.fact_key,))
    conn.close()

    # A later snapshot that omits the fact does not remove it.
    updated = write_snapshot(
        tmp_path,
        "jobs-later.csv",
        "id,status,assignee,amount,completed_at,invoiced_at\n"
        "102,in_progress,hanako,,2026-10-02,\n",
    )
    assert ingest_snapshot(workspace, updated, observed_at=LATER).ok
    assert [f.fact_key for f in store_facts(workspace, "Job", "0101")] == [
        fact.fact_key
    ]


def test_rejected_records_write_no_facts(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(
        tmp_path,
        "jobs.csv",
        "id,status,assignee,amount,completed_at,invoiced_at\n"
        "0101,completed,taro,42.5,2026-10-01,\n"
        ",completed,hanako,,2026-10-02,\n",
    )

    result = ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    # INV-006: the run fails on the rejected record, but the accepted row's
    # facts are persisted and the rejected row writes nothing.
    assert not result.ok
    assert len(result.accepted) == 1
    store = open_store(workspace)
    assert store.entity_state("Job", "0101") is not None
    assert count_rows(store.db_path, "events") == 1
    assert count_rows(store.db_path, "entities") == 2  # Job/0101 + Person/taro


def test_failed_apply_leaves_no_partial_state(tmp_path: Path) -> None:
    store = SqliteCanonicalStore(tmp_path / "runtime")
    good = _observation(
        "obs-1",
        entities=[ResolvedEntity(entity="Job", id="1", properties={"status": "ok"})],
        events=[
            ResolvedEvent(
                event="JobCompleted",
                subject_entity="Job",
                subject_id="1",
                occurred_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                observed_at=FIXED_NOW,
            )
        ],
    )
    bad = _observation(
        "obs-2",
        # An unserializable property fails mid-transaction.
        entities=[ResolvedEntity(entity="Job", id="2", properties={"x": object()})],
        events=[],
    )

    # INV-004: entity upserts and event inserts roll back together.
    with pytest.raises(TypeError):
        store.apply("ing-x", [good, bad])
    assert count_rows(store.db_path, "entities") == 0
    assert count_rows(store.db_path, "events") == 0
    assert count_rows(store.db_path, "entity_observations") == 0


def test_store_error_becomes_diagnostic(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)

    class FailingStore:
        def apply(self, ingest_id, observations):
            raise sqlite3.OperationalError("disk full")

    result = ingest_snapshot(
        workspace, snapshot, observed_at=FIXED_NOW, canonical=FailingStore()
    )

    # INV-011: a store failure is an ordered diagnostic, not a traceback.
    assert not result.ok
    assert "CANON.STORE_ERROR" in [d.code for d in result.diagnostics]
    assert result.canonical is None


def test_non_sqlite_apply_error_becomes_canonical_store_error(
    tmp_path: Path,
) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)

    class BuggyStore:
        def apply(self, ingest_id, observations):
            raise TypeError("unserializable property")

    result = ingest_snapshot(
        workspace, snapshot, observed_at=FIXED_NOW, canonical=BuggyStore()
    )

    # INV-011: the store boundary converts any exception — not only sqlite or
    # IO errors — into an ordered diagnostic.
    assert not result.ok
    assert "CANON.STORE_ERROR" in [d.code for d in result.diagnostics]
    assert result.canonical is None


def test_entity_state_is_monotonic_in_observation_time(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    header = "id,status,assignee,amount,completed_at,invoiced_at\n"
    newer = write_snapshot(
        tmp_path,
        "jobs-new.csv",
        header + "0101,completed,taro,42.5,2026-10-01,\n",
    )
    stale = write_snapshot(
        tmp_path,
        "jobs-stale.csv",
        header + "0101,in_progress,hanako,42.5,,\n",
    )

    assert ingest_snapshot(workspace, newer, observed_at=LATER).ok
    state = open_store(workspace).entity_state("Job", "0101")
    assert state is not None
    assert state["updated_at"] == "2026-10-06T12:00:00Z"

    # Replaying an older observation afterwards must not regress the entity:
    # current state moves forward in observation time only.
    result = ingest_snapshot(workspace, stale, observed_at=FIXED_NOW)
    assert result.ok
    state = open_store(workspace).entity_state("Job", "0101")
    assert state["properties"]["status"] == "completed"
    assert state["properties"]["assignee"] == "taro"
    assert state["updated_at"] == "2026-10-06T12:00:00Z"


def test_ingest_emits_canonical_trace_events(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)

    class ListTrace:
        def __init__(self) -> None:
            self.events = []

        def emit(self, stage, kind, data):
            self.events.append((stage, kind, data))

    trace = ListTrace()
    result = ingest_snapshot(
        workspace, snapshot, observed_at=FIXED_NOW, trace=trace
    )

    assert result.ok
    kinds = [kind for _, kind, _ in trace.events]
    assert "canonical.event_recorded" in kinds
    assert "canonical.applied" in kinds


def test_equal_time_replay_is_order_independent(tmp_path: Path) -> None:
    """Equal observed_at replays resolve by ingest id, not apply order.

    Two runs observed at the same instant with different content must leave
    the same final entity state regardless of which is applied first — the
    larger ingest id wins.
    """
    completed = _observation(
        "obs-a",
        entities=[
            ResolvedEntity(entity="Job", id="1", properties={"status": "completed"})
        ],
        events=[],
    )
    reopened = _observation(
        "obs-b",
        entities=[
            ResolvedEntity(entity="Job", id="1", properties={"status": "in_progress"})
        ],
        events=[],
    )

    def apply_order(order: list[tuple[str, Observation]], name: str) -> dict:
        store = SqliteCanonicalStore(tmp_path / name)
        for ingest_id, obs in order:
            store.apply(ingest_id, [obs])
        state = store.entity_state("Job", "1")
        assert state is not None
        return state

    # ing-b > ing-a lexicographically, so "reopened" wins in both orders.
    forward = apply_order([("ing-a", completed), ("ing-b", reopened)], "s1")
    backward = apply_order([("ing-b", reopened), ("ing-a", completed)], "s2")
    assert forward["properties"]["status"] == "in_progress"
    assert backward["properties"]["status"] == "in_progress"
    # Both observations stay linked even when their update loses the tie.
    assert count_rows(tmp_path / "s2" / "canon.db", "entity_observations") == 2

    # Within one run, the first row for an entity wins (ids are equal).
    store = SqliteCanonicalStore(tmp_path / "s3")
    store.apply("ing-x", [completed, reopened])
    state = store.entity_state("Job", "1")
    assert state["properties"]["status"] == "completed"


def test_canonical_apply_skipped_when_evidence_write_fails(
    tmp_path: Path,
) -> None:
    workspace = make_ws(tmp_path)
    snapshot = write_snapshot(tmp_path, "jobs.csv", CSV_SAMPLE)

    class FailingObservationsStore(FileEvidenceStore):
        def write_observations(self, name, lines):
            raise OSError("disk full")

    result = ingest_snapshot(
        workspace,
        snapshot,
        observed_at=FIXED_NOW,
        store=FailingObservationsStore(tmp_path / "rt"),
    )

    # Facts are never committed without their evidence: the run fails on
    # INGEST.STORE_ERROR and the canonical apply is skipped with a warning.
    assert not result.ok
    codes = [d.code for d in result.diagnostics]
    assert "INGEST.STORE_ERROR" in codes
    assert "CANON.SKIPPED" in codes
    assert result.canonical is None


def test_property_round_trip(tmp_path: Path) -> None:
    store = SqliteCanonicalStore(tmp_path / "runtime")
    moment = datetime(2026, 9, 30, 8, 30, tzinfo=timezone.utc)
    obs = _observation(
        "obs-1",
        entities=[
            ResolvedEntity(
                entity="Job",
                id="1",
                properties={
                    "status": "completed",
                    "amount": 42.5,
                    "active": True,
                    "count": 7,
                    "seen": moment,
                },
            )
        ],
        events=[],
    )
    store.apply("ing-x", [obs])

    # INV-010: values round-trip through the canonical JSON encoding;
    # datetimes persist as UTC ISO 8601 strings.
    state = store.entity_state("Job", "1")
    assert state["properties"] == {
        "status": "completed",
        "amount": 42.5,
        "active": True,
        "count": 7,
        "seen": "2026-09-30T08:30:00Z",
    }


def test_readonly_reads_pre_upgrade_schema(tmp_path: Path) -> None:
    """A canon.db from before the migrated tables/columns reads through
    a readonly store as empty sections and migration-equivalent values
    — never as store errors, and without modifying the database."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    conn = sqlite3.connect(runtime / "canon.db")
    conn.execute(
        """
        CREATE TABLE entities (
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            properties TEXT NOT NULL,
            first_observation_id TEXT NOT NULL,
            last_observation_id TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (entity_type, entity_id)
        )
        """
    )
    conn.execute(
        "INSERT INTO entities VALUES ("
        " 'Job', 'C01', '{}', 'obs-1', 'obs-2', '2026-09-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    store = SqliteCanonicalStore(runtime, readonly=True)
    try:
        (entity,) = store.entities()
        assert entity["properties_changed_at"] == "2026-09-01T00:00:00Z"
        assert entity["last_ingest_id"] == ""

        (typed,) = store.entities_of_type("Job")
        assert typed["properties_changed_at"] == "2026-09-01T00:00:00Z"

        assert store.diagnosis_runs() == []
        assert store.diagnosis_run("dgx-x") is None
        assert store.proposals() == []
        assert store.proposal("dgx-x-p0") is None
        assert store.decisions_for("dgx-x-p0") == []
        assert store.executions_for_key("key") == []
        assert store.executions_for_proposal("dgx-x-p0") == []
        assert store.fact_links("fact") == []
        assert store.links_for_target("target") == []
        assert store.evaluation_runs() == []
        assert store.evaluation_run("evl-x") is None
    finally:
        store.close()

    # The read-only open never writes the missing schema back.
    conn = sqlite3.connect(runtime / "canon.db")
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(entities)")
    }
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    conn.close()
    assert "properties_changed_at" not in columns
    assert "last_ingest_id" not in columns
    assert tables == {"entities"}


def _observation(
    observation_id: str,
    *,
    entities: list[ResolvedEntity],
    events: list[ResolvedEvent],
) -> Observation:
    return Observation(
        observation_id=observation_id,
        ingest_id="ing-x",
        source="jobs",
        row=1,
        status="accepted",
        observed_at=FIXED_NOW,
        content_digest="sha256:x",
        mapping_digest="sha256:m",
        record={},
        entities=entities,
        events=events,
        diagnostics=[],
    )
