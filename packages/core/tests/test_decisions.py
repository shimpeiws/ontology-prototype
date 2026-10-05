"""Decision lifecycle tests: proposal persistence, decide, approval check."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
import support

from ontology_core import (
    ProviderRequest,
    SqliteCanonicalStore,
    check_approval,
    decide,
    ingest_snapshot,
    load_workspace,
    payload_digest,
    proposal_view,
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
    """Run a diagnosis that validates one proposal; return (ws, proposal)."""
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
        workspace, FixedProvider(output), model="m",
        run_id=projection.run_id,
    )
    assert result.ok, [d.format() for d in result.diagnostics]
    views = proposal_view(workspace)
    assert len(views) == 1
    return workspace, views[0]


def test_validated_proposals_are_persisted(tmp_path: Path) -> None:
    workspace, view = make_proposal(tmp_path)

    assert view["status"] == "proposed"
    assert view["action"] == "SendReminder"
    assert view["subject_entity"] == "Job"
    assert view["subject_id"] == "A01"
    assert view["related_entities"] == [
        {"param": "target", "entity": "Person", "id": "taro"}
    ]
    assert view["evidence"]["fact_key"].startswith("fact-")
    assert view["payload_digest"] == payload_digest(
        "SendReminder", view["params"]
    )
    assert view["proposal_id"].startswith(view["diagnosis_run_id"])
    assert view["latest_decision"] is None
    assert view["decisions"] == []


def test_rejected_proposals_are_not_persisted(tmp_path: Path) -> None:
    output = json.dumps(
        {
            "explanation": "x",
            "hypotheses": [],
            "follow_up_questions": [],
            "action_proposals": [
                {"action": "DeleteJob", "params": {}},
            ],
        }
    )
    workspace = make_ws(tmp_path)
    snapshot = tmp_path / "jobs.csv"
    snapshot.write_text(CSV_SAMPLE, encoding="utf-8")
    ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)
    projection = run_projection(
        workspace, "CompletedButNotInvoiced", as_of=AS_OF
    )
    result = run_diagnosis(
        workspace, FixedProvider(output), model="m",
        run_id=projection.run_id,
    )
    assert result.ok
    assert result.proposals == []
    assert len(result.rejected) == 1
    assert proposal_view(workspace) == []


def test_approve_then_check_passes(tmp_path: Path) -> None:
    workspace, view = make_proposal(tmp_path)

    result = decide(
        workspace, view["proposal_id"], "approved",
        reviewer="fde-sato", reason="looks right",
    )
    assert result.ok, [d.format() for d in result.diagnostics]
    assert result.recorded
    assert result.decision == "approved"

    check = check_approval(
        workspace,
        view["proposal_id"],
        action=view["action"],
        params=view["params"],
    )
    assert check.ok
    assert check.approved
    assert check.status == "approved"

    refreshed = proposal_view(workspace)[0]
    assert refreshed["status"] == "approved"
    latest = refreshed["latest_decision"]
    assert latest["reviewer"] == "fde-sato"
    assert latest["reason"] == "looks right"
    assert latest["decided_at"]
    assert latest["payload_digest"] == view["payload_digest"]


def test_reject_and_undecided_and_unknown_fail(tmp_path: Path) -> None:
    workspace, view = make_proposal(tmp_path)

    # Undecided (status proposed) fails.
    check = check_approval(
        workspace,
        view["proposal_id"],
        action=view["action"],
        params=view["params"],
    )
    assert not check.ok
    assert check.status == "proposed"
    assert "DECISION.NOT_APPROVED" in {
        d.code for d in check.diagnostics
    }

    # Rejected fails.
    assert decide(
        workspace, view["proposal_id"], "rejected",
        reviewer="fde-sato", reason="wrong target",
    ).ok
    check = check_approval(
        workspace,
        view["proposal_id"],
        action=view["action"],
        params=view["params"],
    )
    assert not check.ok
    assert check.status == "rejected"

    # Unknown id fails.
    check = check_approval(
        workspace, "prp-does-not-exist", action="SendReminder", params={}
    )
    assert not check.ok
    assert "DECISION.UNKNOWN_PROPOSAL" in {
        d.code for d in check.diagnostics
    }


def test_changed_payload_fails_approval(tmp_path: Path) -> None:
    workspace, view = make_proposal(tmp_path)
    decide(
        workspace, view["proposal_id"], "approved", reviewer="fde-sato"
    )

    # Same payload passes.
    assert check_approval(
        workspace,
        view["proposal_id"],
        action="SendReminder",
        params=view["params"],
    ).ok

    # Any param change fails — even one field.
    changed = dict(view["params"], reason="edited after approval")
    check = check_approval(
        workspace,
        view["proposal_id"],
        action="SendReminder",
        params=changed,
    )
    assert not check.ok
    assert "DECISION.PAYLOAD_CHANGED" in {
        d.code for d in check.diagnostics
    }

    # A different action on the same proposal id fails too.
    check = check_approval(
        workspace,
        view["proposal_id"],
        action="PageOnCall",
        params=view["params"],
    )
    assert not check.ok


def test_repeated_decisions_latest_wins(tmp_path: Path) -> None:
    workspace, view = make_proposal(tmp_path)
    pid = view["proposal_id"]

    decide(workspace, pid, "approved", reviewer="fde-sato")
    decide(workspace, pid, "rejected", reviewer="fde-ueda",
           reason="second look")
    assert not check_approval(
        workspace, pid, action=view["action"], params=view["params"]
    ).ok

    decide(workspace, pid, "approved", reviewer="fde-ueda",
           reason="final call")
    assert check_approval(
        workspace, pid, action=view["action"], params=view["params"]
    ).ok

    refreshed = proposal_view(workspace)[0]
    # History is preserved: all three decisions remain inspectable.
    assert len(refreshed["decisions"]) == 3
    assert refreshed["status"] == "approved"
    assert refreshed["decisions"][0]["reviewer"] == "fde-sato"


def test_decide_validation_failures(tmp_path: Path) -> None:
    workspace, view = make_proposal(tmp_path)
    pid = view["proposal_id"]

    # Missing/empty reviewer — an agent-originated "approval" has no
    # human identity to record and fails.
    for reviewer in ("", "   "):
        result = decide(workspace, pid, "approved", reviewer=reviewer)
        assert not result.ok
        assert not result.recorded
        assert "DECISION.NO_REVIEWER" in {
            d.code for d in result.diagnostics
        }

    # Invalid decision value.
    result = decide(workspace, pid, "maybe", reviewer="fde-sato")
    assert not result.ok
    assert "DECISION.INVALID_DECISION" in {
        d.code for d in result.diagnostics
    }

    # Unknown proposal.
    result = decide(
        workspace, "prp-nope", "approved", reviewer="fde-sato"
    )
    assert not result.ok
    assert "DECISION.UNKNOWN_PROPOSAL" in {
        d.code for d in result.diagnostics
    }

    # Nothing was recorded by any of these.
    assert not check_approval(
        workspace, pid, action=view["action"], params=view["params"]
    ).ok


def test_diagnosis_run_writes_no_decisions(tmp_path: Path) -> None:
    """Model output can propose, but the diagnosis pipeline itself never
    writes decisions — approval requires the explicit decide path."""
    output = json.dumps(
        {
            "explanation": "x",
            "hypotheses": [],
            "follow_up_questions": [],
            "action_proposals": [
                {
                    "action": "SendReminder",
                    "params": {
                        "target": "taro",
                        "related_entity": "A01",
                        "reason": "x",
                    },
                    "reason": "x",
                    # A model trying to masquerade as a human decision.
                    "approved": True,
                    "reviewer": "human",
                }
            ],
        }
    )
    workspace, _ = make_proposal(tmp_path, output)
    view = proposal_view(workspace)[0]
    assert view["status"] == "proposed"
    assert view["decisions"] == []
    assert not check_approval(
        workspace,
        view["proposal_id"],
        action=view["action"],
        params=view["params"],
    ).ok


def test_decisions_and_proposals_are_append_only(tmp_path: Path) -> None:
    workspace, view = make_proposal(tmp_path)
    decide(workspace, view["proposal_id"], "approved", reviewer="r")

    db = Path(workspace.path) / RUNTIME_DIR / "canon.db"
    conn = sqlite3.connect(db)
    pid = view["proposal_id"]
    for statement in (
        "UPDATE proposals SET action = 'x' WHERE proposal_id = ?",
        "DELETE FROM proposals WHERE proposal_id = ?",
        "UPDATE decisions SET decision = 'x' WHERE proposal_id = ?",
        "DELETE FROM decisions WHERE proposal_id = ?",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(statement, (pid,))
    conn.close()


def test_decision_trace_event(tmp_path: Path) -> None:
    workspace, view = make_proposal(tmp_path)
    trace_path = tmp_path / "trace.jsonl"
    result = decide(
        workspace, view["proposal_id"], "approved",
        reviewer="fde-sato",
        trace=JsonlTraceSink(trace_path),
    )
    assert result.ok
    events = [
        json.loads(line)
        for line in trace_path.read_text().splitlines()
    ]
    assert len(events) == 1
    assert events[0]["stage"] == "decision"
    assert events[0]["kind"] == "decision.recorded"
    assert events[0]["data"]["proposal_id"] == view["proposal_id"]


def test_proposals_do_not_mutate_upstream(tmp_path: Path) -> None:
    workspace, view = make_proposal(tmp_path)
    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    conn = store._connect()
    before = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("entities", "events", "projection_runs",
                      "diagnosis_runs")
    }
    decide(workspace, view["proposal_id"], "approved", reviewer="r")
    after = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("entities", "events", "projection_runs",
                      "diagnosis_runs")
    }
    store.close()
    assert before == after


def test_existing_db_gets_decision_tables(tmp_path: Path) -> None:
    workspace, view = make_proposal(tmp_path)
    db = Path(workspace.path) / RUNTIME_DIR / "canon.db"
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE proposals")
    conn.execute("DROP TABLE decisions")
    conn.commit()
    conn.close()

    # The missing tables — and their append-only triggers — are
    # recreated on next store open, and the dropped proposal row is
    # backfilled from its diagnosis run record: the decision succeeds
    # instead of losing reviewability forever.
    result = decide(
        workspace, view["proposal_id"], "approved", reviewer="r"
    )
    assert result.ok, [d.format() for d in result.diagnostics]
    assert result.recorded

    conn = sqlite3.connect(db)
    objects = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type IN ('table', 'trigger')"
        )
    }
    conn.close()
    assert {"proposals", "decisions"} <= objects
    assert {
        "proposals_no_update",
        "proposals_no_delete",
        "decisions_no_update",
        "decisions_no_delete",
    } <= objects


def test_proposal_index_refers_to_model_output_index(tmp_path: Path) -> None:
    """proposal_index/proposal_id track the model's raw output position,
    not the post-validation list — a persisted row points back at the
    recorded output it was validated from."""
    output = json.dumps(
        {
            "explanation": "x",
            "hypotheses": [],
            "follow_up_questions": [],
            "action_proposals": [
                {"action": "DeleteJob", "params": {}},  # index 0: rejected
                {
                    "action": "SendReminder",
                    "params": {
                        "target": "taro",
                        "related_entity": "A01",
                        "reason": "x",
                    },
                    "reason": "x",
                },  # index 1: accepted
            ],
        }
    )
    workspace, view = make_proposal(tmp_path, output)

    assert view["proposal_index"] == 1
    assert view["proposal_id"].endswith("-p1")


def test_decide_failures_emit_trace_event(tmp_path: Path) -> None:
    """A failed decision *attempt* is itself auditable: every decide()
    outcome — including validation failures — leaves a trace event."""
    workspace, view = make_proposal(tmp_path)
    trace_path = tmp_path / "trace.jsonl"
    sink = JsonlTraceSink(trace_path)

    decide(
        workspace, view["proposal_id"], "approved",
        reviewer="", trace=sink,
    )
    decide(
        workspace, view["proposal_id"], "maybe",
        reviewer="fde-sato", trace=sink,
    )
    decide(
        workspace, "prp-nope", "approved",
        reviewer="fde-sato", trace=sink,
    )

    events = [
        json.loads(line)
        for line in trace_path.read_text().splitlines()
    ]
    assert [e["kind"] for e in events] == ["decision.failed"] * 3
    # The attempted decision is visible even though nothing recorded.
    assert [e["data"]["decision"] for e in events] == [
        "approved",
        "maybe",
        "approved",
    ]


def test_decide_trace_error_is_diagnosed(tmp_path: Path) -> None:
    """A trace write failure surfaces as a warning, never a silent gap:
    a recorded decision without a trace event is not 'normal'."""

    class FailingTrace:
        def emit(self, stage, kind, data):
            raise OSError("disk full")

    workspace, view = make_proposal(tmp_path)
    result = decide(
        workspace, view["proposal_id"], "approved",
        reviewer="fde-sato", trace=FailingTrace(),
    )
    assert result.recorded
    assert "DECISION.TRACE_ERROR" in {
        d.code for d in result.diagnostics
    }


def test_check_approval_requires_payload_arguments(tmp_path: Path) -> None:
    """The executor gate binds to the payload about to execute —
    omitting action/params is a type error, never 'check whatever is
    stored'."""
    workspace, view = make_proposal(tmp_path)
    with pytest.raises(TypeError):
        check_approval(workspace, view["proposal_id"])


def test_latest_decision_uses_insertion_order(tmp_path: Path) -> None:
    """'Latest' means latest *recorded*, not latest wall-clock
    decided_at: a clock rewind must not resurrect an overridden
    decision."""
    workspace, view = make_proposal(tmp_path)
    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    pid = view["proposal_id"]
    store.record_decision(
        {
            "decision_id": "dec-newer-clock",
            "proposal_id": pid,
            "decision": "approved",
            "reviewer": "r",
            "reason": None,
            "payload_digest": view["payload_digest"],
            "decided_at": datetime(2026, 10, 6, tzinfo=timezone.utc),
        }
    )
    # Inserted later, but with an earlier wall-clock timestamp (clock
    # rewind): it still wins as the latest recorded decision.
    store.record_decision(
        {
            "decision_id": "dec-older-clock",
            "proposal_id": pid,
            "decision": "rejected",
            "reviewer": "r",
            "reason": None,
            "payload_digest": view["payload_digest"],
            "decided_at": datetime(2026, 10, 5, tzinfo=timezone.utc),
        }
    )

    assert store.decisions_for(pid)[-1]["decision_id"] == "dec-older-clock"
    refreshed = proposal_view(workspace)[0]
    assert refreshed["status"] == "rejected"
    assert refreshed["latest_decision"]["decision_id"] == "dec-older-clock"


def test_check_approval_revalidates_against_current_contract(
    tmp_path: Path,
) -> None:
    """Approving then editing the contract (action dropped from
    permitted_actions) must not leave a stale 'approved' verdict."""
    import dataclasses

    workspace, view = make_proposal(tmp_path)
    decide(
        workspace, view["proposal_id"], "approved", reviewer="fde-sato"
    )

    # Sanity: still approved under the unchanged contract.
    assert check_approval(
        workspace,
        view["proposal_id"],
        action=view["action"],
        params=view["params"],
    ).ok

    edited = dataclasses.replace(workspace.agent, permitted_actions={})
    changed_ws = dataclasses.replace(workspace, agent=edited)
    check = check_approval(
        changed_ws,
        view["proposal_id"],
        action=view["action"],
        params=view["params"],
    )
    assert not check.ok
    assert "DECISION.CONTRACT_CHANGED" in {
        d.code for d in check.diagnostics
    }


def test_record_diagnosis_run_rejects_legacy_store(tmp_path: Path) -> None:
    """A store implementing the pre-proposal_rows signature fails loudly
    (diagnostic + unrecorded run), never silently drops the run."""

    class LegacyStore(SqliteCanonicalStore):
        def record_diagnosis_run(self, record):  # no proposal_rows kwarg
            super().record_diagnosis_run(record)

    workspace = make_ws(tmp_path)
    snapshot = tmp_path / "jobs.csv"
    snapshot.write_text(CSV_SAMPLE, encoding="utf-8")
    ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)
    store = LegacyStore(Path(workspace.path) / RUNTIME_DIR)
    projection = run_projection(
        workspace, "CompletedButNotInvoiced", as_of=AS_OF
    )
    result = run_diagnosis(
        workspace, FixedProvider(GOOD_OUTPUT), model="m",
        run_id=projection.run_id, store=store,
    )

    assert not result.recorded
    assert "DIAGNOSIS.STORE_ERROR" in {
        d.code for d in result.diagnostics
    }
    assert store.diagnosis_run(result.run_id) is None


def test_proposal_row_failure_rolls_back_run_record(tmp_path: Path) -> None:
    """The run record and its proposal rows commit atomically: a
    proposal-insert failure leaves no half-recorded run."""

    class PoisonedStore(SqliteCanonicalStore):
        def record_diagnosis_run(self, record, *, proposal_rows=None):
            conn = self._connect()
            # Reproduce the shared-transaction write: run insert then a
            # proposal insert that violates the PK.
            with conn:
                conn.execute(
                    "INSERT INTO diagnosis_runs (run_id, provider, model,"
                    " prompt_version, prompt, status, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        record["run_id"], record["provider"],
                        record["model"], record["prompt_version"],
                        record["prompt"], record["status"],
                        record["created_at"].isoformat(),
                    ),
                )
                for i in range(2):
                    conn.execute(
                        "INSERT INTO proposals (proposal_id,"
                        " diagnosis_run_id, proposal_index, action,"
                        " params, payload_digest, created_at)"
                        " VALUES ('dup', ?, ?, 'a', '{}', 'd', ?)",
                        (
                            record["run_id"], i,
                            record["created_at"].isoformat(),
                        ),
                    )

    workspace = make_ws(tmp_path)
    snapshot = tmp_path / "jobs.csv"
    snapshot.write_text(CSV_SAMPLE, encoding="utf-8")
    ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)
    store = PoisonedStore(Path(workspace.path) / RUNTIME_DIR)
    projection = run_projection(
        workspace, "CompletedButNotInvoiced", as_of=AS_OF
    )
    result = run_diagnosis(
        workspace, FixedProvider(GOOD_OUTPUT), model="m",
        run_id=projection.run_id, store=store,
    )

    assert not result.recorded
    assert store.diagnosis_run(result.run_id) is None


def test_decide_store_errors_become_diagnostics(tmp_path: Path) -> None:
    """Store faults on the decide path become diagnostics, never
    tracebacks."""

    class BrokenReadStore(SqliteCanonicalStore):
        def proposal(self, proposal_id):
            raise RuntimeError("locked")

    workspace, view = make_proposal(tmp_path)
    result = decide(
        workspace, view["proposal_id"], "approved",
        reviewer="r",
        store=BrokenReadStore(Path(workspace.path) / RUNTIME_DIR),
    )
    assert not result.recorded
    assert "DECISION.STORE_ERROR" in {
        d.code for d in result.diagnostics
    }

    class BrokenWriteStore(SqliteCanonicalStore):
        def record_decision(self, record):
            raise RuntimeError("locked")

    result = decide(
        workspace, view["proposal_id"], "approved",
        reviewer="r",
        store=BrokenWriteStore(Path(workspace.path) / RUNTIME_DIR),
    )
    assert not result.recorded
    assert "DECISION.STORE_ERROR" in {
        d.code for d in result.diagnostics
    }


def test_no_candidate_diagnosis_run_is_still_recorded(
    tmp_path: Path,
) -> None:
    """A zero-candidate run still records: build_proposal_rows must not
    resolve the projection subject when there is nothing to index."""
    workspace = make_ws(tmp_path)
    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    result = run_diagnosis(
        workspace, FixedProvider(GOOD_OUTPUT), model="m",
        as_of=AS_OF, store=store,
    )

    assert result.run_id is not None
    assert result.recorded
    assert store.diagnosis_run(result.run_id) is not None


def test_payload_digest_is_version_stable(tmp_path: Path) -> None:
    """The digest recipe carries an explicit version tag, so a recipe
    change can be told apart from a payload change."""
    import hashlib

    digest = payload_digest("SendReminder", {"a": 1})
    raw = (
        "v1\n"
        + json.dumps(
            {"action": "SendReminder", "params": {"a": 1}},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    ).encode("utf-8")
    assert digest == "sha256:" + hashlib.sha256(raw).hexdigest()


def test_backfill_indexes_legacy_run_proposals(tmp_path: Path) -> None:
    """A run recorded before the proposals table existed still yields a
    reviewable, decidable proposal once inspected — the repair path."""
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
                        # No "index" key — pre-#6 records lack it; the
                        # array position is the fallback (old -pN scheme).
                    }
                ]
            ),
            "[]",
            "[]",
            "2026-10-04T00:00:00Z",
        ),
    )
    conn.commit()

    views = proposal_view(workspace)
    assert len(views) == 1
    view = views[0]
    assert view["proposal_id"] == "dgx-legacy-p0"
    assert view["proposal_index"] == 0
    assert view["evidence"] == {}
    assert view["subject_entity"] == "Job"
    assert view["subject_id"] == "A01"
    assert view["related_entities"] == [
        {"param": "target", "entity": "Person", "id": "taro"}
    ]

    # The backfilled proposal can be decided like any other.
    result = decide(
        workspace, "dgx-legacy-p0", "approved", reviewer="fde-sato"
    )
    assert result.ok, [d.format() for d in result.diagnostics]
    assert check_approval(
        workspace,
        "dgx-legacy-p0",
        action="SendReminder",
        params={
            "target": "taro",
            "related_entity": "A01",
            "reason": "x",
        },
    ).ok


def test_multiple_subject_refs_all_visible(tmp_path: Path) -> None:
    """An action with two refs to the projection subject keeps both
    targets visible: the first becomes subject_id, the rest related."""
    docs = support.base_docs()
    docs["agent"]["agent"]["permitted_actions"]["SendReminder"][
        "params"
    ]["also_job"] = {"type": "ref", "ref": "Job", "required": False}
    ws_dir = support.make_workspace(tmp_path / "ws", docs=docs)
    loaded = load_workspace(ws_dir)
    assert loaded.ok
    workspace = loaded.workspace

    snapshot = tmp_path / "jobs.csv"
    snapshot.write_text(
        "id,status,assignee,amount,completed_at,invoiced_at\n"
        "A01,completed,taro,1,2026-10-01,\n"
        "B02,completed,taro,1,2026-10-01,\n",
        encoding="utf-8",
    )
    assert ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW).ok
    projection = run_projection(
        workspace, "CompletedButNotInvoiced", as_of=AS_OF
    )
    assert projection.ok
    output = json.dumps(
        {
            "explanation": "x",
            "hypotheses": [],
            "follow_up_questions": [],
            "action_proposals": [
                {
                    "action": "SendReminder",
                    "params": {
                        "target": "taro",
                        "related_entity": "A01",
                        "also_job": "B02",
                        "reason": "x",
                    },
                    "reason": "x",
                }
            ],
        }
    )
    result = run_diagnosis(
        workspace, FixedProvider(output), model="m",
        run_id=projection.run_id,
    )
    assert result.ok, [d.format() for d in result.diagnostics]

    view = proposal_view(workspace)[0]
    assert view["subject_id"] == "A01"
    assert {tuple(sorted(r.items())) for r in view["related_entities"]} == {
        tuple(sorted({"param": "target", "entity": "Person",
                      "id": "taro"}.items())),
        tuple(sorted({"param": "also_job", "entity": "Job",
                      "id": "B02"}.items())),
    }


def test_backfill_uses_frozen_run_subject(tmp_path: Path) -> None:
    """A workspace edited since diagnosis cannot relabel a historical
    proposal: subject_entity comes from the frozen projection run."""
    import dataclasses

    workspace = make_ws(tmp_path)
    snapshot = tmp_path / "jobs.csv"
    snapshot.write_text(CSV_SAMPLE, encoding="utf-8")
    ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)
    projection = run_projection(
        workspace, "CompletedButNotInvoiced", as_of=AS_OF
    )
    assert projection.ok

    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    conn = store._connect()
    conn.execute(
        "INSERT INTO diagnosis_runs (run_id, projection_run_id, provider,"
        " model, prompt_version, prompt, status, input_digest,"
        " provider_called, deliverables, proposals, rejected, diagnostics,"
        " created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "dgx-legacy",
            projection.run_id,
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

    # The workspace projection now selects Person, but the frozen run
    # diagnosed Job candidates — the row must keep the historical truth.
    proj_spec = workspace.domain.projections[workspace.agent.projection]
    changed_domain = dataclasses.replace(
        workspace.domain,
        projections={
            workspace.agent.projection: dataclasses.replace(
                proj_spec, subject="Person"
            )
        },
    )
    changed_ws = dataclasses.replace(workspace, domain=changed_domain)

    view = proposal_view(changed_ws)[0]
    assert view["subject_entity"] == "Job"
    assert view["subject_id"] == "A01"


def test_backfill_recovers_raw_output_index(tmp_path: Path) -> None:
    """A legacy record without `index` gets the raw output position when
    it can be recovered unambiguously — a rejected earlier item no
    longer shifts the accepted proposal's `-pN`."""
    workspace = make_ws(tmp_path)
    snapshot = tmp_path / "jobs.csv"
    snapshot.write_text(CSV_SAMPLE, encoding="utf-8")
    ingest_snapshot(workspace, snapshot, observed_at=FIXED_NOW)
    store = SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)
    proposal_params = {
        "target": "taro",
        "related_entity": "A01",
        "reason": "x",
    }
    raw_output = json.dumps(
        {
            "explanation": "x",
            "hypotheses": [],
            "follow_up_questions": [],
            "action_proposals": [
                {"action": "DeleteJob", "params": {}},  # raw index 0: rejected
                {
                    "action": "SendReminder",
                    "params": proposal_params,
                    "reason": "x",
                    "approved": True,  # stripped at validation time
                },  # raw index 1: accepted
            ],
        }
    )
    conn = store._connect()
    conn.execute(
        "INSERT INTO diagnosis_runs (run_id, provider, model,"
        " prompt_version, prompt, status, input_digest, provider_called,"
        " output, deliverables, proposals, rejected, diagnostics,"
        " created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "dgx-legacy",
            "legacy-provider",
            "m",
            "diagnosis.v1",
            "prompt",
            "ok",
            "sha256:legacy",
            1,
            raw_output,
            "{}",
            json.dumps(
                [
                    {
                        "action": "SendReminder",
                        "params": proposal_params,
                        "reason": "x",
                        # validated record: no "index", stripped extras
                    }
                ]
            ),
            json.dumps([{"index": 0, "item": {"action": "DeleteJob",
                                              "params": {}},
                         "reason": "not permitted"}]),
            "[]",
            "2026-10-04T00:00:00Z",
        ),
    )
    conn.commit()

    view = proposal_view(workspace)[0]
    assert view["proposal_index"] == 1
    assert view["proposal_id"] == "dgx-legacy-p1"
