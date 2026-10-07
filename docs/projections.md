# Projections: deterministic candidates over canonical events

```bash
ontology project <workspace> --as-of 2026-10-05T00:00:00Z
ontology project <workspace> CompletedButNotInvoiced --as-of 2026-10-05T00:00:00Z --json
ontology project <workspace> --run prj-20261005T000000000000Z-abcd1234
```

`ontology project` computes a declared domain projection over the canonical event store and freezes the run. `--as-of` is required and must carry an explicit offset — a naive time is ambiguous and is rejected, never guessed. The projection name defaults to the workspace agent's `agent.projection`.

## Declared selection

A projection's semantics are authored in `domain.yaml` under `select`, not registered in code: `required_events` must all have occurred by `as_of`, no `absent_events` may have occurred by `as_of`, the optional `elapsed` comparison holds, and the optional `where` condition matches the subject's latest observed properties. See [workspace.md](workspace.md) for the vocabulary.

For `CompletedButNotInvoiced` (`select.required_events: [JobCompleted]`, `absent_events: [InvoiceIssued]`, `elapsed: gt 2 days`): a subject is a candidate when `JobCompleted` occurred, no `InvoiceIssued` had occurred by `as_of`, and `as_of − occurred_at` is strictly greater than two days. Exactly two days is not a candidate, and a future-dated start event is excluded with a `PROJECTION.FUTURE_EVENT` warning.

When several start events exist for one subject, the earliest `occurred_at` sets the clock — the first completion is when the delay began. Any end event with `occurred_at <= as_of` excludes the subject regardless of ordering; end events that occurred *after* `as_of` do not (a later invoice does not change that the job was uninvoiced at `as_of`) and are listed in the run inputs as `future_end_fact_keys`.

Each candidate carries the canonical subject, the subject entity's latest observed properties (for job-invoicing that includes `assignee`), the completion time, the elapsed duration, and evidence references: the start fact key plus every observation id and ingest id that asserted it. Entity properties are the latest *observed* state, not the state as of `as_of` — they may postdate the evaluation instant, so candidates expose `evidence.entity_updated_at` to mark their provenance. As-of-correct property history is a later concern (reconciliation in #8).

## Frozen runs

Every run persists into `projection_runs` (append-only, like `events`):

- the projection definition digest — schema version, the full projection declaration (including its `select`), the evaluator version, the full evaluation contract, the referenced events' declarations, and the date-only timezone policy;
- the input references examined — per subject, the anchor start fact keys (`start_fact_keys`), the absent/end fact keys (`end_fact_keys`, `future_end_fact_keys`), the per-required-event fact keys (`required_fact_keys`) when the selection names more than the anchor event, and the exclusion reason;
- the full candidate set as computed.

A historical run is therefore inspectable with `project --run <id>` and unchanged by later ingestion or configuration edits; a definition or contract edit changes the digest even when the candidate list stays the same.

Only successful runs are frozen: a run that fails (`SUBJECT_MISMATCH`, a store error, …) is not recorded, so a stored run always means "evaluated to this candidate set" and never "evaluation failed". The attempt is still traced as `projection.failed` (successful runs emit `projection.completed`), so no record in `projection_runs` plus a failed trace event is inspectable evidence of a failed run. `project --run` is read-only — it does not create `.ontology/` or `canon.db` on a workspace that has not ingested — and cannot be combined with `--as-of` or a projection name.

## Diagnostics

- `PROJECTION.UNKNOWN_PROJECTION` — the name is not declared in `domain.projections`.
- `PROJECTION.MISSING_SELECTION` — the projection carries no `select` (unreachable through the loader, which requires it; guards programmatically built workspaces).
- `PROJECTION.NAIVE_AS_OF` — `as_of` had no timezone offset.
- `PROJECTION.SUBJECT_MISMATCH` — a `select`-referenced event does not exist or is not bound to the projection subject.
- `PROJECTION.FUTURE_EVENT` — warning: a start event occurred after `as_of`.
- `PROJECTION.STORE_ERROR` — canonical state could not be read, or the frozen run could not be persisted.
- `PROJECTION.TRACE_ERROR` — warning: the trace event could not be written.

CLI argument errors use the same diagnostic channel (and honor `--json`):

- `PROJECTION.AS_OF_REQUIRED` — computing a projection needs `--as-of`.
- `PROJECTION.INVALID_AS_OF` — `--as-of` is not an ISO 8601 datetime with an explicit offset.
- `PROJECTION.UNKNOWN_RUN` — `--run` names no recorded run (failed runs are never recorded).
- `PROJECTION.CONFLICTING_ARGS` — `--run` was combined with `--as-of` or a projection name.

## Non-goals

A generic projection/query language is out of scope. The `select` vocabulary is deliberately restricted to what the shipped workflows need; richer selection (aggregation-based projections, earliest-per-subject derivation) would extend the vocabulary, not introduce a code registry.
