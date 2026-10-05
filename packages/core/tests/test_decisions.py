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

    check = check_approval(workspace, view["proposal_id"])
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
    check = check_approval(workspace, view["proposal_id"])
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
    check = check_approval(workspace, view["proposal_id"])
    assert not check.ok
    assert check.status == "rejected"

    # Unknown id fails.
    check = check_approval(workspace, "prp-does-not-exist")
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
    assert not check_approval(workspace, pid).ok

    decide(workspace, pid, "approved", reviewer="fde-ueda",
           reason="final call")
    assert check_approval(workspace, pid).ok

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
    assert not check_approval(workspace, pid).ok


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
    assert not check_approval(workspace, view["proposal_id"]).ok


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

    # The missing tables are recreated on next store open.
    result = decide(
        workspace, view["proposal_id"], "approved", reviewer="r"
    )
    # The proposal row is gone with the dropped table, so the decision
    # fails cleanly — the point is no crash and tables exist again.
    assert not result.ok
    assert "DECISION.UNKNOWN_PROPOSAL" in {
        d.code for d in result.diagnostics
    }
