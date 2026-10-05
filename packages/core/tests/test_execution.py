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
from ontology_core.execution import REMINDER_COLUMNS
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


def csv_key(workspace) -> str:
    """The adapter key a CSV adapter on the default path binds to."""
    return f"csv-reminders|{reminders_path(workspace)}"


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
            proposal["proposal_id"],
            proposal["payload_digest"],
            csv_key(workspace),
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

    # The attempt is persisted: an attempting intent row then the
    # outcome row carrying the receipt.
    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    attempts = store.executions_for_key(result.idempotency_key)
    assert [a["status"] for a in attempts] == ["attempting", "succeeded"]
    assert [a["attempt"] for a in attempts] == [1, 1]
    assert attempts[-1]["receipt"] == receipt
    assert attempts[-1]["adapter"] == "csv-reminders"
    assert attempts[-1]["proposal_id"] == proposal["proposal_id"]


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
        name = "csv-reminders"
        destination = str(reminders_path(workspace))

        def deliver(self, request):
            raise AssertionError("adapter must not be called")

    second = execute(workspace, pid, Bomb())
    assert second.ok
    assert second.status == "succeeded"
    assert second.replayed
    assert second.receipt == first.receipt
    assert second.attempt == 1
    assert second.attempts == 1

    assert len(read_reminders(workspace)) == 1
    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    assert [
        a["status"]
        for a in store.executions_for_key(first.idempotency_key)
    ] == ["attempting", "succeeded"]


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
    assert [a["status"] for a in attempts] == [
        "attempting",
        "failed",
        "attempting",
        "succeeded",
    ]
    assert [a["attempt"] for a in attempts] == [1, 1, 2, 2]
    assert {a["execution_id"] for a in attempts} == {first.execution_id}
    assert attempts[1]["error"] == "destination unavailable before write"


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
    assert [a["status"] for a in attempts] == ["attempting", "uncertain"]
    assert attempts[-1]["error"] == "unexpected RuntimeError"


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

    # The intent row cannot be written, so the adapter is never invoked:
    # an unrecorded adapter call would be invisible to the next run's
    # replay/uncertainty gates.
    result = execute(
        workspace,
        proposal["proposal_id"],
        csv_adapter(workspace),
        store=BrokenWriteStore(Path(workspace.path) / RUNTIME_DIR),
    )
    assert not result.ok
    assert result.status == "refused"
    assert "EXECUTION.STORE_ERROR" in {
        d.code for d in result.diagnostics
    }
    assert read_reminders(workspace) == []


def test_execute_refuses_when_execution_history_unreadable(
    tmp_path: Path,
) -> None:
    """A store that cannot read prior executions fails closed: retrying
    blind could duplicate a landed effect."""

    class BrokenHistoryStore(SqliteCanonicalStore):
        def executions_for_key(self, key):
            raise RuntimeError("locked")

    workspace, proposal = make_proposal(tmp_path)
    approve(workspace, proposal)
    result = execute(
        workspace,
        proposal["proposal_id"],
        csv_adapter(workspace),
        store=BrokenHistoryStore(Path(workspace.path) / RUNTIME_DIR),
    )
    assert result.status == "refused"
    assert "EXECUTION.STORE_ERROR" in {
        d.code for d in result.diagnostics
    }
    assert read_reminders(workspace) == []


class NonIdempotentCsv:
    """A CSV destination adapter with no idempotent declaration."""

    name = "csv-reminders"

    def __init__(self, inner: CsvReminderAdapter) -> None:
        self.inner = inner
        self.calls = 0

    @property
    def destination(self) -> str:
        return self.inner.destination

    def deliver(self, request: AdapterRequest) -> dict:
        self.calls += 1
        return self.inner.deliver(request)


def test_unrecorded_outcome_blocks_blind_retry(tmp_path: Path) -> None:
    """The adapter write lands but the outcome row cannot be persisted:
    the stranded 'attempting' intent row must block a blind retry."""

    class OutcomeWriteFails(SqliteCanonicalStore):
        def __init__(self, root):
            super().__init__(root)
            self.writes = 0

        def record_execution(self, record):
            self.writes += 1
            # The intent row lands; the outcome row fails.
            if self.writes > 1:
                raise RuntimeError("disk full")
            super().record_execution(record)

    workspace, proposal = make_proposal(tmp_path)
    approve(workspace, proposal)
    pid = proposal["proposal_id"]
    store = OutcomeWriteFails(Path(workspace.path) / RUNTIME_DIR)
    adapter = NonIdempotentCsv(csv_adapter(workspace))

    first = execute(workspace, pid, adapter, store=store)
    # The adapter wrote, but the outcome row did not: report uncertain —
    # claiming success would hide an effect with no receipt on record.
    assert first.status == "uncertain"
    assert not first.ok
    assert "EXECUTION.STORE_ERROR" in {
        d.code for d in first.diagnostics
    }
    assert len(read_reminders(workspace)) == 1

    # Next run sees the stranded intent row: no second adapter call —
    # the earlier write may have landed (and here it did).
    second = execute(workspace, pid, adapter, store=store)
    assert second.status == "refused"
    assert "EXECUTION.UNCERTAIN" in {
        d.code for d in second.diagnostics
    }
    assert adapter.calls == 1
    assert len(read_reminders(workspace)) == 1


def test_uncertain_retry_allowed_for_idempotent_adapter(
    tmp_path: Path,
) -> None:
    """An adapter that dedupes on the idempotency key may retry after
    uncertain: a re-delivered key reports the existing row."""

    class CrashOnceThenCsv:
        name = "csv-reminders"
        idempotent = True

        def __init__(self, inner: CsvReminderAdapter) -> None:
            self.inner = inner
            self.calls = 0

        @property
        def destination(self) -> str:
            return self.inner.destination

        def deliver(self, request: AdapterRequest) -> dict:
            self.calls += 1
            receipt = self.inner.deliver(request)
            if self.calls == 1:
                raise RuntimeError("simulated crash after write")
            # Second call: same key already wrote — dedupe receipt.
            return receipt

    workspace, proposal = make_proposal(tmp_path)
    approve(workspace, proposal)
    pid = proposal["proposal_id"]
    adapter = CrashOnceThenCsv(csv_adapter(workspace))

    first = execute(workspace, pid, adapter)
    assert first.status == "uncertain"
    assert len(read_reminders(workspace)) == 1

    second = execute(workspace, pid, adapter)
    assert second.ok, [d.format() for d in second.diagnostics]
    assert second.status == "succeeded"
    assert second.attempt == 2
    assert second.attempts == 2
    assert second.receipt["deduplicated"] is True
    assert adapter.calls == 2
    assert len(read_reminders(workspace)) == 1

    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    attempts = store.executions_for_key(first.idempotency_key)
    assert [a["status"] for a in attempts] == [
        "attempting",
        "uncertain",
        "attempting",
        "succeeded",
    ]
    assert [a["attempt"] for a in attempts] == [1, 1, 2, 2]


def test_execute_records_uncertain_before_propagating_interrupt(
    tmp_path: Path,
) -> None:
    """Interruption mid-deliver still leaves an honest trail: the intent
    row, an uncertain outcome row, and a trace event."""

    class Interrupting:
        name = "interrupting"

        def deliver(self, request):
            raise KeyboardInterrupt()

    workspace, proposal = make_proposal(tmp_path)
    approve(workspace, proposal)
    trace_path = tmp_path / "trace.jsonl"
    sink = JsonlTraceSink(trace_path)

    with pytest.raises(KeyboardInterrupt):
        execute(
            workspace,
            proposal["proposal_id"],
            Interrupting(),
            trace=sink,
        )

    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    _, key = execution_identity(
        proposal["proposal_id"], proposal["payload_digest"], "interrupting"
    )
    attempts = store.executions_for_key(key)
    assert [a["status"] for a in attempts] == ["attempting", "uncertain"]
    assert attempts[-1]["error"] == "interrupted: KeyboardInterrupt"
    events = [
        json.loads(line)
        for line in trace_path.read_text().splitlines()
    ]
    assert [e["kind"] for e in events] == ["execution.uncertain"]
    assert events[0]["data"]["status"] == "uncertain"


def test_different_destination_gets_own_execution(tmp_path: Path) -> None:
    """A different destination is a different execution: it must never
    replay another file's receipt — it delivers honestly, once."""
    workspace, proposal = make_proposal(tmp_path)
    approve(workspace, proposal)
    pid = proposal["proposal_id"]

    first = execute(workspace, pid, csv_adapter(workspace))
    assert first.ok

    other_path = tmp_path / "other" / "reminders.csv"
    second = execute(workspace, pid, CsvReminderAdapter(other_path))
    assert second.ok, [d.format() for d in second.diagnostics]
    assert not second.replayed
    assert second.execution_id != first.execution_id
    assert second.receipt["path"] == str(other_path)

    # Each destination holds exactly one reminder for the proposal.
    assert len(read_reminders(workspace)) == 1
    with other_path.open("r", newline="", encoding="utf-8") as f:
        assert len(list(csv.DictReader(f))) == 1

    # And replay still holds per destination.
    third = execute(workspace, pid, CsvReminderAdapter(other_path))
    assert third.replayed
    assert third.receipt == second.receipt


def test_csv_adapter_neutralizes_spreadsheet_formulas(
    tmp_path: Path,
) -> None:
    """Model-authored values that a spreadsheet would evaluate are
    written inert — the CSV is meant to be opened in Excel/Sheets."""
    adapter = CsvReminderAdapter(tmp_path / "reminders.csv")
    adapter.deliver(
        AdapterRequest(
            execution_id="exe-x",
            idempotency_key="idem-x",
            attempt=1,
            proposal_id="p",
            action="SendReminder",
            params={
                "target": "=cmd|'/c calc'!A0",
                "related_entity": "-2+3",
                "reason": "@SUM(1+1)*cmd|' /c calc'!A0",
            },
            payload_digest="sha256:x",
            subject_entity="Job",
            subject_id="A01",
            related_entities=[],
            reason="x",
        )
    )
    with adapter.path.open("r", newline="", encoding="utf-8") as f:
        row = list(csv.DictReader(f))[0]
    assert row["target"] == "'=cmd|'/c calc'!A0"
    assert row["related_entity"] == "'-2+3"
    assert row["reason"].startswith("'")

    # A leading space is inert to the prefix check but spreadsheets
    # still evaluate the cell — it is neutralized too.
    adapter2 = CsvReminderAdapter(tmp_path / "reminders2.csv")
    adapter2.deliver(
        AdapterRequest(
            execution_id="exe-y",
            idempotency_key="idem-y",
            attempt=1,
            proposal_id="p",
            action="SendReminder",
            params={
                "target": " =cmd|'/c calc'!A0",
                "related_entity": "A01",
                "reason": "x",
            },
            payload_digest="sha256:y",
            subject_entity="Job",
            subject_id="A01",
            related_entities=[],
            reason="x",
        )
    )
    with adapter2.path.open("r", newline="", encoding="utf-8") as f:
        row = list(csv.DictReader(f))[0]
    assert row["target"] == "' =cmd|'/c calc'!A0"


def test_execute_refused_when_contract_changed(tmp_path: Path) -> None:
    """Approving then dropping the action from the contract fails the
    gate — a stale approval cannot carry a now-invalid action."""
    import dataclasses

    workspace, proposal = make_proposal(tmp_path)
    approve(workspace, proposal)

    edited = dataclasses.replace(workspace.agent, permitted_actions={})
    changed_ws = dataclasses.replace(workspace, agent=edited)
    result = execute(
        changed_ws, proposal["proposal_id"], csv_adapter(workspace)
    )
    assert result.status == "refused"
    assert "DECISION.CONTRACT_CHANGED" in {
        d.code for d in result.diagnostics
    }
    assert read_reminders(workspace) == []


def test_execute_backfills_legacy_proposal(tmp_path: Path) -> None:
    """A proposal from a run recorded before the proposals table is
    recovered by backfill, not reported as unknown."""
    workspace = make_ws(tmp_path)
    snapshot = tmp_path / "jobs.csv"
    snapshot.write_text(CSV_SAMPLE, encoding="utf-8")
    ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)
    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    conn = store._connect()
    conn.execute(
        "INSERT INTO diagnosis_runs (run_id, provider, model,"
        " prompt_version, prompt, status, input_digest, provider_called,"
        " deliverables, proposals, rejected, diagnostics, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "dgx-legacy",
            "legacy-provider",
            "m",
            "diagnosis.v1",
            "prompt",
            "ok",
            "sha256:legacy",
            1,
            "{}",
            json.dumps(
                [
                    {
                        "action": "SendReminder",
                        "params": {
                            "target": "taro",
                            "related_entity": "A01",
                            "reason": "x",
                        },
                        "reason": "legacy proposal",
                    }
                ]
            ),
            "[]",
            "[]",
            "2026-10-04T00:00:00Z",
        ),
    )
    conn.commit()

    # Not UNKNOWN_PROPOSAL — the proposal is recovered and gated.
    result = execute(workspace, "dgx-legacy-p0", csv_adapter(workspace))
    assert result.status == "refused"
    assert "DECISION.NOT_APPROVED" in {
        d.code for d in result.diagnostics
    }

    decide(workspace, "dgx-legacy-p0", "approved", reviewer="fde-sato")
    result = execute(workspace, "dgx-legacy-p0", csv_adapter(workspace))
    assert result.ok, [d.format() for d in result.diagnostics]
    assert len(read_reminders(workspace)) == 1


def test_csv_adapter_does_not_dedupe_a_truncated_row(
    tmp_path: Path,
) -> None:
    """A torn write (row truncated after the key column) must not dedupe
    as delivered, and must not be blindly appended either."""
    path = tmp_path / "reminders.csv"
    adapter = CsvReminderAdapter(path)
    request = AdapterRequest(
        execution_id="exe-x",
        idempotency_key="idem-x",
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
    adapter.deliver(request)
    # Simulate the interruption mid-write: truncate the last row after
    # the idempotency_key column (reminder_id/proposal_id/execution_id/
    # key present, the rest missing).
    lines = path.read_text().splitlines()
    head = lines[0].split(",")
    key_col = head.index("idempotency_key")
    lines[-1] = ",".join(lines[-1].split(",")[: key_col + 1])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(AdapterFailed):
        adapter.deliver(request)


def test_execution_latest_row_ignores_created_at_clock_rewind(
    tmp_path: Path,
) -> None:
    """Derived state follows insertion order, never wall-clock
    created_at — a rewound timestamp must not resurrect an old status."""
    workspace, proposal = make_proposal(tmp_path)
    approve(workspace, proposal)
    pid = proposal["proposal_id"]
    exe_id, key = execution_identity(
        pid, proposal["payload_digest"], csv_key(workspace)
    )
    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    base = {
        "execution_id": exe_id,
        "idempotency_key": key,
        "proposal_id": pid,
        "action": "SendReminder",
        "adapter": "csv-reminders",
    }
    store.record_execution(
        {
            **base,
            "attempt": 1,
            "status": "attempting",
            "receipt": None,
            "error": None,
            "created_at": datetime.now(timezone.utc),
        }
    )
    store.record_execution(
        {
            **base,
            "attempt": 1,
            "status": "uncertain",
            "receipt": None,
            # Rewound clock: earlier than the intent row. Latest-state
            # derivation must still pick this row by insertion order.
            "error": "unexpected RuntimeError",
            "created_at": datetime(2000, 1, 1, tzinfo=timezone.utc),
        }
    )

    # The rewound uncertain row is still the latest — with a
    # non-idempotent adapter this refuses instead of proceeding.
    adapter = NonIdempotentCsv(csv_adapter(workspace))
    result = execute(workspace, pid, adapter, store=store)
    assert result.status == "refused"
    assert "EXECUTION.UNCERTAIN" in {
        d.code for d in result.diagnostics
    }
    assert adapter.calls == 0
    assert read_reminders(workspace) == []


def test_execute_failed_attempt_emits_trace_event(tmp_path: Path) -> None:
    """execution.failed is traceable like the other outcomes."""

    class CleanFail:
        name = "clean-fail"

        def deliver(self, request):
            raise AdapterFailed("destination unavailable")

    workspace, proposal = make_proposal(tmp_path)
    approve(workspace, proposal)
    trace_path = tmp_path / "trace.jsonl"
    sink = JsonlTraceSink(trace_path)
    result = execute(
        workspace, proposal["proposal_id"], CleanFail(), trace=sink
    )
    assert result.status == "failed"
    events = [
        json.loads(line)
        for line in trace_path.read_text().splitlines()
    ]
    assert [e["kind"] for e in events] == ["execution.failed"]
    assert events[0]["data"]["status"] == "failed"


def test_executions_table_created_on_existing_db(tmp_path: Path) -> None:
    """A canon.db created before the executions table gains it on the
    next store open — the CREATE TABLE IF NOT EXISTS migration path."""
    workspace = make_ws(tmp_path)
    snapshot = tmp_path / "jobs.csv"
    snapshot.write_text(CSV_SAMPLE, encoding="utf-8")
    ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)

    db = Path(workspace.path) / RUNTIME_DIR / "canon.db"
    conn = sqlite3.connect(db)
    conn.execute("DROP TRIGGER IF EXISTS executions_no_update")
    conn.execute("DROP TRIGGER IF EXISTS executions_no_delete")
    conn.execute("DROP TABLE IF EXISTS executions")
    conn.commit()
    conn.close()

    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    assert store.executions_for_key("idem-none") == []


def test_csv_adapter_refuses_file_without_trailing_newline(
    tmp_path: Path,
) -> None:
    """A file ending mid-row (no newline terminator) is a torn write —
    appending would merge into the partial row, so it must refuse."""
    path = tmp_path / "reminders.csv"
    path.write_text(
        ",".join(REMINDER_COLUMNS) + "\n"
        "rem-x,prop-x,exe-x,idem-x,SendRemind",  # torn final row
        encoding="utf-8",
    )
    adapter = CsvReminderAdapter(path)
    request = AdapterRequest(
        execution_id="exe-y",
        idempotency_key="idem-y",
        attempt=1,
        proposal_id="p",
        action="SendReminder",
        params={"target": "taro", "related_entity": "A01"},
        payload_digest="sha256:x",
        subject_entity="Job",
        subject_id="A01",
        related_entities=[],
        reason="x",
    )
    with pytest.raises(AdapterFailed):
        adapter.deliver(request)
    # Nothing appended.
    assert len(path.read_text().splitlines()) == 2


def test_send_reminder_without_optional_reason(tmp_path: Path) -> None:
    """reason is contract-optional: a proposal without it still
    delivers, with an empty reason column."""
    path = tmp_path / "reminders.csv"
    adapter = CsvReminderAdapter(path)
    request = AdapterRequest(
        execution_id="exe-x",
        idempotency_key="idem-x",
        attempt=1,
        proposal_id="p",
        action="SendReminder",
        params={"target": "taro", "related_entity": "A01"},
        payload_digest="sha256:x",
        subject_entity="Job",
        subject_id="A01",
        related_entities=[],
        reason="",
    )
    receipt = adapter.deliver(request)
    assert receipt["reminder_id"].startswith("rem-")
    with path.open("r", newline="", encoding="utf-8") as f:
        row = next(iter(csv.DictReader(f)))
    assert row["reason"] == ""


def test_execute_surfaces_adapter_error_in_result(tmp_path: Path) -> None:
    """A definite adapter failure is visible on the result, not only in
    the executions row."""
    workspace, proposal = make_proposal(tmp_path)
    approve(workspace, proposal)

    blocked = tmp_path / "blocked.csv"
    blocked.mkdir()  # a directory cannot be opened for append
    result = execute(
        workspace, proposal["proposal_id"], CsvReminderAdapter(blocked)
    )
    assert result.status in ("failed", "uncertain")
    assert result.error

    class CleanFail:
        name = "clean-fail"

        def deliver(self, request):
            raise AdapterFailed("destination unavailable")

    result = execute(
        workspace, proposal["proposal_id"], CleanFail()
    )
    assert result.status == "failed"
    assert result.error == "destination unavailable"


def test_execute_truncates_adapter_failure_message(tmp_path: Path) -> None:
    """An adapter-controlled failure message is capped before it is
    persisted or shown."""

    class Verbose:
        name = "verbose"

        def deliver(self, request):
            raise AdapterFailed("x" * 2000)

    workspace, proposal = make_proposal(tmp_path)
    approve(workspace, proposal)
    result = execute(
        workspace, proposal["proposal_id"], Verbose()
    )
    assert result.status == "failed"
    assert len(result.error) <= 512

    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    rows = store.executions_for_key(result.idempotency_key)
    assert len(rows[-1]["error"]) <= 512
