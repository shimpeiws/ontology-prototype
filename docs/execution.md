# Write-back execution

`ontology execute` turns a persisted, human-approved proposal into a
concrete effect through a narrow adapter boundary. The executor lives in
the shared core, so neither the CLI nor a future API can bypass the
approval check through an alternate route (see
[monorepo.md](monorepo.md)).

## Usage

```bash
# Execute an approved proposal through the local CSV adapter
ontology execute <workspace> <proposal-id>

# JSON output with the persisted receipt
ontology execute <workspace> <proposal-id> --json
```

The only adapter today is `csv` (the default): a local mock notification
adapter that appends one row per reminder to
`<workspace>/.ontology/reminders.csv` (a runtime artifact, git-ignored).
Each row carries `reminder_id`, `proposal_id`, `execution_id`,
`idempotency_key`, `action`, `target`, `related_entity`, `reason`, and
`created_at` — the proposal/job/target references an FDE can inspect.

## Gate and order of operations

`execute` never takes a payload from the caller: it executes exactly the
persisted proposal. Per call it:

1. Loads the persisted proposal (`EXECUTION.UNKNOWN_PROPOSAL` if absent).
2. Resolves the deterministic execution identity — `execution_id` and
   `idempotency_key` derive from `proposal_id` + `payload_digest`, so
   every call for the same approved payload converges on one execution.
3. Checks the execution's latest recorded attempt:
   - `succeeded` → replays the persisted receipt (`execution.replayed`
     trace event); the adapter is never invoked, so repeated successful
     execution cannot duplicate output.
   - `uncertain` → refuses (`EXECUTION.UNCERTAIN`): the adapter may have
     written already, so a blind retry could duplicate the effect. An
     operator reconciles manually (inspect `reminders.csv`).
   - `failed` → proceeds: the adapter attested no side effect occurred.
4. Re-runs `check_approval` against the stored payload — immediately
   before the adapter can write. Unapproved, rejected, changed-payload,
   and contract-invalidated proposals are refused and never reach the
   adapter (refusals emit `execution.refused`, matching the
   `decision.failed` convention; they record no `executions` row).
5. Invokes `adapter.deliver(request)` once.

## Honest outcomes — no exactly-once claim

Every adapter-reaching attempt appends one row to `executions`
(append-only, trigger-enforced like `decisions`):

- `execution_id`, `idempotency_key`, `proposal_id`, `action`, `adapter`
- `attempt` — 1-based attempt number under that execution
- `status` — one of:
  - `succeeded` — the adapter returned a receipt (persisted verbatim in
    `receipt`).
  - `failed` — the adapter raised `AdapterFailed`, attesting *nothing*
    was written. Retry is permitted.
  - `uncertain` — the adapter raised anything else (including
    interruption). The write may or may not have landed; the error is
    recorded by exception class only (an adapter message could embed
    request details). Automatic retry is refused — see step 3.
- `error`, `created_at`

An execution's current state derives from its latest *recorded* attempt
(insertion order, never wall-clock `created_at`), same as decisions.

## The adapter contract

`ExecutionAdapter` exposes a stable `name` and `deliver(request)`.
`deliver` returns a receipt dict on success and raises:

- `AdapterFailed` — definite no-effect failure (unsupported action,
  invalid params, destination unavailable *before* the write). Recorded
  as `failed`; safe to retry.
- anything else — recorded as `uncertain`.

`CsvReminderAdapter` also dedupes on the idempotency key: asked to
deliver a key it already wrote, it returns a `deduplicated: true`
receipt naming the existing row instead of appending a second one.
Refusing work it cannot honor honestly (a non-`SendReminder` action or
missing params) is a definite failure — never a fabricated receipt.

## What write-back never does

- It never mutates canonical entities, events, projection or diagnosis
  runs, proposals, or decisions — the only writes are `executions`
  rows, the trace, and the adapter's own output file.
- It never fabricates business outcomes: writing a reminder does not
  create an `InvoiceIssued` event or touch the source data. The loop is
  closed by re-observing the real source, not by faking the result.

## Trace

Every `execute` call emits one event to `.ontology/trace.jsonl`:
`execution.refused`, `execution.succeeded`, `execution.failed`,
`execution.uncertain`, or `execution.replayed`, with execution id,
idempotency key, proposal id, adapter, attempt, status, and error codes.
A trace write failure surfaces as an `EXECUTION.TRACE_ERROR` warning —
a recorded execution without a trace event is never silently normal.
