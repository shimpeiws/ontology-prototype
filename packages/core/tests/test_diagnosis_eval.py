"""Diagnosis evaluation harness tests (issue #31).

The harness replays committed frozen fixtures through controlled provider
doubles. These tests prove the suite itself works: fixtures are immutable
and deterministic, scenario expectations hold, the run budget bounds
provider calls, and substituting provider/model leaves the whole
downstream workflow (validation, approval, write-back) unchanged.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ontology_core import (
    SCENARIOS,
    CsvReminderAdapter,
    RunBudget,
    check_approval,
    decide,
    evaluate_subject,
    execute,
    get_diagnosis_run,
    prepare,
    run_diagnosis,
    run_scenario,
    run_suite,
)
from ontology_core import diagnosis_eval
from ontology_core.diagnosis_eval import (
    DATASET_CLEAN,
    DATASET_UNTRUSTED,
    EVAL_VERSION,
    INJECTION_TEXT,
    PROMPT_VERSION as EVAL_PROMPT_VERSION,
    SECRET_MARKER,
    FIXTURE_DIR,
    ScriptedProvider,
)


def _scenario(scenario_id: str):
    return next(s for s in SCENARIOS if s.id == scenario_id)


def _fixture_digests() -> dict[str, str]:
    return {
        str(p.relative_to(FIXTURE_DIR)): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in sorted(FIXTURE_DIR.rglob("*"))
        if p.is_file()
    }


def _golden_text(candidates) -> str:
    """Compose a contract-valid output for the first frozen candidate —
    the same shape the harness's golden double emits."""
    first = candidates[0]
    return json.dumps(
        {
            "explanation": f"Job {first['subject']['id']} is uninvoiced.",
            "hypotheses": [
                {
                    "statement": "Manual invoicing step may be pending.",
                    "evidence": [first["evidence"]["fact_key"]],
                }
            ],
            "follow_up_questions": ["Is invoicing manual?"],
            "action_proposals": [
                {
                    "action": "SendReminder",
                    "params": {
                        "target": first["properties"]["assignee"],
                        "related_entity": first["subject"]["id"],
                        "reason": "Exceeded the two-day threshold.",
                    },
                    "reason": "Oldest uninvoiced candidate.",
                }
            ],
        }
    )


def test_suite_passes_on_committed_fixtures(tmp_path: Path) -> None:
    report = run_suite(tmp_path / "suite")

    assert report.eval_version == EVAL_VERSION
    assert report.prompt_version == EVAL_PROMPT_VERSION
    assert report.within_budget
    assert report.provider_requests == len(SCENARIOS)
    assert report.passed, [
        f"{s.scenario}:{c.name}:{c.detail}"
        for s in report.scenarios
        for c in s.checks
        if not c.passed
    ]
    assert {s.scenario for s in report.scenarios} == {
        s.id for s in SCENARIOS
    }
    # The clean fixture freezes exactly the two documented candidates.
    assert report.datasets[DATASET_CLEAN]["candidate_ids"] == ["E01", "E02"]
    assert report.datasets[DATASET_UNTRUSTED]["candidate_ids"] == ["X01"]


def test_fixtures_are_not_mutated(tmp_path: Path) -> None:
    """INV-001: preparing and running never writes into eval_fixtures."""
    before = _fixture_digests()
    report = run_suite(tmp_path / "suite")
    assert report.passed
    assert _fixture_digests() == before


def test_evaluation_is_repeatable(tmp_path: Path) -> None:
    """INV-008: two independent prepares freeze the same candidates.

    Observation/ingest ids embed a per-ingest uuid by design, so the
    stable parts of the frozen candidate set — subject ids, event times,
    properties, and content-derived fact keys — are what must match.
    """
    first = prepare(tmp_path / "a")
    second = prepare(tmp_path / "b")

    def stable(candidate: dict) -> dict:
        return {
            "subject": candidate["subject"],
            "occurred_at": candidate["occurred_at"],
            "elapsed_seconds": candidate["elapsed_seconds"],
            "properties": candidate["properties"],
            "fact_key": candidate["evidence"]["fact_key"],
        }

    assert first.run_id != second.run_id
    assert [stable(c) for c in first.candidates] == [
        stable(c) for c in second.candidates
    ]
    assert [c["subject"]["id"] for c in first.candidates] == ["E01", "E02"]

    provider = ScriptedProvider("repeat-a", _golden_text(first.candidates))
    ra = run_diagnosis(
        first.workspace, provider, model="m", run_id=first.run_id,
        store=first.store,
    )
    provider_b = ScriptedProvider(
        "repeat-b", _golden_text(second.candidates)
    )
    rb = run_diagnosis(
        second.workspace, provider_b, model="m", run_id=second.run_id,
        store=second.store,
    )
    assert ra.ok and rb.ok
    assert [p.to_dict() for p in ra.proposals] == [
        p.to_dict() for p in rb.proposals
    ]


def test_malformed_and_contract_violating_scenarios(tmp_path: Path) -> None:
    """INV-002: bad output never yields a persisted proposal."""
    prep = prepare(tmp_path / "ws")
    for scenario_id in ("malformed-output", "contract-violation"):
        outcome = run_scenario(prep, _scenario(scenario_id))
        assert outcome.passed, [
            f"{c.name}:{c.detail}" for c in outcome.checks
        ]
        record = get_diagnosis_run(
            prep.workspace, outcome.run_id, store=prep.store
        )
        assert record["status"] == "failed"
        assert record["proposals"] == []


def test_provider_failure_scenarios(tmp_path: Path) -> None:
    """INV-003: provider failure is a recorded diagnostic, and an
    unexpected adapter error's message never leaks."""
    prep = prepare(tmp_path / "ws")
    for scenario_id in ("provider-error", "provider-crash"):
        outcome = run_scenario(prep, _scenario(scenario_id))
        assert outcome.passed, [
            f"{c.name}:{c.detail}" for c in outcome.checks
        ]

    crash = run_scenario(prep, _scenario("provider-crash"))
    record = get_diagnosis_run(
        prep.workspace, crash.run_id, store=prep.store
    )
    assert SECRET_MARKER not in json.dumps(record)


def test_untrusted_source_scenario(tmp_path: Path) -> None:
    """INV-006: injection text stays JSON-escaped data; obeying it is
    rejected by validation."""
    prep = prepare(tmp_path / "ws", dataset=DATASET_UNTRUSTED)
    outcome = run_scenario(prep, _scenario("untrusted-source"))

    assert outcome.passed, [
        f"{c.name}:{c.detail}" for c in outcome.checks
    ]
    record = get_diagnosis_run(
        prep.workspace, outcome.run_id, store=prep.store
    )
    # Embedded as escaped JSON string data inside the candidate block.
    assert json.dumps(INJECTION_TEXT) in record["prompt"]
    assert record["rejected"][0]["reason"].find(
        "not among the diagnosed candidates"
    ) != -1


def test_budget_bounds_provider_requests(tmp_path: Path) -> None:
    """INV-007: the suite stops calling providers once the cap is spent."""
    budget = RunBudget(repeats=1, max_provider_requests=3)
    report = run_suite(tmp_path / "suite", budget=budget)

    assert report.provider_requests <= 3
    assert not report.passed
    exhausted = [
        s for s in report.scenarios if s.run_id is None
    ]
    assert exhausted
    assert all(
        any(c.name == "budget" and not c.passed for c in s.checks)
        for s in exhausted
    )


def test_provider_substitution_keeps_workflow(tmp_path: Path) -> None:
    """INV-004/INV-005: swapping provider adapter and model id changes
    only recorded identity — candidates, input digest, validation,
    approval gate, and write-back are identical."""
    prep = prepare(tmp_path / "ws")
    workspace = prep.workspace
    assert workspace.evaluation is not None

    def diagnose(name: str, model: str):
        provider = ScriptedProvider(name, _golden_text(prep.candidates))
        result = run_diagnosis(
            workspace, provider, model=model, run_id=prep.run_id,
            store=prep.store,
        )
        assert result.ok, [d.format() for d in result.diagnostics]
        return result

    run_a = diagnose("provider-a", "eval/model-a")
    run_b = diagnose("provider-b", "eval/model-b")
    rec_a = get_diagnosis_run(workspace, run_a.run_id, store=prep.store)
    rec_b = get_diagnosis_run(workspace, run_b.run_id, store=prep.store)

    # Same frozen candidate selection and identical input digest.
    assert rec_a["projection_run_id"] == rec_b["projection_run_id"]
    assert rec_a["input_digest"] == rec_b["input_digest"]
    # Validation verdicts are identical; only identity differs.
    assert [p.to_dict() for p in run_a.proposals] == [
        p.to_dict() for p in run_b.proposals
    ]
    assert (rec_a["provider"], rec_a["model"]) == (
        "provider-a",
        "eval/model-a",
    )
    assert (rec_b["provider"], rec_b["model"]) == (
        "provider-b",
        "eval/model-b",
    )

    adapter = CsvReminderAdapter(prep.workspace_dir / "reminders.csv")
    for run in (run_a, run_b):
        proposal_id = f"{run.run_id}-p0"
        # Human approval is still mandatory: execution before a decision
        # is refused, regardless of which provider produced the proposal.
        refused = execute(
            workspace, proposal_id, adapter, store=prep.store
        )
        assert not refused.ok
        assert refused.status == "refused"

        decided = decide(
            workspace,
            proposal_id,
            "approved",
            reviewer="fde-eval",
            store=prep.store,
        )
        assert decided.ok, [d.format() for d in decided.diagnostics]
        proposal = run.proposals[0]
        approval = check_approval(
            workspace,
            proposal_id,
            action=proposal.action,
            params=proposal.params,
            store=prep.store,
        )
        assert approval.ok
        executed = execute(
            workspace, proposal_id, adapter, store=prep.store
        )
        assert executed.ok, [d.format() for d in executed.diagnostics]
        assert executed.status == "succeeded"
        # Repeating write-back replays the receipt, never re-delivers.
        replayed = execute(
            workspace, proposal_id, adapter, store=prep.store
        )
        assert replayed.replayed

    # The outcome-evaluation contract is untouched by any of it.
    assert workspace.evaluation.name == "completion-to-invoice-latency"


def test_evaluate_subject_scores_live_style_run(tmp_path: Path) -> None:
    """INV-009: a live-comparison run reuses the same scoring without
    modifying the harness."""
    prep = prepare(tmp_path / "probe")
    text = _golden_text(prep.candidates)

    report = evaluate_subject(
        tmp_path / "subject",
        ScriptedProvider("subject-double", text),
        model="eval/subject",
        repeats=2,
        budget=RunBudget(repeats=2, max_provider_requests=4),
    )

    assert report.provider == "subject-double"
    assert report.model == "eval/subject"
    assert report.repeats_completed == 2
    assert report.provider_requests == 2
    assert report.within_budget
    assert report.passed, [
        f"{c.name}:{c.detail}"
        for r in report.runs
        for c in r.checks
        if not c.passed
    ]


def test_evaluate_subject_respects_budget(tmp_path: Path) -> None:
    report = evaluate_subject(
        tmp_path / "subject",
        ScriptedProvider("subject-double", "x"),
        model="eval/subject",
        repeats=5,
        budget=RunBudget(repeats=2, max_provider_requests=4),
    )
    assert report.repeats_requested == 5
    assert report.repeats_completed == 2
    assert report.provider_requests == 2


def test_main_runs_the_suite(tmp_path: Path, capsys) -> None:
    code = diagnosis_eval.main(["--json", str(tmp_path / "suite")])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert payload["eval_version"] == EVAL_VERSION
