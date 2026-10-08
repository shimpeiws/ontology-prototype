# Outcome evaluation: baseline/after cohorts over canonical intervals

```bash
ontology evaluate <workspace> --boundary 2026-10-05T00:00:00Z --as-of 2026-10-15T00:00:00Z
ontology evaluate <workspace> --boundary 2026-10-05T00:00:00Z --as-of 2026-10-15T00:00:00Z --json
ontology evaluate <workspace> --run evl-20261015T000000000000Z-abcd1234
```

`ontology evaluate` answers the last question of the operational loop: did the business outcome move? It pairs the evaluation contract's interval events per canonical subject, splits subjects into a baseline and an after cohort at an explicit `--boundary`, and freezes a full report into the append-only `evaluation_runs` table. `--as-of` and `--boundary` are both required and must carry explicit offsets — naive times are ambiguous and are rejected, never guessed.

## Cohort and pairing rules

Only events with `occurred_at <= as_of` are visible to a run; later facts are listed in the report inputs as `future_*_fact_keys` and do not close an interval.

Cohort membership is decided by the interval's *start* event alone: a subject whose earliest visible `interval.start_event` occurred before `boundary` is in the **baseline** cohort, otherwise in the **after** cohort. A baseline pair whose end occurred at or after the boundary stays in baseline and is flagged `closes_after_boundary`.

Each subject resolves to exactly one disposition:

- **paired** — one start event and at least one end event at or after it; the earliest closing end pairs the interval. Additional ends are evidence anomalies (`EVALUATION.MULTIPLE_ENDS`) recorded on the pair.
- **pending** — one start event and no visible end event; reported with its elapsed time at `as_of`, never silently omitted.
- **invalid** — every end event precedes the start event, so the only pairings would have negative duration (`EVALUATION.NEGATIVE_INTERVAL`).
- **ambiguous** — the interval cannot be paired uniquely: more than one start event, or end events on both sides of the start (`EVALUATION.AMBIGUOUS_INTERVAL`). Ambiguous subjects are excluded from aggregates.
- **unpaired_end** — an end event with no visible start event (`EVALUATION.END_WITHOUT_START`); it belongs to no cohort and is reported under `unpaired_ends`.
- **excluded** — no visible events at all: a future-dated start event (`EVALUATION.FUTURE_EVENT`), or a subject whose only interval facts are all after `as_of`. Excluded subjects stay inspectable in the report inputs with their future fact keys; an all-future subject emits no diagnostic because it asserts nothing about the as-of state.

## Aggregates, target, guardrails

The report computes per cohort the paired intervals and their aggregate in the contract's unit (for v0.1, `median` of `duration` in `days`). `change` is `baseline − after`; for the duration metric a positive value is an improvement. Either cohort without measured intervals makes `change` and the target `unavailable`/`unverified` — honest unavailability, never a zero claim, and this holds for every aggregation: a cohort with zero paired intervals has no aggregate at all (its interval count is already explicit in `counts` and `sample`).

With `aggregation: count` the aggregate is the number of paired intervals rather than a duration, and `change.direction` is reported as `increase`/`decrease`/`unchanged` — interval-count movement, not a duration verdict.

The target compares the after aggregate with the contract comparator — `lt` is strict, so a median exactly on the target is a **miss**.

Guardrails evaluate per cohort from explicit counts and denominators over metrics declared in `evaluation.metrics` — no metric semantics live in code. A declaration names the entity it counts (`entity`), the ref-typed property linking each such entity to a cohort subject (`ref`), and exactly one predicate: `flag` — a boolean property, so entities carrying a boolean value form the denominator and `true` values the numerator, while an entity without a boolean value is an evidence gap — or `where` — a condition in the shared condition language, under which every linked entity is scored and condition-matching entities form the numerator. An optional `noun` names the numerator count in the report (default: `<flag>s`, or `matched` for a `where`). The ref is typed, so the metric applies only to intervals whose subject is the ref's declared target; for any other subject entity the declaration is not applicable and the guardrail reports `unavailable`. Entities linked to no cohort subject, and entities whose last state-changing observation postdates `as_of`, are reported as evidence gaps, never folded into the rate. `not_increase` requires rates for both cohorts; a missing denominator makes the guardrail **unavailable** — never `passed`. A guardrail naming a metric that is not declared also reports `unavailable`.

`overall` is `success` only when the target is attained and every guardrail passed; a missed target or failed guardrail is `not_met`; anything else is `unverified`. The exit code reports operational success only — `ontology evaluate` exits nonzero when the evaluation could not be computed or frozen, not on the business verdict; a `not_met` report still exits 0 and is inspected via stdout or `--json`.

Guardrail evidence is bounded by `properties_changed_at` — the last observation that actually changed the entity's stored properties. An entity whose properties last changed after `as_of` is excluded from the rate and counted in `postdating_as_of`, since current state cannot prove what its predicate value was at the instant; re-observing identical properties does not move the timestamp, so repeat ingestion cannot erase certifiable evidence.

## Frozen runs

Every successful run persists into `evaluation_runs` (append-only, like `events`):

- the contract digest — schema version, the semantics id and version, the full evaluation contract (including the declared metric semantics), the interval events' domain declarations, and the date-only timezone policy;
- the explicit `as_of` and `boundary`;
- the per-subject inputs examined — visible and future fact keys, disposition, and exclusion reason;
- the full report: cohort pairings with fact keys, counts, aggregates, change, target and guardrail verdicts.

A historical run is therefore inspectable with `evaluate --run <id>` and unchanged by later ingestion or configuration edits; a contract or definition edit changes the digest even when the report stays the same. Inspection does not load or validate the workspace definitions, so a frozen report remains readable after a configuration edit makes the workspace invalid.

Only successful runs are frozen: a run that fails (naive times, `BOUNDARY_AFTER_AS_OF`, a store error) is not recorded, so a stored run always means "evaluated to this report" and never "evaluation failed". `evaluate --run` is read-only — it does not create `.ontology/` or `canon.db` on a workspace that has not ingested — and cannot be combined with `--as-of` or `--boundary`.

## No causal claim

The report records an observed difference between time-based cohorts. The boundary is caller-supplied data — typically the intervention time — but the evaluator never ties it to a proposal or execution and makes no causal claim. The correlation between a reminder and a later invoice is already recorded on the facts themselves as `correlated_intervention` links; the outcome report deliberately does not promote correlation to causation.

## Diagnostics

- `EVALUATION.NAIVE_AS_OF` / `EVALUATION.NAIVE_BOUNDARY` — a time had no timezone offset.
- `EVALUATION.BOUNDARY_AFTER_AS_OF` — the cohort boundary is later than `as_of`.
- `EVALUATION.NEGATIVE_INTERVAL` — warning: every end event precedes the start event.
- `EVALUATION.AMBIGUOUS_INTERVAL` — warning: the interval cannot be paired uniquely.
- `EVALUATION.MULTIPLE_ENDS` — warning: several ends could close the interval; the earliest pairs it.
- `EVALUATION.END_WITHOUT_START` — warning: an end event has no start event at or before `as_of`.
- `EVALUATION.FUTURE_EVENT` — warning: a start event occurred after `as_of`.
- `EVALUATION.GUARDRAIL_UNAVAILABLE` — warning: a guardrail lacks the evidence to decide, or its semantics do not apply to the interval's subject type.
- `EVALUATION.UNSUPPORTED_GUARDRAIL` — warning: a guardrail names a metric not declared in `evaluation.metrics`.
- `EVALUATION.STORE_ERROR` — canonical state could not be read, or the frozen run could not be persisted. A store write failure still prints the computed report, but without a `run <id>` label and with a `report not frozen` note — an unrecorded run id is never presented as inspectable.
- `EVALUATION.UNKNOWN_EVENT` — the contract's interval event is not declared in the domain (unreachable through `load_workspace`, which validates it; only a directly constructed `Workspace` can produce it).
- `EVALUATION.TRACE_ERROR` — warning: the trace event could not be written.

CLI argument errors use the same diagnostic channel (and honor `--json`):

- `EVALUATION.REQUIRED_ARGS` — computing an evaluation needs `--boundary` and `--as-of`.
- `EVALUATION.INVALID_TIME` — a flag value is not an ISO 8601 datetime with an explicit offset.
- `EVALUATION.UNKNOWN_RUN` — `--run` names no recorded run (failed runs are never recorded).
- `EVALUATION.CONFLICTING_ARGS` — `--run` was combined with `--as-of` or `--boundary`.

## Non-goals

A generic cohort/query language is out of scope for v0.1. Guardrail metrics are declared data — an entity, a subject ref, and one `flag`/`where` predicate; wider joins and non-rate aggregates remain out of scope.
