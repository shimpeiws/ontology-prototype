"""SQLite storage coordination (ADR 0003).

The API and CLI share the same ``canon.db``: WAL lets readers inspect
committed state while a writer holds the lock, ``busy_timeout`` bounds
how long a contended writer waits, and lock contention surfaces as the
stable ``STORAGE.WRITE_BUSY`` diagnostic code rather than an
operation-specific ``*.STORE_ERROR``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import support

from ontology_core import (
    BUSY_TIMEOUT_MS,
    SqliteCanonicalStore,
    decide,
    ingest_snapshot,
    is_write_busy,
    load_workspace,
    proposal_view,
    run_diagnosis,
    run_projection,
    store_error_code,
)
from ontology_core.canonical import CANON_DB
from ontology_core.evidence import RUNTIME_DIR
from test_decisions import (
    AS_OF,
    CSV_SAMPLE,
    FIXED_NOW,
    GOOD_OUTPUT,
    FixedProvider,
)


def make_ws(tmp_path: Path):
    ws_dir = support.make_workspace(tmp_path / "ws")
    result = load_workspace(ws_dir)
    assert result.ok, [d.format() for d in result.diagnostics]
    return result.workspace


def make_proposal(tmp_path: Path):
    """One persisted, undecided proposal."""
    workspace = make_ws(tmp_path)
    snapshot = tmp_path / "jobs.csv"
    snapshot.write_text(CSV_SAMPLE, encoding="utf-8")
    assert ingest_snapshot(
        workspace, snapshot, observed_at=FIXED_NOW
    ).ok
    projection = run_projection(
        workspace, "CompletedButNotInvoiced", as_of=AS_OF
    )
    assert projection.ok
    result = run_diagnosis(
        workspace, FixedProvider(GOOD_OUTPUT), model="m",
        run_id=projection.run_id,
    )
    assert result.ok, [d.format() for d in result.diagnostics]
    views = proposal_view(workspace)
    assert len(views) == 1
    return workspace, views[0]


def hold_write_lock(db: Path) -> sqlite3.Connection:
    """A connection holding the write lock — RESERVED in WAL, so readers
    still see committed state while writers queue."""
    conn = sqlite3.connect(db)
    conn.execute("BEGIN IMMEDIATE")
    return conn


def test_wal_mode_enabled_on_writable_store(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    mode = store._connect().execute("PRAGMA journal_mode").fetchone()[0]
    store.close()
    assert mode == "wal"


def test_busy_timeout_explicit_on_connections(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    runtime = Path(workspace.path) / RUNTIME_DIR

    store = SqliteCanonicalStore(runtime)
    assert store._connect().execute(
        "PRAGMA busy_timeout"
    ).fetchone()[0] == BUSY_TIMEOUT_MS
    store.close()

    custom = SqliteCanonicalStore(runtime, busy_timeout_ms=250)
    assert custom._connect().execute(
        "PRAGMA busy_timeout"
    ).fetchone()[0] == 250
    custom.close()


def test_reader_sees_committed_state_while_writer_holds_lock(
    tmp_path: Path,
) -> None:
    """WAL: a writer holding its transaction never blocks readers on the
    last committed state."""
    workspace, view = make_proposal(tmp_path)
    db = Path(workspace.path) / RUNTIME_DIR / CANON_DB
    holder = hold_write_lock(db)
    try:
        reader = SqliteCanonicalStore(
            Path(workspace.path) / RUNTIME_DIR, readonly=True
        )
        try:
            proposal = reader.proposal(view["proposal_id"])
            assert proposal is not None
            assert proposal["payload_digest"] == view["payload_digest"]
        finally:
            reader.close()
    finally:
        holder.rollback()
        holder.close()


def test_write_contention_classifies_as_write_busy(tmp_path: Path) -> None:
    """A contended write raises SQLITE_BUSY and the diagnostic code is
    the stable STORAGE.WRITE_BUSY, not an operation-specific
    *.STORE_ERROR."""
    workspace, view = make_proposal(tmp_path)
    db = Path(workspace.path) / RUNTIME_DIR / CANON_DB
    holder = hold_write_lock(db)
    try:
        store = SqliteCanonicalStore(
            Path(workspace.path) / RUNTIME_DIR, busy_timeout_ms=50
        )
        try:
            store.record_decision(
                {
                    "decision_id": "dec-test",
                    "proposal_id": view["proposal_id"],
                    "decision": "approved",
                    "reviewer": "r",
                    "reason": None,
                    "payload_digest": view["payload_digest"],
                    "decided_at": FIXED_NOW,
                }
            )
            raise AssertionError("contended write should fail")
        except sqlite3.OperationalError as exc:
            assert is_write_busy(exc)
            assert store_error_code(exc, "DECISION.STORE_ERROR") == (
                "STORAGE.WRITE_BUSY"
            )
        finally:
            store.close()
    finally:
        holder.rollback()
        holder.close()


def test_decide_under_contention_reports_write_busy(
    tmp_path: Path,
) -> None:
    workspace, view = make_proposal(tmp_path)
    db = Path(workspace.path) / RUNTIME_DIR / CANON_DB
    holder = hold_write_lock(db)
    try:
        store = SqliteCanonicalStore(
            Path(workspace.path) / RUNTIME_DIR, busy_timeout_ms=50
        )
        result = decide(
            workspace,
            view["proposal_id"],
            "approved",
            reviewer="r",
            store=store,
        )
        store.close()
    finally:
        holder.rollback()
        holder.close()
    assert not result.recorded
    assert "STORAGE.WRITE_BUSY" in {d.code for d in result.diagnostics}

    # Releasing the lock unblocks the same call — contention is a
    # transient transport condition, not a state.
    result = decide(
        workspace, view["proposal_id"], "approved", reviewer="r"
    )
    assert result.recorded


def test_extended_busy_codes_classify() -> None:
    """SQLITE_BUSY_SNAPSHOT and other extended busy codes share the
    low byte; they classify as busy, non-busy errors keep the
    operation's own code."""
    busy = sqlite3.OperationalError("database is locked")
    busy.sqlite_errorcode = sqlite3.SQLITE_BUSY_SNAPSHOT
    assert is_write_busy(busy)

    integrity = sqlite3.IntegrityError("UNIQUE constraint failed")
    assert not is_write_busy(integrity)
    assert store_error_code(integrity, "DECISION.STORE_ERROR") == (
        "DECISION.STORE_ERROR"
    )

    generic = RuntimeError("boom")
    assert not is_write_busy(generic)
    assert store_error_code(generic, "DECISION.STORE_ERROR") == (
        "DECISION.STORE_ERROR"
    )


def test_transactions_stay_atomic_under_wal(tmp_path: Path) -> None:
    """A rolled-back writer leaves readers on the prior commit — WAL
    never exposes a partial transaction."""
    workspace, view = make_proposal(tmp_path)
    db = Path(workspace.path) / RUNTIME_DIR / CANON_DB
    holder = sqlite3.connect(db)
    holder.isolation_level = None
    holder.execute("BEGIN IMMEDIATE")
    holder.execute(
        "INSERT INTO decisions (decision_id, proposal_id, decision,"
        " reviewer, reason, payload_digest, decided_at) VALUES"
        " ('dec-ghost', ?, 'approved', 'r', NULL, ?, 'now')",
        (view["proposal_id"], view["payload_digest"]),
    )
    holder.rollback()
    holder.close()

    reader = SqliteCanonicalStore(
        Path(workspace.path) / RUNTIME_DIR, readonly=True
    )
    try:
        assert reader.decisions_for(view["proposal_id"]) == []
    finally:
        reader.close()
