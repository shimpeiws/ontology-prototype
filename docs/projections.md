# Projections: deterministic candidates over canonical events

```bash
ontology project <workspace> --as-of 2026-10-05T00:00:00Z
ontology project <workspace> CompletedButNotInvoiced --as-of 2026-10-05T00:00:00Z --json
ontology project <workspace> --run prj-20261005T000000000000Z-abcd1234
```

`ontology project` computes a declared domain projection over the canonical event store and freezes the run. `--as-of` is required and must carry an explicit offset — a naive time is ambiguous and is rejected, never guessed. The projection name defaults to the workspace agent's `agent.projection`.

## CompletedButNotInvoiced

v0.1 registers one semantic under this projection name: a subject is a candidate when the evaluation contract's `interval.start_event` occurred but no `interval.end_event` exists for that subject, and `as_of − occurred_at` is strictly greater than two days. Exactly two days is not a candidate, and a future-dated start event is excluded with a `PROJECTION.FUTURE_EVENT` warning.

When several start events exist for one subject, the earliest `occurred_at` sets the clock — the first completion is when the delay began. Any end event excludes the subject regardless of ordering.

Each candidate carries the canonical subject, the subject entity's current properties (for job-invoicing that includes `assignee`), the completion time, the elapsed duration, and evidence references: the start fact key plus every observation id and ingest id that asserted it.

## Frozen runs

Every run persists into `projection_runs` (append-only, like `events`):

- the projection definition digest — schema version, projection declaration, semantic id and version, the full evaluation contract, the interval events' declarations, the date-only timezone policy, and the fixed threshold;
- the input references examined — per subject, the start/end fact keys and the exclusion reason;
- the full candidate set as computed.

A historical run is therefore inspectable with `project --run <id>` and unchanged by later ingestion or configuration edits; a definition or contract edit changes the digest even when the candidate list stays the same.

Only successful runs are frozen: a run that fails (`SUBJECT_MISMATCH`, a store error, …) is not recorded, so a stored run always means "evaluated to this candidate set" and never "evaluation failed". The attempt is still traced as `projection.failed` (successful runs emit `projection.completed`), so no record in `projection_runs` plus a failed trace event is inspectable evidence of a failed run. `project --run` is read-only — it does not create `.ontology/` or `canon.db` on a workspace that has not ingested — and cannot be combined with `--as-of` or a projection name.

## Diagnostics

- `PROJECTION.UNKNOWN_PROJECTION` — the name is not declared in `domain.projections`.
- `PROJECTION.UNSUPPORTED_PROJECTION` — declared, but no registered semantics exist in schema v1.
- `PROJECTION.NAIVE_AS_OF` — `as_of` had no timezone offset.
- `PROJECTION.SUBJECT_MISMATCH` — the evaluation interval's event subjects do not match the projection subject.
- `PROJECTION.FUTURE_EVENT` — warning: a start event occurred after `as_of`.
- `PROJECTION.STORE_ERROR` — canonical state could not be read, or the frozen run could not be persisted.
- `PROJECTION.TRACE_ERROR` — warning: the trace event could not be written.

CLI argument errors use the same diagnostic channel (and honor `--json`):

- `PROJECTION.AS_OF_REQUIRED` — computing a projection needs `--as-of`.
- `PROJECTION.INVALID_AS_OF` — `--as-of` is not an ISO 8601 datetime with an explicit offset.
- `PROJECTION.UNKNOWN_RUN` — `--run` names no recorded run (failed runs are never recorded).
- `PROJECTION.CONFLICTING_ARGS` — `--run` was combined with `--as-of` or a projection name.

## Non-goals

A generic projection/query language is out of scope for v0.1. Additional projection kinds register their own semantics under new names.
