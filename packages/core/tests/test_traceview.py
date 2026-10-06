"""Trace assembly: linked inspection and export across the whole loop.

One run must be followable from source observation through the outcome
report — including the failed paths — with every link resolvable or
explicitly flagged, and without the workspace definitions being loaded.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import support

from ontology_core import (
    AdapterFailed,
    CsvReminderAdapter,
    ProviderRequest,
    SqliteCanonicalStore,
    assemble_trace,
    decide,
    execute,
    export_bundle,
    ingest_snapshot,
    load_workspace,
    run_diagnosis,
    run_evaluation,
    run_projection,
    trace_anchor,
    trace_overview,
    trace_subject,
    write_export,
)
from ontology_core.diagnosis import ProviderError
from ontology_core.evidence import RUNTIME_DIR

FIXED_NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
AS_OF = datetime(2026, 10, 5, 0, 0, 0, tzinfo=timezone.utc)
BOUNDARY = datetime(2026, 10, 5, 0, 0, 0, tzinfo=timezone.utc)

CSV_SAMPLE = (
    "id,status,assignee,amount,completed_at,invoiced_at\n"
    "A01,completed,taro,1,2026-10-01,\n"
    "D04,completed,hanako,1,2026-10-01,2026-10-04\n"
)

GOOD_OUTPUT = json.dumps(
    {
        "explanation": "Job A01 has been completed for 4 days without an invoice.",
        "hypotheses": [
            {
                "statement": "The assignee may not have been notified.",
                "evidence": [],
            }
        ],
        "follow_up_questions": ["Is invoicing a manual step?"],
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

MIXED_OUTPUT = json.dumps(
    {
        "explanation": "x",
        "hypotheses": [],
        "follow_up_questions": [],
        "action_proposals": [
            {"action": "DeleteJob", "params": {}},
            {
                "action": "SendReminder",
                "params": {
                    "target": "taro",
                    "related_entity": "A01",
                    "reason": "still valid",
                },
            },
        ],
    }
)


class FixedProvider:
    name = "fixed-double"

    def __init__(self, output: str) -> None:
        self.output = output

    def complete(self, request: ProviderRequest) -> str:
        return self.output


class FailingProvider:
    name = "failing-double"

    def complete(self, request: ProviderRequest) -> str:
        raise ProviderError("connection refused")


class FailThenSucceed:
    name = "fail-then-succeed"

    def __init__(self, inner: CsvReminderAdapter) -> None:
        self.inner = inner
        self.calls = 0

    def deliver(self, request) -> dict:
        self.calls += 1
        if self.calls == 1:
            raise AdapterFailed("destination unavailable before write")
        return self.inner.deliver(request)


def make_ws(tmp_path: Path):
    ws_dir = support.make_workspace(tmp_path / "ws")
    result = load_workspace(ws_dir)
    assert result.ok, [d.format() for d in result.diagnostics]
    return result.workspace


def open_store(workspace) -> SqliteCanonicalStore:
    return SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)


def reminders_path(workspace) -> Path:
    return Path(workspace.path) / RUNTIME_DIR / "reminders.csv"


def _ingest(workspace, tmp_path: Path, name: str, text: str, observed_at):
    snapshot = tmp_path / name
    snapshot.write_text(text, encoding="utf-8")
    result = ingest_snapshot(workspace, snapshot, observed_at=observed_at)
    assert result.ok, [d.format() for d in result.diagnostics]
    return result


def _proposal_created(proposal) -> datetime:
    return datetime.fromisoformat(
        proposal["created_at"].replace("Z", "+00:00")
    ).astimezone(timezone.utc)


def make_loop(tmp_path: Path):
    """Run the full loop once and return the workspace plus key ids.

    ingest -> project -> diagnose -> approve -> execute -> re-observe the
    invoice -> evaluate. The re-observation lands one InvoiceIssued fact
    correlated with the proposal, so the subject chain is complete.
    """
    workspace = make_ws(tmp_path)
    _ingest(workspace, tmp_path, "jobs.csv", CSV_SAMPLE, FIXED_NOW)
    projection = run_projection(
        workspace, "CompletedButNotInvoiced", as_of=AS_OF
    )
    assert projection.ok
    diagnosis = run_diagnosis(
        workspace, FixedProvider(GOOD_OUTPUT), model="m",
        run_id=projection.run_id,
    )
    assert diagnosis.ok, [d.format() for d in diagnosis.diagnostics]
    (proposal,) = open_store(workspace).proposals()
    decided = decide(
        workspace, proposal["proposal_id"], "approved",
        reviewer="fde-sato",
    )
    assert decided.ok
    executed = execute(
        workspace, proposal["proposal_id"],
        CsvReminderAdapter(reminders_path(workspace)),
    )
    assert executed.ok, [d.format() for d in executed.diagnostics]

    # The invoice event must postdate the proposal or it cannot be a
    # later observation of the intervention (day granularity).
    occurred_on = _proposal_created(proposal).date() + timedelta(days=1)
    later = _ingest(
        workspace, tmp_path, "jobs-later.csv",
        "id,status,assignee,amount,completed_at,invoiced_at\n"
        f"A01,completed,taro,1,2026-10-01,{occurred_on.isoformat()}\n"
        "D04,completed,hanako,1,2026-10-01,2026-10-04\n",
        datetime.combine(
            occurred_on, datetime.min.time(), tzinfo=timezone.utc
        )
        + timedelta(hours=12),
    )
    evaluated = run_evaluation(
        workspace,
        as_of=datetime.combine(
            occurred_on + timedelta(days=10),
            datetime.min.time(),
            tzinfo=timezone.utc,
        ),
        boundary=BOUNDARY,
    )
    assert evaluated.ok, [d.format() for d in evaluated.diagnostics]
    return {
        "workspace": workspace,
        "projection_run_id": projection.run_id,
        "diagnosis_run_id": diagnosis.run_id,
        "proposal_id": proposal["proposal_id"],
        "execution_id": executed.execution_id,
        "idempotency_key": executed.idempotency_key,
        "evaluation_run_id": evaluated.run_id,
        "later_ingest_id": later.ingest_id,
    }


def ids_of(asm) -> set[str]:
    return {link["from"] for link in asm.links} | {
        link["to"] for link in asm.links
    }


def test_full_loop_links_every_stage(tmp_path: Path) -> None:
    ctx = make_loop(tmp_path)
    asm = assemble_trace(Path(ctx["workspace"].path))

    assert asm.ok, [d.format() for d in asm.diagnostics]
    assert asm.unresolved == []
    overview = trace_overview(asm)
    assert overview["proposals"]["by_status"] == {"approved": 1}
    assert overview["executions"]["by_status"] == {
        "attempting": 1,
        "succeeded": 1,
    }
    assert [r["run_id"] for r in overview["evaluation_runs"]] == [
        ctx["evaluation_run_id"]
    ]

    # Every stage id lands in the link graph.
    for artifact_id in (
        ctx["projection_run_id"],
        ctx["diagnosis_run_id"],
        ctx["proposal_id"],
        ctx["execution_id"],
        ctx["idempotency_key"],
        ctx["evaluation_run_id"],
        ctx["later_ingest_id"],
    ):
        assert artifact_id in ids_of(asm), artifact_id


def test_subject_view_follows_one_run_end_to_end(tmp_path: Path) -> None:
    ctx = make_loop(tmp_path)
    asm = assemble_trace(Path(ctx["workspace"].path))

    view = trace_subject(asm, "Job", "A01")

    assert view["unresolved"] == []
    assert [f["event"] for f in view["facts"]] == [
        "JobCompleted",
        "InvoiceIssued",
    ]
    # Observed: raw observation evidence stays attached to each fact.
    completed = view["facts"][0]
    assert completed["observation_ids"]
    assert completed["ingest_ids"]
    assert view["observations"]
    assert view["snapshots"]
    # Derived: the subject was the projection's candidate.
    assert [p["run_id"] for p in view["projections"]] == [
        ctx["projection_run_id"]
    ]
    assert view["projections"][0]["role"] == "candidate"
    # AI: the diagnosis that proposed for this subject.
    assert [d["run_id"] for d in view["diagnoses"]] == [
        ctx["diagnosis_run_id"]
    ]
    (proposal,) = view["proposals"]
    assert proposal["proposal_id"] == ctx["proposal_id"]
    assert proposal["status"] == "approved"
    # Human: the approval is bound to the proposal.
    (decision,) = view["decisions"]
    assert decision["decision"] == "approved"
    assert decision["reviewer"] == "fde-sato"
    # Write-back: attempt rows keep attempting + succeeded with receipt.
    assert [a["status"] for a in view["executions"]] == [
        "attempting",
        "succeeded",
    ]
    assert view["executions"][-1]["receipt"]["reminder_id"]
    # The later fact is correlated with the intervention.
    invoice = view["facts"][1]
    assert invoice["fact_key"] in proposal["correlated_facts"]
    # Evaluated: the frozen report carries the subject's pair.
    (evaluation,) = view["evaluations"]
    assert evaluation["run_id"] == ctx["evaluation_run_id"]
    assert evaluation["subject"]["disposition"] == "paired"
    # The journal slice covers every stage that ran for this subject.
    stages = {e["stage"] for e in view["journal"]}
    assert {
        "ingest",
        "projection",
        "diagnosis",
        "decision",
        "execution",
        "evaluation",
    } <= stages


def test_failed_model_call_stays_inspectable(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    _ingest(workspace, tmp_path, "jobs.csv", CSV_SAMPLE, FIXED_NOW)
    projection = run_projection(
        workspace, "CompletedButNotInvoiced", as_of=AS_OF
    )
    diagnosis = run_diagnosis(
        workspace, FailingProvider(), model="m", run_id=projection.run_id
    )
    assert not diagnosis.ok
    assert diagnosis.status == "failed"

    asm = assemble_trace(Path(workspace.path))
    run = trace_anchor(asm, diagnosis.run_id)
    assert run is not None
    assert run["kind"] == "diagnosis_run"
    assert run["nature"] == "ai"
    assert run["record"]["status"] == "failed"
    assert run["record"]["provider"] == "failing-double"
    assert run["record"]["output"] is None
    assert any(
        d["code"] == "DIAGNOSIS.PROVIDER_ERROR"
        for d in run["record"]["diagnostics"]
    )
    # The journal keeps the failed-run event too.
    assert any(
        e["kind"] == "diagnosis.failed" and e["data"]["run_id"] == diagnosis.run_id
        for e in run["journal"]
    )


def test_rejected_proposals_and_rejection_stay_visible(
    tmp_path: Path,
) -> None:
    workspace = make_ws(tmp_path)
    _ingest(workspace, tmp_path, "jobs.csv", CSV_SAMPLE, FIXED_NOW)
    projection = run_projection(
        workspace, "CompletedButNotInvoiced", as_of=AS_OF
    )
    diagnosis = run_diagnosis(
        workspace, FixedProvider(MIXED_OUTPUT), model="m",
        run_id=projection.run_id,
    )
    assert diagnosis.ok

    asm = assemble_trace(Path(workspace.path))
    run = trace_anchor(asm, diagnosis.run_id)
    (rejected,) = run["record"]["rejected"]
    assert rejected["item"]["action"] == "DeleteJob"
    assert "permitted_actions" in rejected["reason"]

    # Reject the validated proposal: the human decision is part of the
    # trace, and nothing downstream hides it.
    (proposal,) = open_store(workspace).proposals()
    decide(
        workspace, proposal["proposal_id"], "rejected",
        reviewer="fde-sato", reason="not worth a reminder",
    )
    asm = assemble_trace(Path(workspace.path))
    assert asm.ok
    anchor = trace_anchor(asm, proposal["proposal_id"])
    assert anchor["record"]["status"] == "rejected"
    (decision,) = anchor["record"]["decisions"]
    assert decision["decision"] == "rejected"
    assert anchor["record"]["executions"] == []


def test_failed_execution_attempts_stay_visible(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    _ingest(workspace, tmp_path, "jobs.csv", CSV_SAMPLE, FIXED_NOW)
    projection = run_projection(
        workspace, "CompletedButNotInvoiced", as_of=AS_OF
    )
    diagnosis = run_diagnosis(
        workspace, FixedProvider(GOOD_OUTPUT), model="m",
        run_id=projection.run_id,
    )
    (proposal,) = open_store(workspace).proposals()
    decide(
        workspace, proposal["proposal_id"], "approved",
        reviewer="fde-sato",
    )
    adapter = FailThenSucceed(CsvReminderAdapter(reminders_path(workspace)))
    first = execute(workspace, proposal["proposal_id"], adapter)
    assert first.status == "failed"
    second = execute(workspace, proposal["proposal_id"], adapter)
    assert second.ok

    asm = assemble_trace(Path(workspace.path))
    anchor = trace_anchor(asm, first.execution_id)
    assert anchor["kind"] == "execution"
    assert [a["status"] for a in anchor["record"]["attempts"]] == [
        "attempting",
        "failed",
        "attempting",
        "succeeded",
    ]
    assert anchor["record"]["attempts"][1]["error"] == (
        "destination unavailable before write"
    )
    # The idempotency key resolves to the same execution.
    by_key = trace_anchor(asm, first.idempotency_key)
    assert by_key["record"]["execution_id"] == first.execution_id


def test_uncertain_execution_is_distinct(tmp_path: Path) -> None:
    class WriteThenCrash:
        name = "write-then-crash"

        def __init__(self, inner):
            self.inner = inner

        def deliver(self, request):
            self.inner.deliver(request)
            raise RuntimeError("simulated crash after write")

    workspace = make_ws(tmp_path)
    _ingest(workspace, tmp_path, "jobs.csv", CSV_SAMPLE, FIXED_NOW)
    projection = run_projection(
        workspace, "CompletedButNotInvoiced", as_of=AS_OF
    )
    run_diagnosis(
        workspace, FixedProvider(GOOD_OUTPUT), model="m",
        run_id=projection.run_id,
    )
    (proposal,) = open_store(workspace).proposals()
    decide(
        workspace, proposal["proposal_id"], "approved",
        reviewer="fde-sato",
    )
    result = execute(
        workspace, proposal["proposal_id"],
        WriteThenCrash(CsvReminderAdapter(reminders_path(workspace))),
    )
    assert result.status == "uncertain"

    asm = assemble_trace(Path(workspace.path))
    anchor = trace_anchor(asm, result.execution_id)
    statuses = [a["status"] for a in anchor["record"]["attempts"]]
    assert statuses == ["attempting", "uncertain"]
    assert anchor["record"]["attempts"][-1]["receipt"] is None


def test_unavailable_evaluation_is_inspectable(tmp_path: Path) -> None:
    # The guardrail has no invoice evidence and the after cohort is
    # empty: the report records unavailability rather than hiding it.
    workspace = make_ws(tmp_path)
    _ingest(workspace, tmp_path, "jobs.csv", CSV_SAMPLE, FIXED_NOW)
    evaluated = run_evaluation(
        workspace,
        as_of=datetime(2026, 10, 15, tzinfo=timezone.utc),
        boundary=BOUNDARY,
    )
    assert evaluated.ok

    asm = assemble_trace(Path(workspace.path))
    anchor = trace_anchor(asm, evaluated.run_id)
    report = anchor["record"]["report"]
    assert report["guardrails"][0]["status"] == "unavailable"
    assert report["target"]["status"] == "unverified"
    assert report["overall"]["status"] == "unverified"
    assert anchor["nature"] == "evaluated"


def test_export_preserves_historical_inputs(tmp_path: Path) -> None:
    # After the source CSV is deleted and the domain definition is
    # broken, the exported bundle still carries the observations,
    # snapshot bytes, and frozen runs that the history ran on.
    ctx = make_loop(tmp_path)
    workspace = ctx["workspace"]
    bundle = export_bundle(assemble_trace(Path(workspace.path)))
    assert bundle["format"] == "ontology-trace"

    # Delete the source snapshot copies and break the definitions.
    for yaml_file in Path(workspace.path).glob("*.yaml"):
        yaml_file.write_text("not: [valid\n", encoding="utf-8")
    (tmp_path / "jobs.csv").unlink()
    (tmp_path / "jobs-later.csv").unlink()

    later = export_bundle(assemble_trace(Path(workspace.path)))
    assert later["format_version"] == 1
    later.pop("exported_at")
    bundle.pop("exported_at")
    assert later == bundle
    # Snapshot contents are embedded, not just digests.
    assert len(bundle["snapshots"]) == 2
    for snapshot in bundle["snapshots"]:
        assert "content" in snapshot or "content_base64" in snapshot
    assert bundle["projection_runs"][0]["run_id"] == (
        ctx["projection_run_id"]
    )


def test_export_refuses_to_overwrite(tmp_path: Path) -> None:
    ctx = make_loop(tmp_path)
    asm = assemble_trace(Path(ctx["workspace"].path))
    target = tmp_path / "trace-export.json"
    write_export(asm, target)
    with pytest.raises(FileExistsError):
        write_export(asm, target)
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["format"] == "ontology-trace"
    assert written["evaluation_runs"][0]["run_id"] == (
        ctx["evaluation_run_id"]
    )


def test_unresolved_reference_is_flagged(tmp_path: Path) -> None:
    ctx = make_loop(tmp_path)
    workspace = ctx["workspace"]
    # A link pointing at a proposal that was never recorded — the trace
    # must flag the dangling reference, not silently drop it.
    fact_key = next(
        f["fact_key"]
        for f in assemble_trace(Path(workspace.path)).facts
        if f["event"] == "InvoiceIssued" and f["subject"]["id"] == "A01"
    )
    store = open_store(workspace)
    conn = store._connect()
    conn.execute(
        """
        INSERT INTO fact_links (
            fact_key, relation, target, ingest_id, observation_id,
            created_at
        ) VALUES (?, 'correlated_intervention', 'dgx-nope-p0',
                  'ing-x', 'obs-x', '2026-10-06T00:00:00Z')
        """,
        (fact_key,),
    )
    conn.commit()

    asm = assemble_trace(Path(workspace.path))
    dangling = [
        link
        for link in asm.unresolved
        if link["to"] == "dgx-nope-p0"
    ]
    assert len(dangling) == 1
    assert any(
        d.code == "TRACE.UNRESOLVED_LINK" for d in asm.diagnostics
    )
    assert trace_anchor(asm, "dgx-nope-p0") is None


def test_trace_is_read_only_on_empty_runtime(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    asm = assemble_trace(Path(workspace.path))
    overview = trace_overview(asm)
    assert overview["journal"]["events"] == 0
    assert overview["canonical"]["facts"] == 0
    # Inspection must not create the runtime directory or canon.db.
    assert not (Path(workspace.path) / RUNTIME_DIR).exists()


def test_unknown_anchor_reports_none(tmp_path: Path) -> None:
    ctx = make_loop(tmp_path)
    asm = assemble_trace(Path(ctx["workspace"].path))
    assert trace_anchor(asm, "dgx-does-not-exist") is None
    assert trace_anchor(asm, "Job/nobody") is None


def test_anchor_views_cover_every_kind(tmp_path: Path) -> None:
    ctx = make_loop(tmp_path)
    asm = assemble_trace(Path(ctx["workspace"].path))

    fact = next(
        f
        for f in asm.facts
        if f["event"] == "InvoiceIssued" and f["subject"]["id"] == "A01"
    )
    ingest_id = fact["ingest_id"]
    obs_id = fact["first_observation_id"]
    view = trace_anchor(asm, obs_id)
    assert view["kind"] == "observation"
    assert view["record"]["status"] == "accepted"

    view = trace_anchor(asm, ingest_id)
    assert view["kind"] == "ingest_run"
    assert view["record"]["accepted"] >= 1

    view = trace_anchor(asm, fact["fact_key"])
    assert view["kind"] == "fact"
    assert view["nature"] == "observed"
    assert any(
        link["to"] == ctx["proposal_id"]
        and link["via"] == "correlated_intervention"
        for link in view["links"]
    )

    view = trace_anchor(asm, "Job/A01")
    assert view["kind"] == "entity"
    assert "Job/A01" in view["subjects"]

    view = trace_anchor(asm, ctx["projection_run_id"])
    assert view["kind"] == "projection_run"
    assert view["nature"] == "derived"
    # A01 is the candidate; D04 appears as a run input.
    assert view["subjects"] == ["Job/A01", "Job/D04"]

    view = trace_anchor(asm, ctx["proposal_id"])
    assert view["kind"] == "proposal"
    assert view["nature"] == "ai"

    (decision,) = [
        d for p in asm.proposals for d in p["decisions"]
    ]
    view = trace_anchor(asm, decision["decision_id"])
    assert view["kind"] == "decision"
    assert view["nature"] == "human"

    view = trace_anchor(asm, ctx["evaluation_run_id"])
    assert view["kind"] == "evaluation_run"
    assert view["nature"] == "evaluated"

    snapshot = asm.snapshots[0]
    view = trace_anchor(asm, snapshot["digest"])
    assert view["kind"] == "snapshot"


def test_journal_only_anchor_for_unpersisted_id(tmp_path: Path) -> None:
    # An id that only ever appears in the journal — e.g. a run that
    # failed before freezing — stays inspectable through its events.
    ctx = make_loop(tmp_path)
    runtime = Path(ctx["workspace"].path) / RUNTIME_DIR
    journal = runtime / "trace.jsonl"
    with journal.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": "2026-10-06T00:00:00Z",
                    "stage": "projection",
                    "kind": "projection.failed",
                    "data": {
                        "run_id": "prj-crashed",
                        "projection": "CompletedButNotInvoiced",
                        "error": "boom",
                    },
                }
            )
            + "\n"
        )
    asm = assemble_trace(Path(ctx["workspace"].path))
    view = trace_anchor(asm, "prj-crashed")
    assert view["kind"] == "journal_only"
    assert view["record"]["journal_events"][0]["data"]["error"] == "boom"


def test_corrupt_journal_line_degrades_to_warning(tmp_path: Path) -> None:
    ctx = make_loop(tmp_path)
    journal = Path(ctx["workspace"].path) / RUNTIME_DIR / "trace.jsonl"
    with journal.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")

    asm = assemble_trace(Path(ctx["workspace"].path))
    assert asm.journal_errors
    assert any(
        d.code == "TRACE.JOURNAL_CORRUPT" for d in asm.diagnostics
    )
    # A warning does not fail the assembly; the rest stays inspectable.
    assert asm.ok
    assert trace_anchor(asm, ctx["proposal_id"]) is not None


def test_assembly_does_not_mutate_runtime(tmp_path: Path) -> None:
    ctx = make_loop(tmp_path)
    runtime = Path(ctx["workspace"].path) / RUNTIME_DIR

    def fingerprint() -> dict:
        entries = {}
        for path in sorted(runtime.rglob("*")):
            if path.is_file():
                entries[str(path.relative_to(runtime))] = (
                    path.stat().st_size,
                    path.read_bytes(),
                )
        return entries

    before = fingerprint()
    asm = assemble_trace(Path(ctx["workspace"].path))
    trace_overview(asm)
    trace_subject(asm, "Job", "A01")
    trace_anchor(asm, ctx["proposal_id"])
    export_bundle(asm)
    assert fingerprint() == before
