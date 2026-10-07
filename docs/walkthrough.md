# Walkthrough: the complete v0.1 operational loop

This walkthrough runs the job-invoicing workflow from business understanding
through a human-approved intervention and outcome evaluation, on committed
synthetic data. Every command is deterministic and repeatable from a clean
checkout. The automated equivalent is
`apps/cli/tests/test_loop_cli.py`; the separate live-model run is recorded in
[v0.1-live-model-run.md](v0.1-live-model-run.md).

## Business record

- **Interview**: the field engineering team's delivery records show completed
  jobs sitting un-invoiced for days. The operations contact reports that
  invoicing is a manual step owned by each job's assignee, and that nothing
  notifies the assignee when a completed job becomes billable.
- **Workflow constraint**: a completed job must not wait more than 2 days for
  invoicing. Jobs completed longer ago are the diagnosis candidates; a job
  exactly 2 days old is not yet a candidate (strict `>`).
- **Hypothesis**: a timely reminder to the assignee closes the gap between
  completion and invoice issuance. The hypothesis is tested against a declared
  contract — baseline median 9 days, target median strictly below 4 days,
  invoice-error rate must not increase — not against the model's prose.

## Data

The demonstration snapshots live in `examples/job-invoicing/data/`:

| File | Rows | Role |
| --- | --- | --- |
| `jobs-baseline.csv` | B01–B03 | Historical export: jobs completed before the intervention, invoiced 9, 8, and 10 days later (median 9 days). |
| `jobs-current.csv` | C01–C04 | Current snapshot: C01 and C02 completed but not invoiced (candidates), C03 completed exactly 2 days before as-of (threshold edge, not a candidate), C04 in progress. |
| `jobs-followup.csv` | C01–C05 | Later snapshot: C01–C03 invoiced 3, 3, and 4 days after completion; C05 completed after the boundary and still uninvoiced (pending outcome). |

The older `jobs.csv` / `jobs-later.csv` pair belongs to the ingestion
documentation examples and is not part of this run.

All timestamps are date-only and resolve to midnight UTC under
`date_only_timezone`. The cohort boundary `2026-10-15T00:00:00Z` marks the
intervention: baseline jobs completed before it, the after cohort after it.
Diagnosis runs at `2026-10-21T00:00:00Z`; evaluation at
`2026-10-26T00:00:00Z`.

## Steps

Work on a clean workspace: `.ontology/` holds runtime state, so either use a
fresh checkout or remove any prior `.ontology/` first. Nothing under
`.ontology/` is committed.

```bash
# 1. Validate the four definition files.
ontology check examples/job-invoicing
# → workspace valid: examples/job-invoicing (0 errors, 0 warnings)

# 2. Ingest the historical export and the current snapshot.
ontology ingest examples/job-invoicing examples/job-invoicing/data/jobs-baseline.csv
# → 3 accepted, 0 rejected; 6 events appended (3 JobCompleted + 3 InvoiceIssued)
ontology ingest examples/job-invoicing examples/job-invoicing/data/jobs-current.csv
# → 4 accepted, 0 rejected; 3 events appended (C01–C03 JobCompleted)

# 3. Freeze the projection at the diagnosis time.
ontology project examples/job-invoicing --as-of 2026-10-21T00:00:00Z
# → 2 candidates: Job/C01 (4.00 days), Job/C02 (3.00 days)
#   C03 is exactly 2 days old and does not meet > 2; C04 has no completion.

# 4. Diagnose with the deterministic offline double.
ontology diagnose examples/job-invoicing --as-of 2026-10-21T00:00:00Z --provider stub
# → explanation, labeled hypotheses, and a SendReminder proposal for Job/C01
#   persisted for human review.

# 5. Execution is refused until a human decides.
ontology proposals examples/job-invoicing            # note <proposal-id>
ontology execute examples/job-invoicing <proposal-id>
# → refused: proposal is proposed, not approved (DECISION.NOT_APPROVED)

# 6. Record an explicit human approval, then execute.
ontology decide examples/job-invoicing <proposal-id> --approve --reviewer fde-sato
ontology execute examples/job-invoicing <proposal-id>
# → succeeded: reminder rem-… appended to .ontology/reminders.csv
# Repeating either step is safe: a second execute reports "(already executed)"
# and appends nothing; re-ingesting the current snapshot appends 0 events.

# 7. Re-observe the source after the outcome arrives.
ontology ingest examples/job-invoicing examples/job-invoicing/data/jobs-followup.csv
# → 5 accepted; 4 events appended; the new InvoiceIssued facts link to the
#   intervention as correlated_intervention — correlation, never causation.

# 8. Evaluate baseline vs after cohorts at the intervention boundary.
ontology evaluate examples/job-invoicing --boundary 2026-10-15T00:00:00Z --as-of 2026-10-26T00:00:00Z
# → baseline: median 9 days (3 paired)
#   after:    median 3 days (3 paired, 1 pending — C05)
#   change:   6 days (improvement)
#   target lt 4 days: attained
#   guardrail invoice-error-rate: unavailable — no invoice-error evidence
#   overall:  unverified

# 9. Inspect the linked trace end to end.
ontology trace examples/job-invoicing
ontology trace examples/job-invoicing --subject Job/C01
ontology trace examples/job-invoicing --export trace-export.json
# → observation evidence → canonical events → projection run → diagnosis
#   output → proposal → decision → execution receipt → later events →
#   evaluation report, with 0 unresolved links.
```

## Reading the result honestly

The report shows an observed difference between two time-based cohorts of
synthetic data. `target attained` means the after median cleared the strict
threshold; `overall: unverified` — not `met` — because the invoice-error
guardrail has no evidence to evaluate. The machinery is verified; the
demonstration does not claim the synthetic data proves a real business
improvement. A 4-day after median would report `missed` / `not_met`, which
`test_loop_median_exactly_at_target_is_a_miss` pins.

## Required negative cases and where they are covered

| Case | Coverage |
| --- | --- |
| Malformed mapping / input | `packages/core/tests/test_loader.py`, `test_ingest.py`, `test_mapping.py`, `test_domain.py`; `eval_fixtures/outputs/malformed.txt` |
| Missing timestamps | `test_ingest.py` — `occurred_at` is required when an event condition holds |
| Duplicate observations | `test_reobservation.py::test_repeated_later_snapshot_preserves_events_adds_provenance`; e2e re-ingest appends 0 events |
| Source correction | `test_reobservation.py::test_changed_timestamp_keeps_both_facts_and_warns`, `test_correction_warning_aggregated_per_fact` (`CANON.POSSIBLE_CORRECTION`) |
| Ambiguous / negative intervals | `test_outcome.py::test_pending_negative_and_ambiguous_intervals` |
| Exactly-on-threshold candidates | `test_projection.py::test_projection_threshold_boundaries`; C03 in this walkthrough |
| Invalid / disallowed AI proposals | `test_diagnosis.py::test_invalid_output_never_becomes_proposal`, `test_disallowed_and_invalid_proposals_rejected`, `test_injection_driven_output_is_rejected` |
| Model failure | `test_diagnosis.py::test_provider_failure_is_diagnostic_and_recorded` |
| Unapproved / rejected / changed proposal execution | `test_execution.py::test_execute_refused_for_unapproved_rejected_unknown`, `test_decisions.py::test_changed_payload_fails_approval`; e2e refusal before decide and after reject |
| Repeated write-back | `test_execution.py::test_repeated_execute_does_not_duplicate`, `test_csv_adapter_dedupes_idempotency_key`; e2e re-execute |
| Failed / uncertain execution | `test_execution.py::test_failed_attempt_can_retry`, `test_uncertain_attempt_blocks_blind_retry`, `test_unrecorded_outcome_blocks_blind_retry` |
| Pending outcomes | `test_outcome.py::test_pending_negative_and_ambiguous_intervals`; C05 in this walkthrough |
| Unavailable / failed guardrails | `test_outcome.py::test_guardrail_unavailable_passed_and_failed`, `test_guardrail_unsupported_metric`; the live report's `unavailable` |

## Live model

`--provider stub` is the deterministic double used above and in CI. The same
commands against a real model — `--provider openrouter --model
anthropic/claude-haiku-4.5` (the evidence-backed selection, see
[ADR 0002](adr/0002-default-diagnosis-model.md)) — were run once with a real
credential; the sanitized run is recorded in
[v0.1-live-model-run.md](v0.1-live-model-run.md). No credentials are required
for, or committed by, this walkthrough.
