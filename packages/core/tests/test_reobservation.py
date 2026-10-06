"""Re-observation tests (#8): later snapshots add only new facts,
conflicting timestamps are retained with ambiguity diagnostics, and new
facts link to interventions on the same subject as correlation.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import support

from ontology_core import (
    CsvReminderAdapter,
    Observation,
    ProviderRequest,
    ResolvedEntity,
    ResolvedEvent,
    SqliteCanonicalStore,
    decide,
    execute,
    ingest_snapshot,
    load_workspace,
    run_diagnosis,
    run_projection,
)
from ontology_core.evidence import RUNTIME_DIR

FIXED_NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
EVEN_LATER = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
AS_OF = datetime(2026, 10, 5, 0, 0, 0, tzinfo=timezone.utc)

HEADER = "id,status,assignee,amount,completed_at,invoiced_at\n"

INITIAL_CSV = (
    HEADER
    + "0101,completed,taro,42.5,2026-10-01,\n"
    + "102,in_progress,hanako,,,\n"
)

# Job 0101 got invoiced on 2026-10-05; job 102 is unchanged.
LATER_CSV = (
    HEADER
    + "0101,completed,taro,42.5,2026-10-01,2026-10-05\n"
    + "102,in_progress,hanako,,,\n"
)

GOOD_OUTPUT = json.dumps(
    {
        "explanation": "Job 0101 has been completed for 4 days uninvoiced.",
        "hypotheses": [],
        "follow_up_questions": [],
        "action_proposals": [
            {
                "action": "SendReminder",
                "params": {
                    "target": "taro",
                    "related_entity": "0101",
                    "reason": "Job exceeded the two-day threshold.",
                },
                "reason": "Candidate is the oldest uninvoiced job.",
            }
        ],
    }
)


class FixedProvider:
    name = "fixed-double"

    def complete(self, request: ProviderRequest) -> str:
        return GOOD_OUTPUT


def make_ws(tmp_path: Path):
    ws_dir = support.make_workspace(tmp_path / "ws")
    result = load_workspace(ws_dir)
    assert result.ok, [d.format() for d in result.diagnostics]
    return result.workspace


def write_snapshot(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def open_store(workspace) -> SqliteCanonicalStore:
    return SqliteCanonicalStore(Path(workspace.path) / RUNTIME_DIR)


def facts(workspace, entity: str, entity_id: str):
    return open_store(workspace).events_for_subject(entity, entity_id)


def codes(result) -> list[str]:
    return [d.code for d in result.diagnostics]


def ingest(workspace, tmp_path, name: str, text: str, observed_at):
    snapshot = write_snapshot(tmp_path, name, text)
    return ingest_snapshot(workspace, snapshot, observed_at=observed_at)


def make_proposal(workspace, tmp_path):
    """Persist one SendReminder proposal for Job/0101 via the real
    diagnose path, and return its persisted row."""
    projection = run_projection(
        workspace, "CompletedButNotInvoiced", as_of=AS_OF
    )
    assert projection.ok
    result = run_diagnosis(
        workspace, FixedProvider(), model="m", run_id=projection.run_id
    )
    assert result.ok, [d.format() for d in result.diagnostics]
    rows = open_store(workspace).proposals()
    (proposal,) = rows
    return proposal


def later_snapshot_after_proposal(proposal):
    created_at = datetime.fromisoformat(
        proposal["created_at"].replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    occurred_on = created_at.date() + timedelta(days=1)
    snapshot = LATER_CSV.replace("2026-10-05", occurred_on.isoformat())
    observed_at = datetime.combine(
        occurred_on, datetime.min.time(), tzinfo=timezone.utc
    ) + timedelta(hours=12)
    return snapshot, observed_at


def test_later_snapshot_appends_only_new_facts(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    first = ingest(workspace, tmp_path, "jobs.csv", INITIAL_CSV, FIXED_NOW)
    assert first.ok

    later = ingest(workspace, tmp_path, "jobs-later.csv", LATER_CSV, LATER)

    assert later.ok, [d.format() for d in later.diagnostics]
    assert later.canonical is not None
    # Only the new fact lands: one InvoiceIssued for Job/0101.
    assert later.canonical.events_appended == 1
    assert later.canonical.events_reobserved == 1  # JobCompleted re-asserted
    job_facts = facts(workspace, "Job", "0101")
    assert [f.event for f in job_facts] == ["JobCompleted", "InvoiceIssued"]
    invoice = job_facts[1]
    assert invoice.occurred_at == datetime(2026, 10, 5, tzinfo=timezone.utc)
    assert invoice.ingest_id == later.ingest_id
    # No correction is flagged: no earlier InvoiceIssued exists.
    assert "CANON.POSSIBLE_CORRECTION" not in codes(later)


def test_repeated_later_snapshot_preserves_events_adds_provenance(
    tmp_path: Path,
) -> None:
    workspace = make_ws(tmp_path)
    ingest(workspace, tmp_path, "jobs.csv", INITIAL_CSV, FIXED_NOW)
    second = ingest(workspace, tmp_path, "jobs-later.csv", LATER_CSV, LATER)
    links_after_second = len(second.canonical.links)

    repeat = ingest(workspace, tmp_path, "jobs-later.csv", LATER_CSV, EVEN_LATER)

    assert repeat.ok
    assert repeat.canonical is not None
    # Event history is unchanged: both known facts are re-observed, none
    # are appended, and no new links are recorded for a re-observation.
    assert repeat.canonical.events_appended == 0
    assert repeat.canonical.events_reobserved == 2
    assert repeat.canonical.links == []
    job_facts = facts(workspace, "Job", "0101")
    assert [f.event for f in job_facts] == ["JobCompleted", "InvoiceIssued"]
    # The repeat observation links to the existing fact — that is the
    # provenance a repeated snapshot adds.
    store = open_store(workspace)
    invoice = job_facts[1]
    observation_ids = {obs for obs, _ in store.event_links(invoice.fact_key)}
    assert len(observation_ids) == 2
    assert len(store.fact_links(invoice.fact_key)) == links_after_second


def test_changed_timestamp_keeps_both_facts_and_warns(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    invoiced = HEADER + "0101,completed,taro,42.5,2026-10-01,2026-10-04\n"
    ingest(workspace, tmp_path, "jobs.csv", invoiced, FIXED_NOW)
    before = {f.fact_key for f in facts(workspace, "Job", "0101")}

    # The source corrects invoiced_at from 2026-10-04 to 2026-10-05.
    corrected = ingest(
        workspace, tmp_path, "jobs-corrected.csv", LATER_CSV, LATER
    )

    # INV-002: a warning, not an error — the run succeeds.
    assert corrected.ok
    warnings = [
        d for d in corrected.diagnostics if d.code == "CANON.POSSIBLE_CORRECTION"
    ]
    assert len(warnings) == 1
    assert "2026-10-04" in warnings[0].message
    assert "2026-10-05" in warnings[0].message

    # INV-001: the correction lands as a new fact; the earlier fact is
    # retained untouched.
    all_facts = facts(workspace, "Job", "0101")
    assert len(all_facts) == len(before) + 1
    new_fact = next(f for f in all_facts if f.fact_key not in before)
    assert new_fact.event == "InvoiceIssued"
    assert new_fact.occurred_at == datetime(2026, 10, 5, tzinfo=timezone.utc)

    # The ambiguity is recorded inspectably: new fact → earlier fact.
    links = open_store(workspace).fact_links(new_fact.fact_key)
    corrections = [l for l in links if l["relation"] == "possibly_corrects"]
    assert len(corrections) == 1
    assert corrections[0]["target"] in before


def test_conflicting_assertions_within_one_snapshot_warn(
    tmp_path: Path,
) -> None:
    workspace = make_ws(tmp_path)
    two_times = (
        HEADER
        + "0101,completed,taro,42.5,2026-10-01,2026-10-04\n"
        + "0101,completed,taro,42.5,2026-10-01,2026-10-05\n"
    )

    result = ingest(workspace, tmp_path, "jobs.csv", two_times, FIXED_NOW)

    assert result.ok
    assert "CANON.POSSIBLE_CORRECTION" in codes(result)
    # Both asserted times are recorded as distinct facts.
    assert {
        f.occurred_at for f in facts(workspace, "Job", "0101") if f.event == "InvoiceIssued"
    } == {
        datetime(2026, 10, 4, tzinfo=timezone.utc),
        datetime(2026, 10, 5, tzinfo=timezone.utc),
    }


def test_correction_warning_aggregated_per_fact(tmp_path: Path) -> None:
    """One new fact conflicting with several earlier facts is one
    ambiguity — one warning — while every pairwise link is recorded."""
    workspace = make_ws(tmp_path)
    for day in ("2026-10-03", "2026-10-04"):
        ingest(
            workspace,
            tmp_path,
            f"jobs-{day}.csv",
            HEADER + f"0101,completed,taro,42.5,2026-10-01,{day}\n",
            FIXED_NOW,
        )

    third = ingest(
        workspace,
        tmp_path,
        "jobs-third.csv",
        HEADER + "0101,completed,taro,42.5,2026-10-01,2026-10-05\n",
        LATER,
    )

    assert third.ok
    warnings = [
        d for d in third.diagnostics if d.code == "CANON.POSSIBLE_CORRECTION"
    ]
    assert len(warnings) == 1
    assert "2026-10-03" in warnings[0].message
    assert "2026-10-04" in warnings[0].message
    assert "2026-10-05" in warnings[0].message
    new_fact = next(
        f
        for f in facts(workspace, "Job", "0101")
        if f.occurred_at == datetime(2026, 10, 5, tzinfo=timezone.utc)
    )
    links = open_store(workspace).fact_links(new_fact.fact_key)
    assert len([l for l in links if l["relation"] == "possibly_corrects"]) == 2


def test_later_fact_links_to_intervention_as_correlation(
    tmp_path: Path,
) -> None:
    workspace = make_ws(tmp_path)
    ingest(workspace, tmp_path, "jobs.csv", INITIAL_CSV, FIXED_NOW)
    proposal = make_proposal(workspace, tmp_path)

    decided = decide(
        workspace,
        proposal["proposal_id"],
        "approved",
        reviewer="fde-sato",
    )
    assert decided.ok
    executed = execute(
        workspace,
        proposal["proposal_id"],
        CsvReminderAdapter(tmp_path / "reminders.csv"),
    )
    assert executed.ok

    snapshot, observed_at = later_snapshot_after_proposal(proposal)
    later = ingest(
        workspace, tmp_path, "jobs-later.csv", snapshot, observed_at
    )

    assert later.ok
    invoice = next(
        f for f in facts(workspace, "Job", "0101") if f.event == "InvoiceIssued"
    )
    store = open_store(workspace)
    links = store.fact_links(invoice.fact_key)
    (link,) = [l for l in links if l["relation"] == "correlated_intervention"]
    # INV-003: the fact links to the intervention (the proposal id embeds
    # the diagnosis run) — a correlation, never a causal claim.
    assert link["target"] == proposal["proposal_id"]
    assert link["ingest_id"] == later.ingest_id
    # And the proposal side can find the fact again.
    assert store.links_for_target(proposal["proposal_id"]) == [link]

    # The reminder write-back is not evidence of the outcome: no link to
    # the adapter output exists and the source snapshot is what landed
    # the fact.


def test_later_fact_links_rejected_and_failed_interventions(
    tmp_path: Path,
) -> None:
    workspace = make_ws(tmp_path)
    ingest(workspace, tmp_path, "jobs.csv", INITIAL_CSV, FIXED_NOW)
    store = open_store(workspace)
    store.record_proposal_rows(
        [
            {
                "proposal_id": "run-r-p0",
                "diagnosis_run_id": "run-r",
                "proposal_index": 0,
                "action": "SendReminder",
                "params": {
                    "target": "taro",
                    "related_entity": "0101",
                    "reason": "x",
                },
                "reason": "x",
                "subject_entity": "Job",
                "subject_id": "0101",
                "related_entities": [
                    {"param": "target", "entity": "Person", "id": "taro"}
                ],
                "evidence": {},
                "payload_digest": "sha256:r",
                "created_at": FIXED_NOW,
            },
            {
                "proposal_id": "run-f-p0",
                "diagnosis_run_id": "run-f",
                "proposal_index": 0,
                "action": "SendReminder",
                "params": {
                    "target": "taro",
                    "related_entity": "0101",
                    "reason": "y",
                },
                "reason": "y",
                "subject_entity": "Job",
                "subject_id": "0101",
                "related_entities": [],
                "evidence": {},
                "payload_digest": "sha256:f",
                "created_at": FIXED_NOW,
            },
            # An intervention on an unrelated subject must not link.
            {
                "proposal_id": "run-o-p0",
                "diagnosis_run_id": "run-o",
                "proposal_index": 0,
                "action": "SendReminder",
                "params": {
                    "target": "hanako",
                    "related_entity": "9999",
                    "reason": "z",
                },
                "reason": "z",
                "subject_entity": "Job",
                "subject_id": "9999",
                "related_entities": [],
                "evidence": {},
                "payload_digest": "sha256:o",
                "created_at": FIXED_NOW,
            },
        ]
    )
    store.record_decision(
        {
            "decision_id": "dec-r",
            "proposal_id": "run-r-p0",
            "decision": "rejected",
            "reviewer": "fde-sato",
            "reason": None,
            "payload_digest": "sha256:r",
            "decided_at": FIXED_NOW,
        }
    )
    store.record_execution(
        {
            "execution_id": "exe-f",
            "idempotency_key": "idem-f",
            "proposal_id": "run-f-p0",
            "action": "SendReminder",
            "adapter": "csv-reminders",
            "attempt": 1,
            "status": "failed",
            "receipt": None,
            "error": "AdapterFailed: unsupported action",
            "created_at": FIXED_NOW,
        }
    )

    later = ingest(workspace, tmp_path, "jobs-later.csv", LATER_CSV, LATER)

    assert later.ok
    invoice = next(
        f for f in facts(workspace, "Job", "0101") if f.event == "InvoiceIssued"
    )
    links = open_store(workspace).fact_links(invoice.fact_key)
    targets = {
        l["target"]
        for l in links
        if l["relation"] == "correlated_intervention"
    }
    # Rejected and failed interventions are still interventions that
    # happened: the link is traceability, not a claim they worked.
    assert targets == {"run-r-p0", "run-f-p0"}


def test_unindexed_proposal_still_links(tmp_path: Path) -> None:
    """A diagnosis run recorded before the proposals index existed still
    links: ingest repairs the index before applying."""
    workspace = make_ws(tmp_path)
    ingest(workspace, tmp_path, "jobs.csv", INITIAL_CSV, FIXED_NOW)
    store = open_store(workspace)
    store.record_diagnosis_run(
        {
            "run_id": "run-legacy",
            "provider": "stub",
            "model": "m",
            "prompt_version": "v1",
            "prompt": "p",
            "status": "ok",
            "input_digest": "sha256:x",
            "provider_called": True,
            "output": None,
            "deliverables": {},
            "proposals": [
                {
                    "action": "SendReminder",
                    "params": {
                        "target": "taro",
                        "related_entity": "0101",
                        "reason": "x",
                    },
                    "reason": "x",
                    "index": 0,
                }
            ],
            "rejected": [],
            "diagnostics": [],
            "created_at": FIXED_NOW,
        }
    )
    assert store.proposals() == []

    later = ingest(workspace, tmp_path, "jobs-later.csv", LATER_CSV, LATER)

    assert later.ok
    invoice = next(
        f for f in facts(workspace, "Job", "0101") if f.event == "InvoiceIssued"
    )
    links = open_store(workspace).fact_links(invoice.fact_key)
    assert [
        l["target"]
        for l in links
        if l["relation"] == "correlated_intervention"
    ] == ["run-legacy-p0"]


def test_missing_record_deletes_nothing(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    ingest(workspace, tmp_path, "jobs.csv", INITIAL_CSV, FIXED_NOW)

    # A later snapshot that drops job 102 entirely changes nothing.
    result = ingest(
        workspace,
        tmp_path,
        "jobs-short.csv",
        HEADER + "0101,completed,taro,42.5,2026-10-01,\n",
        LATER,
    )

    assert result.ok
    store = open_store(workspace)
    assert store.entity_state("Job", "102") is not None
    assert [f.event for f in facts(workspace, "Job", "0101")] == ["JobCompleted"]
    assert "CANON.POSSIBLE_CORRECTION" not in codes(result)


def test_failed_apply_writes_no_fact_links(tmp_path: Path) -> None:
    """A failed canonical apply leaves no link rows — links commit or
    roll back with the facts they describe."""
    store = SqliteCanonicalStore(tmp_path / "runtime")
    store.apply(
        "ing-a",
        [
            support_observation(
                "obs-a",
                event_time=datetime(2026, 10, 4, tzinfo=timezone.utc),
            )
        ],
    )
    assert count_rows(store.db_path, "fact_links") == 0

    # obs-b would append a conflicting InvoiceIssued and record a
    # possibly_corrects link inside the transaction; obs-bad then fails
    # on an unserializable entity property, so the whole run — fact and
    # link alike — must roll back.
    linking = support_observation(
        "obs-b",
        event_time=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )
    bad = support_observation(
        "obs-bad",
        event_time=datetime(2026, 10, 6, tzinfo=timezone.utc),
        unserializable=True,
    )
    with pytest.raises(TypeError):
        store.apply("ing-b", [linking, bad])

    assert count_rows(store.db_path, "fact_links") == 0
    assert count_rows(store.db_path, "events") == 1

    # Applying the same linking observation alone writes the link, so
    # its absence above was the rollback — not a link never produced.
    store.apply("ing-c", [linking])
    assert count_rows(store.db_path, "fact_links") == 1


def test_link_matches_via_related_entities_ref(tmp_path: Path) -> None:
    """A proposal whose canonical subject is another entity — or NULL —
    still links when a related_entities ref names the event's subject."""
    workspace = make_ws(tmp_path)
    ingest(workspace, tmp_path, "jobs.csv", INITIAL_CSV, FIXED_NOW)
    open_store(workspace).record_proposal_rows(
        [
            {
                "proposal_id": "run-rel-p0",
                "diagnosis_run_id": "run-rel",
                "proposal_index": 0,
                "action": "SendReminder",
                "params": {"target": "taro", "related_entity": "0101"},
                "reason": "x",
                "subject_entity": "Person",
                "subject_id": "taro",
                "related_entities": [
                    {"param": "related_entity", "entity": "Job", "id": "0101"}
                ],
                "evidence": {},
                "payload_digest": "sha256:rel",
                "created_at": FIXED_NOW,
            },
            {
                "proposal_id": "run-null-p0",
                "diagnosis_run_id": "run-null",
                "proposal_index": 0,
                "action": "SendReminder",
                "params": {"target": "taro", "related_entity": "0101"},
                "reason": "y",
                "subject_entity": None,
                "subject_id": None,
                "related_entities": [
                    {"param": "related_entity", "entity": "Job", "id": "0101"}
                ],
                "evidence": {},
                "payload_digest": "sha256:null",
                "created_at": FIXED_NOW,
            },
        ]
    )

    later = ingest(workspace, tmp_path, "jobs-later.csv", LATER_CSV, LATER)

    assert later.ok
    invoice = next(
        f for f in facts(workspace, "Job", "0101") if f.event == "InvoiceIssued"
    )
    links = open_store(workspace).fact_links(invoice.fact_key)
    targets = {
        l["target"]
        for l in links
        if l["relation"] == "correlated_intervention"
    }
    assert targets == {"run-rel-p0", "run-null-p0"}


def test_fact_predating_intervention_does_not_link(tmp_path: Path) -> None:
    """A fact dated before the proposal was recorded is provably not a
    later outcome — ingestion order is not business-event order."""
    workspace = make_ws(tmp_path)
    ingest(workspace, tmp_path, "jobs.csv", INITIAL_CSV, FIXED_NOW)
    open_store(workspace).record_proposal_rows(
        [
            {
                "proposal_id": "run-p-p0",
                "diagnosis_run_id": "run-p",
                "proposal_index": 0,
                "action": "SendReminder",
                "params": {"target": "taro", "related_entity": "0101"},
                "reason": "x",
                "subject_entity": "Job",
                "subject_id": "0101",
                "related_entities": [],
                "evidence": {},
                "payload_digest": "sha256:p",
                "created_at": datetime(2026, 10, 5, tzinfo=timezone.utc),
            }
        ]
    )

    # The invoice was issued on 10-03 — before the proposal existed —
    # even though the snapshot reporting it only arrives on 10-06.
    earlier_invoice = HEADER + "0101,completed,taro,42.5,2026-10-01,2026-10-03\n"
    first = ingest(
        workspace, tmp_path, "jobs-a.csv", earlier_invoice, LATER
    )
    assert first.ok
    invoice = next(
        f for f in facts(workspace, "Job", "0101") if f.event == "InvoiceIssued"
    )
    links = open_store(workspace).fact_links(invoice.fact_key)
    assert [
        l for l in links if l["relation"] == "correlated_intervention"
    ] == []

    # A fact dated after the proposal still links.
    newest_invoice = HEADER + "0101,completed,taro,42.5,2026-10-01,2026-10-07\n"
    second = ingest(
        workspace, tmp_path, "jobs-b.csv", newest_invoice, EVEN_LATER
    )
    assert second.ok
    newest = next(
        f
        for f in facts(workspace, "Job", "0101")
        if f.occurred_at == datetime(2026, 10, 7, tzinfo=timezone.utc)
    )
    links = open_store(workspace).fact_links(newest.fact_key)
    assert [
        l["target"]
        for l in links
        if l["relation"] == "correlated_intervention"
    ] == ["run-p-p0"]


def test_apply_survives_unreadable_proposal_row(tmp_path: Path) -> None:
    """A corrupt proposals row must not fail canonical apply — linking
    is auxiliary, so it is skipped rather than breaking the run."""
    store = SqliteCanonicalStore(tmp_path / "runtime")
    store.apply(
        "ing-a",
        [
            support_observation(
                "obs-a",
                event_time=datetime(2026, 10, 4, tzinfo=timezone.utc),
            )
        ],
    )
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            INSERT INTO proposals (
                proposal_id, diagnosis_run_id, proposal_index,
                action, params, reason,
                subject_entity, subject_id, related_entities,
                evidence, payload_digest, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "corrupt-p0",
                "run-corrupt",
                0,
                "SendReminder",
                "not-json{",
                None,
                "Job",
                "0101",
                "not-json{",
                "{}",
                "sha256:bad",
                "2026-10-05T00:00:00Z",
            ),
        )

    # A conflicting second fact both appends and would have linked to
    # the proposal; the corrupt row disables intervention linking only.
    store.apply(
        "ing-b",
        [
            support_observation(
                "obs-b",
                event_time=datetime(2026, 10, 5, tzinfo=timezone.utc),
            )
        ],
    )

    assert count_rows(store.db_path, "events") == 2
    relations = {
        row[0]
        for row in sqlite3.connect(store.db_path)
        .execute("SELECT relation FROM fact_links")
        .fetchall()
    }
    assert relations == {"possibly_corrects"}


def count_rows(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def support_observation(
    observation_id: str,
    *,
    event_time: datetime,
    unserializable: bool = False,
):
    properties: dict = {"status": "completed"}
    if unserializable:
        properties["x"] = object()
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
        entities=[
            ResolvedEntity(entity="Job", id="0101", properties=properties)
        ],
        events=[
            ResolvedEvent(
                event="InvoiceIssued",
                subject_entity="Job",
                subject_id="0101",
                occurred_at=event_time,
                observed_at=FIXED_NOW,
            )
        ],
        diagnostics=[],
    )


def test_fact_links_are_traced_and_queryable(tmp_path: Path) -> None:
    workspace = make_ws(tmp_path)
    ingest(workspace, tmp_path, "jobs.csv", INITIAL_CSV, FIXED_NOW)
    proposal = make_proposal(workspace, tmp_path)

    class ListTrace:
        def __init__(self) -> None:
            self.events = []

        def emit(self, stage, kind, data):
            self.events.append((stage, kind, data))

    trace = ListTrace()
    snapshot, observed_at = later_snapshot_after_proposal(proposal)
    later = ingest_snapshot(
        workspace,
        write_snapshot(tmp_path, "jobs-later.csv", snapshot),
        observed_at=observed_at,
        trace=trace,
    )

    assert later.ok
    linked = [
        data
        for _, kind, data in trace.events
        if kind == "canonical.fact_linked"
    ]
    invoice = next(
        f for f in facts(workspace, "Job", "0101") if f.event == "InvoiceIssued"
    )
    assert [
        (l["fact_key"], l["relation"], l["target"]) for l in linked
    ] == [
        (
            invoice.fact_key,
            "correlated_intervention",
            proposal["proposal_id"],
        )
    ]
    applied = next(
        data for _, kind, data in trace.events if kind == "canonical.applied"
    )
    assert applied["fact_links"] == 1
