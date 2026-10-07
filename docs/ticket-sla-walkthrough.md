# Walkthrough: the ticket-SLA workflow (second workflow, v0.3)

This walkthrough records the second workflow — support-ticket
first-response SLA — alongside the job-invoicing record in
[walkthrough.md](walkthrough.md). Its purpose is generality: it shows
which parts of the loop are already workflow-agnostic and where YAML
alone is insufficient. Every gap it exposes is listed in
[generality-gaps.md](generality-gaps.md).

The automated equivalent is
`apps/cli/tests/test_ticket_sla_cli.py`.

## Business record

- **Interview**: the support team's ticket export shows first responses
  arriving a day or more after a ticket is opened. The support lead
  reports that replying to a new ticket is a manual step owned by each
  ticket's assignee, and that nothing notifies the assignee when an
  unanswered ticket approaches its SLA.
- **Workflow constraint**: an opened ticket must not wait more than 4
  hours for a first response. Tickets opened longer ago without a
  `FirstResponse` are the diagnosis candidates; a ticket exactly 4 hours
  old is not yet a candidate (strict `>`).
- **Hypothesis**: a timely reminder to the assignee closes the gap
  between ticket open and first response. The hypothesis is tested
  against a declared contract — baseline median 26 hours, target median
  strictly below 4 hours, reopen rate must not increase — not against
  the model's prose.

## Data

The demonstration snapshots live in `examples/ticket-sla/data/`:

| File | Rows | Role |
| --- | --- | --- |
| `tickets-baseline.csv` | T-B01–T-B03 | Historical export: tickets opened before the intervention, first responses 26, 28, and 24 hours later (median 26 hours). |
| `tickets-current.csv` | T-C00–T-C04 | Current snapshot at the diagnosis instant: T-C00 opened but still unanswered past the 4-hour SLA (the candidate), T-C01 and T-C02 already first-responded 3 and 2.5 hours after open, T-C03 opened 3 hours before as-of (still inside the SLA), T-C04 answered and resolved. |
| `tickets-followup.csv` | T-C00–T-C05 | Later snapshot: T-C00 first-responded 20 hours after open (after the diagnosis instant) and resolved; T-C01–T-C03 resolved with first responses 3, 2.5, and 3.5 hours after open; T-C05 opened after the last observation and still unanswered (pending outcome). |
| `resolutions-baseline.csv` | R-B01–R-B03 | Reopen evidence for the baseline cohort: R-B03 reopened. Ingestible via the `resolutions` source (#57): each row binds a `Resolution` entity whose `ticket` ref resolves cross-source by exact stable id. |
| `resolutions-followup.csv` | R-C00–R-C04 | Reopen evidence for the after cohort: none reopened. Ingestible via the `resolutions` source for the same reason. |

All timestamps are ISO 8601 datetimes with an explicit `Z` offset —
first-response SLAs are measured in hours, so this workflow cannot use
the date-only columns the job-invoicing snapshots use. The scenario
times are caller-supplied, not the real time of your run: the cohort
boundary `2026-10-20T00:00:00Z` marks the intervention (baseline tickets
opened before it, the after cohort after it); diagnosis would run at
`2026-10-24T00:00:00Z`; evaluation runs at `2026-10-30T00:00:00Z`.

`first_response_at` arrives pre-computed on the ticket export. Deriving
`FirstResponse` from a raw replies export would be the more realistic
model and would need earliest-per-subject event selection on top of the
second source — recorded as G-06 in the gap log.

## What runs and what does not

```bash
# 1. The four definitions validate cleanly.
ontology check examples/ticket-sla
# → workspace valid: examples/ticket-sla (0 errors, 0 warnings)

# 2. Both snapshots ingest without rejection; re-observation is idempotent.
#    The mapping declares two sources (tickets, resolutions), so --source
#    selects which bindings apply.
ontology ingest examples/ticket-sla examples/ticket-sla/data/tickets-baseline.csv --source tickets
# → 3 accepted, 0 rejected; 9 events appended (3× TicketOpened, FirstResponse, TicketResolved)
ontology ingest examples/ticket-sla examples/ticket-sla/data/tickets-current.csv --source tickets
# → 5 accepted, 0 rejected; 9 events appended

# 2b. The resolution system is a second source: each row creates a
#     Resolution entity whose `ticket` ref resolves cross-source against
#     the canonical Tickets by exact stable id. Its TicketResolved facts
#     match the tickets export's resolved_at values, so they are
#     re-observed, never duplicated.
ontology ingest examples/ticket-sla examples/ticket-sla/data/resolutions-baseline.csv --source resolutions
# → 3 accepted, 0 rejected; 3 Resolution entities upserted;
#   3 TicketResolved events re-observed (0 appended)

# 3. The projection runs from its declared `select` — no code was
#    registered for this workflow. T-C00 was opened 16 hours before
#    as-of with no FirstResponse (> the 4-hour SLA); T-C03, opened 3
#    hours before as-of, stays inside the SLA and is excluded (#55).
ontology project examples/ticket-sla --as-of 2026-10-24T00:00:00Z
# → 1 candidate: T-C00 (elapsed 16h)

# 4. Diagnosis proceeds past the projection stage with a provider.
ontology diagnose examples/ticket-sla --as-of 2026-10-24T00:00:00Z --provider stub
# → diagnosis run recorded over 1 candidate (stub proposal)

# 5. Evaluation of the declared interval does run — interval pairing,
#    cohorts, and the strict target are all generic. Reopen evidence now
#    ingests from the resolutions export, but the reopen-rate guardrail
#    still reports unavailable because reopen_rate has no registered
#    metric semantics (gap G-02 → #56).
ontology ingest examples/ticket-sla examples/ticket-sla/data/tickets-followup.csv --source tickets
# → 6 accepted, 0 rejected; 7 events appended; 9 re-observed
ontology ingest examples/ticket-sla examples/ticket-sla/data/resolutions-followup.csv --source resolutions
# → 5 accepted, 0 rejected; 5 TicketResolved events re-observed (0 appended)
ontology evaluate examples/ticket-sla --boundary 2026-10-20T00:00:00Z --as-of 2026-10-30T00:00:00Z
# → baseline: median 26 hours (3 paired)
#   after:    median 3 hours (5 paired, 1 pending — T-C05)
#   change:   23 hours (improvement)
#   target lt 4 hours: attained
#   guardrail reopen-rate: unavailable — no registered semantics
#   overall:  unverified
```

The parts that run — validation, ingestion with observation evidence and
re-observation idempotency, declared candidate selection, diagnosis,
interval pairing, cohort evaluation, strict targets — are
workflow-agnostic. What still stops is the reopen-rate guardrail and its
evidence source — gaps G-02/G-03.

## Reading the result honestly

The report shows an observed difference between two time-based cohorts
of synthetic data. `target attained` means the after median cleared the
strict threshold; `overall: unverified` — not `met` — because the
reopen-rate guardrail has no registered semantics even though its
evidence now ingests from the `resolutions` source. The demonstration
does not claim the synthetic data proves a real business improvement.
