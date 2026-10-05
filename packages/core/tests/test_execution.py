"""Write-back execution tests: approval gate, CSV adapter, retries."""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
import support

from ontology_core import (
    AdapterFailed,
    AdapterRequest,
    CsvReminderAdapter,
    ProviderRequest,
    SqliteCanonicalStore,
    decide,
    execute,
    execution_identity,
    ingest_snapshot,
    load_workspace,
    payload_digest,
    run_diagnosis,
    run_projection,
)
from ontology_core.evidence import RUNTIME_DIR
from ontology_core.trace import JsonlTraceSink

FIXED_NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
AS_OF = datetime(2026, 10, 5, 0, 0, 0, tzinfo=timezone.utc)

CSV_SAMPLE = (
    "id,status,assignee,amount,completed_at,invoiced_at\n"
    "A01,completed,taro,1,2026-10-01,\n"
)

GOOD_OUTPUT = json.dumps(
    {
        "explanation": "Job A01 has been completed for 4 days without an invoice.",
        "hypotheses": [],
        "follow_up_questions": [],
        "action_proposals": [
            {
                "action": "SendReminder",
                "params": {
                    "target": "taro",
                    "related_entity": "A01",
                    "reason": "Job exceeded the two-day threshold.",
                },
                "reason": "Candidate is the oldest uninvoiced job.",
            }
        ],
    }
)


class FixedProvider:
    name = "fixed-double"

    def __init__(self, output: str) -> None:
        self.output = output

    def complete(self, request: ProviderRequest) -> str:
        return self.output


def make_ws(tmp_path: Path):
    ws_dir = support.make_workspace(tmp_path / "ws")
    result = load_workspace(ws_dir)
    assert result.ok, [d.format() for d in result.diagnostics]
    return result.workspace


def make_proposal(tmp_path: Path, output: str = GOOD_OUTPUT):
    """Run a diagnosis that validates one proposal; return (ws, view-ish)."""
    workspace = make_ws(tmp_path)
    snapshot = tmp_path / "jobs.csv"
    snapshot.write_text(CSV_SAMPLE, encoding="utf-8")
    assert ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW).ok
    projection = run_projection(
        workspace, "CompletedButNotInvoiced", as_of=AS_OF
    )
    assert projection.ok
    result = run_diagnosis(
        workspace, FixedProvider(output), model="m",
        run_id=projection.run_id,
    )
    assert result.ok, [d.format() for d in result.diagnostics]
    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    proposals = store.proposals()
    assert len(proposals) == 1
    return workspace, proposals[0]


def approve(workspace, proposal) -> None:
    result = decide(
        workspace, proposal["proposal_id"], "approved",
        reviewer="fde-sato",
    )
    assert result.ok, [d.format() for d in result.diagnostics]


def reminders_path(workspace) -> Path:
    return Path(workspace.path) / RUNTIME_DIR / "reminders.csv"


def read_reminders(workspace) -> list[dict[str, str]]:
    path = reminders_path(workspace)
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def csv_adapter(workspace) -> CsvReminderAdapter:
    return CsvReminderAdapter(reminders_path(workspace))


def test_approved_proposal_executes_with_receipt(tmp_path: Path) -> None:
    workspace, proposal = make_proposal(tmp_path)
    approve(workspace, proposal)

    result = execute(workspace, proposal["proposal_id"], csv_adapter(workspace))

    assert result.ok, [d.format() for d in result.diagnostics]
    assert result.status == "succeeded"
    assert result.attempt == 1
    assert result.attempts == 1
    assert result.recorded
    assert not result.replayed
    assert (result.execution_id, result.idempotency_key) == (
        execution_identity(
            proposal["proposal_id"], proposal["payload_digest"]
        )
    )

    rows = read_reminders(workspace)
    assert len(rows) == 1
    row = rows[0]
    assert row["proposal_id"] == proposal["proposal_id"]
    assert row["execution_id"] == result.execution_id
    assert row["idempotency_key"] == result.idempotency_key
    assert row["action"] == "SendReminder"
    assert row["target"] == "taro"
    assert row["related_entity"] == "A01"
    assert row["reason"] == "Job exceeded the two-day threshold."
    assert row["reminder_id"].startswith("rem-")

    receipt = result.receipt
    assert receipt["reminder_id"] == row["reminder_id"]
    assert receipt["path"] == str(reminders_path(workspace))
    assert not receipt["deduplicated"]

    # The attempt is persisted with the receipt.
    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    attempts = store.executions_for_key(result.idempotency_key)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "succeeded"
    assert attempts[0]["receipt"] == receipt
    assert attempts[0]["adapter"] == "csv-reminders"
    assert attempts[0]["proposal_id"] == proposal["proposal_id"]


def test_execute_refused_for_unapproved_rejected_unknown(
    tmp_path: Path,
) -> None:
    workspace, proposal = make_proposal(tmp_path)
    pid = proposal["proposal_id"]

    # Undecided (proposed) never reaches the adapter.
    result = execute(workspace, pid, csv_adapter(workspace))
    assert not result.ok
    assert result.status == "refused"
    assert not result.recorded
    assert "DECISION.NOT_APPROVED" in {d.code for d in result.diagnostics}
    assert read_reminders(workspace) == []

    # Rejected never reaches the adapter either.
    decide(workspace, pid, "rejected", reviewer="fde-sato")
    result = execute(workspace, pid, csv_adapter(workspace))
    assert result.status == "refused"
    assert "DECISION.NOT_APPROVED" in {d.code for d in result.diagnostics}
    assert read_reminders(workspace) == []

    # Approval is bound to the latest decision: approve → reject →
    # execute still refuses.
    decide(workspace, pid, "approved", reviewer="fde-sato")
    decide(workspace, pid, "rejected", reviewer="fde-ueda")
    result = execute(workspace, pid, csv_adapter(workspace))
    assert result.status == "refused"
    assert read_reminders(workspace) == []

    # Unknown proposal id fails closed.
    result = execute(workspace, "prp-nope", csv_adapter(workspace))
    assert result.status == "refused"
    assert "EXECUTION.UNKNOWN_PROPOSAL" in {
        d.code for d in result.diagnostics
    }


def test_changed_payload_never_reaches_adapter(tmp_path: Path) -> None:
    """A stored proposal row whose digest does not match its payload —
    the shape of a tampered/changed proposal — fails the gate even when
    a decision approved the stored digest."""
    workspace, proposal = make_proposal(tmp_path)
    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    conn = store._connect()
    bad_params = {"target": "taro", "related_entity": "A01", "reason": "x"}
    conn.execute(
        "INSERT INTO proposals (proposal_id, diagnosis_run_id,"
        " proposal_index, action, params, reason, subject_entity,"
        " subject_id, related_entities, evidence, payload_digest,"
        " created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "dgx-x-p9",
            proposal["diagnosis_run_id"],
            9,
            "SendReminder",
            json.dumps(bad_params),
            "x",
            "Job",
            "A01",
            "[]",
            "{}",
            # Digest of a *different* payload than what is stored.
            payload_digest("SendReminder", {"target": "mallory"}),
            "2026-10-04T00:00:00Z",
        ),
    )
    conn.commit()

    assert decide(
        workspace, "dgx-x-p9", "approved", reviewer="fde-sato"
    ).ok
    result = execute(workspace, "dgx-x-p9", csv_adapter(workspace))
    assert result.status == "refused"
    assert "DECISION.PAYLOAD_CHANGED" in {
        d.code for d in result.diagnostics
    }
    assert read_reminders(workspace) == []


def test_repeated_execute_does_not_duplicate(tmp_path: Path) -> None:
    workspace, proposal = make_proposal(tmp_path)
    approve(workspace, proposal)
    pid = proposal["proposal_id"]

    first = execute(workspace, pid, csv_adapter(workspace))
    assert first.ok

    # A second execute replays the persisted receipt and never touches
    # the adapter again.
    class Bomb:
        name = "bomb"

        def deliver(self, request):
            raise AssertionError("adapter must not be called")

    second = execute(workspace, pid, Bomb())
    assert second.ok
    assert second.status == "succeeded"
    assert second.replayed
    assert second.receipt == first.receipt
    assert second.attempt == 1

    assert len(read_reminders(workspace)) == 1
    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    assert len(store.executions_for_key(first.idempotency_key)) == 1


class FailThenSucceed:
    """Adapter that fails cleanly once, then writes like the CSV one."""

    name = "fail-then-succeed"

    def __init__(self, inner: CsvReminderAdapter) -> None:
        self.inner = inner
        self.calls = 0

    def deliver(self, request: AdapterRequest) -> dict:
        self.calls += 1
        if self.calls == 1:
            raise AdapterFailed("destination unavailable before write")
        return self.inner.deliver(request)


def test_failed_attempt_can_retry(tmp_path: Path) -> None:
    workspace, proposal = make_proposal(tmp_path)
    approve(workspace, proposal)
    pid = proposal["proposal_id"]
    adapter = FailThenSucceed(csv_adapter(workspace))

    first = execute(workspace, pid, adapter)
    assert not first.ok
    assert first.status == "failed"
    assert first.attempt == 1
    assert read_reminders(workspace) == []

    second = execute(workspace, pid, adapter)
    assert second.ok, [d.format() for d in second.diagnostics]
    assert second.status == "succeeded"
    assert second.attempt == 2
    assert adapter.calls == 2

    rows = read_reminders(workspace)
    assert len(rows) == 1
    assert rows[0]["idempotency_key"] == first.idempotency_key

    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    attempts = store.executions_for_key(first.idempotency_key)
    assert [a["status"] for a in attempts] == ["failed", "succeeded"]
    assert [a["attempt"] for a in attempts] == [1, 2]
    assert {a["execution_id"] for a in attempts} == {first.execution_id}
    assert attempts[0]["error"] == "destination unavailable before write"


class WriteThenCrash:
    """Adapter whose write lands but then dies — an interruption."""

    name = "write-then-crash"

    def __init__(self, inner: CsvReminderAdapter) -> None:
        self.inner = inner
        self.calls = 0

    def deliver(self, request: AdapterRequest) -> dict:
        self.calls += 1
        self.inner.deliver(request)
        raise RuntimeError("simulated crash after write")


def test_uncertain_attempt_blocks_blind_retry(tmp_path: Path) -> None:
    workspace, proposal = make_proposal(tmp_path)
    approve(workspace, proposal)
    pid = proposal["proposal_id"]
    adapter = WriteThenCrash(csv_adapter(workspace))

    first = execute(workspace, pid, adapter)
    assert not first.ok
    assert first.status == "uncertain"
    assert first.recorded
    assert first.receipt is None

    # The row may have been written — and in this double it was.
    assert len(read_reminders(workspace)) == 1

    # A retry must not blindly duplicate: refused without adapter call.
    second = execute(workspace, pid, adapter)
    assert not second.ok
    assert second.status == "refused"
    assert not second.recorded
    assert adapter.calls == 1
    assert "EXECUTION.UNCERTAIN" in {d.code for d in second.diagnostics}
    assert len(read_reminders(workspace)) == 1

    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    attempts = store.executions_for_key(first.idempotency_key)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "uncertain"
    assert attempts[0]["error"] == "unexpected RuntimeError"


def test_adapter_refuses_unsupported_action(tmp_path: Path) -> None:
    adapter = CsvReminderAdapter(tmp_path / "reminders.csv")
    request = AdapterRequest(
        execution_id="exe-x",
        idempotency_key="idem-x",
        attempt=1,
        proposal_id="p",
        action="DeleteJob",
        params={},
        payload_digest="sha256:x",
        subject_entity=None,
        subject_id=None,
        related_entities=[],
        reason=None,
    )
    with pytest.raises(AdapterFailed):
        adapter.deliver(request)
    assert not adapter.path.exists()

    # And a SendReminder missing required params is a definite failure,
    # never a fabricated receipt.
    bad = AdapterRequest(
        execution_id="exe-y",
        idempotency_key="idem-y",
        attempt=1,
        proposal_id="p",
        action="SendReminder",
        params={"target": "taro"},
        payload_digest="sha256:y",
        subject_entity=None,
        subject_id=None,
        related_entities=[],
        reason=None,
    )
    with pytest.raises(AdapterFailed):
        adapter.deliver(bad)
    assert not adapter.path.exists()


def test_csv_adapter_dedupes_idempotency_key(tmp_path: Path) -> None:
    adapter = CsvReminderAdapter(tmp_path / "reminders.csv")
    request = AdapterRequest(
        execution_id="exe-x",
        idempotency_key="idem-same",
        attempt=1,
        proposal_id="p",
        action="SendReminder",
        params={
            "target": "taro",
            "related_entity": "A01",
            "reason": "x",
        },
        payload_digest="sha256:x",
        subject_entity="Job",
        subject_id="A01",
        related_entities=[],
        reason="x",
    )
    first = adapter.deliver(request)
    assert not first["deduplicated"]
    second = adapter.deliver(
        AdapterRequest(
            execution_id="exe-x",
            idempotency_key="idem-same",
            attempt=2,
            proposal_id="p",
            action="SendReminder",
            params=request.params,
            payload_digest="sha256:x",
            subject_entity="Job",
            subject_id="A01",
            related_entities=[],
            reason="x",
        )
    )
    assert second["deduplicated"]
    assert second["reminder_id"] == first["reminder_id"]
    with adapter.path.open("r", newline="", encoding="utf-8") as f:
        assert len(list(csv.DictReader(f))) == 1


def test_executions_are_append_only(tmp_path: Path) -> None:
    workspace, proposal = make_proposal(tmp_path)
    approve(workspace, proposal)
    result = execute(
        workspace, proposal["proposal_id"], csv_adapter(workspace)
    )
    assert result.ok

    db = Path(workspace.path) / RUNTIME_DIR / "canon.db"
    conn = sqlite3.connect(db)
    for statement in (
        "UPDATE executions SET status = 'x' WHERE execution_id = ?",
        "DELETE FROM executions WHERE execution_id = ?",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(statement, (result.execution_id,))
    conn.close()


def test_execute_does_not_mutate_upstream(tmp_path: Path) -> None:
    workspace, proposal = make_proposal(tmp_path)
    approve(workspace, proposal)
    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    conn = store._connect()
    before = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "entities",
            "events",
            "entity_observations",
            "event_observations",
            "projection_runs",
            "diagnosis_runs",
            "proposals",
            "decisions",
        )
    }
    result = execute(
        workspace, proposal["proposal_id"], csv_adapter(workspace)
    )
    assert result.ok
    after = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before
    }
    store.close()
    assert before == after


def test_execute_trace_events(tmp_path: Path) -> None:
    workspace, proposal = make_proposal(tmp_path)
    trace_path = tmp_path / "trace.jsonl"
    sink = JsonlTraceSink(trace_path)
    pid = proposal["proposal_id"]

    # Refused (unapproved) attempt leaves a trace event.
    execute(workspace, pid, csv_adapter(workspace), trace=sink)
    approve(workspace, proposal)
    # Succeeded attempt.
    execute(workspace, pid, csv_adapter(workspace), trace=sink)
    # Replay.
    execute(workspace, pid, csv_adapter(workspace), trace=sink)

    events = [
        json.loads(line)
        for line in trace_path.read_text().splitlines()
    ]
    assert [e["stage"] for e in events] == ["execution"] * 3
    assert [e["kind"] for e in events] == [
        "execution.refused",
        "execution.succeeded",
        "execution.replayed",
    ]
    assert events[1]["data"]["proposal_id"] == pid
    assert events[1]["data"]["status"] == "succeeded"
    assert events[1]["data"]["adapter"] == "csv-reminders"


def test_execute_trace_error_is_diagnosed(tmp_path: Path) -> None:
    class FailingTrace:
        def emit(self, stage, kind, data):
            raise OSError("disk full")

    workspace, proposal = make_proposal(tmp_path)
    approve(workspace, proposal)
    result = execute(
        workspace,
        proposal["proposal_id"],
        csv_adapter(workspace),
        trace=FailingTrace(),
    )
    assert result.status == "succeeded"
    assert "EXECUTION.TRACE_ERROR" in {
        d.code for d in result.diagnostics
    }


def test_execute_store_errors_become_diagnostics(tmp_path: Path) -> None:
    class BrokenReadStore(SqliteCanonicalStore):
        def proposal(self, proposal_id):
            raise RuntimeError("locked")

    workspace, proposal = make_proposal(tmp_path)
    approve(workspace, proposal)
    result = execute(
        workspace,
        proposal["proposal_id"],
        csv_adapter(workspace),
        store=BrokenReadStore(Path(workspace.path) / RUNTIME_DIR),
    )
    assert result.status == "refused"
    assert "EXECUTION.STORE_ERROR" in {
        d.code for d in result.diagnostics
    }

    class BrokenWriteStore(SqliteCanonicalStore):
        def record_execution(self, record):
            raise RuntimeError("locked")

    result = execute(
        workspace,
        proposal["proposal_id"],
        csv_adapter(workspace),
        store=BrokenWriteStore(Path(workspace.path) / RUNTIME_DIR),
    )
    # The adapter did write, but the receipt could not be persisted —
    # surfaced as an error, not silently claimed.
    assert not result.ok
    assert "EXECUTION.STORE_ERROR" in {
        d.code for d in result.diagnostics
    }
