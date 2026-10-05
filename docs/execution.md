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

1. Backfills the proposals index (same as `decide`), then loads the
   persisted proposal (`EXECUTION.UNKNOWN_PROPOSAL` if absent).
2. Resolves the deterministic execution identity — `execution_id` and
   `idempotency_key` derive from `proposal_id` + `payload_digest` +
   adapter key (the adapter's `name`, plus its `destination` when it
   exposes one — for the CSV adapter, the output path; the two fields
   are encoded unambiguously, so no pair of values can collide). Every
   call for
   the same approved payload and destination converges on one
   execution; a different destination is a *different* execution with
   its own attempt history, so a receipt is never replayed for a file
   it did not write.
3. Checks the execution's latest recorded row:
   - `succeeded` → replays the persisted receipt
     (`execution.replayed` trace event); the adapter is never invoked,
     so repeated successful execution cannot duplicate output.
   - `uncertain` or `attempting` → the adapter may have written
     already, or the outcome never got recorded. Retry is allowed only
     when the adapter declares `idempotent` (dedupes on the
     idempotency key, so a second landing is impossible); otherwise the
     call refuses (`EXECUTION.UNCERTAIN`). For non-idempotent adapters
     the refusal is permanent for that payload — the recovery is a new
     diagnosis run producing a new proposal.
   - `failed` → proceeds: the adapter attested no side effect occurred.
4. Re-runs `check_approval` against the stored payload — immediately
   before the adapter can write. Unapproved, rejected, changed-payload,
   and contract-invalidated proposals are refused and never reach the
   adapter (refusals emit `execution.refused`, matching the
   `decision.failed` convention; they record no `executions` row).
5. Records an `attempting` intent row. If that write fails the call
   refuses without invoking the adapter — an unrecorded adapter
   invocation would be invisible to the gates above on the next run.
6. Invokes `adapter.deliver(request)` once, then records the outcome row.

## Honest outcomes — no exactly-once claim

Every adapter-reaching attempt appends two rows to `executions`
(append-only, trigger-enforced like `decisions`):

- `execution_id`, `idempotency_key`, `proposal_id`, `action`, `adapter`
- `attempt` — 1-based attempt number under that execution (the intent
  row and its outcome row share it)
- `status` — one of:
  - `attempting` — the intent row written before the adapter call. If it
    is still the latest row on a later call, the attempt's outcome was
    never recorded (interruption or store failure) and it is treated
    exactly like `uncertain`.
  - `succeeded` — the adapter returned a receipt (persisted verbatim in
    `receipt`).
  - `failed` — the adapter raised `AdapterFailed`, attesting *nothing*
    was written. Retry is permitted.
  - `uncertain` — the adapter raised anything else (including
    interruption), or its outcome could not be persisted. The write may
    or may not have landed; the error is recorded by exception class
    only (an adapter message could embed request details). Automatic
    retry follows the rule in step 3.
- `error`, `created_at`

An execution's current state derives from its latest *recorded* row
(insertion order, never wall-clock `created_at`), same as decisions.

`ExecutionResult` mirrors this: `attempt` is the attempt the call ran or
replayed, `attempts` the highest attempt number recorded under the
idempotency key (equal to `attempt` after a new attempt), and `error`
carries the recorded error text for `failed`/`uncertain` outcomes so the
operator sees *why*, not just the status — the CLI prints it and `--json`
includes it.

## The adapter contract

`ExecutionAdapter` exposes a stable `name`, an `idempotent` declaration,
an optional `destination` (folded into the execution identity as above),
and `deliver(request)`. `deliver` returns a receipt dict on success and
raises:

- `AdapterFailed` — definite no-effect failure (unsupported action,
  invalid params, destination unavailable *before* the write). Recorded
  as `failed`; safe to retry. Its message is persisted (capped at 512
  characters) and shown to the operator, so it must name *what* failed —
  never embed param values or secrets.
- anything else — recorded as `uncertain`; only the exception class is
  stored, since an arbitrary adapter message could embed request
  details.

`CsvReminderAdapter` also dedupes on the idempotency key: asked to
deliver a key it already wrote, it returns a `deduplicated: true`
receipt naming the existing row instead of appending a second one. That
is what makes it `idempotent = True` and eligible for retry after an
`uncertain` outcome. Refusing work it cannot honor honestly (a
non-`SendReminder` action or missing params) is a definite failure —
never a fabricated receipt.

The same honesty applies to a damaged output file: a `reminders.csv`
with a ragged row, a key-matching row missing structural cells, or a
final line without a newline terminator may contain a torn reminder —
the adapter raises `AdapterFailed` rather than dedupe it or append over
it.

Because reminders land in a CSV operators open in a spreadsheet, every
cell — generated identifiers included — is written with a leading `'`
when it starts with `=`, `+`, `-`, `@`, whitespace, or a control
character, to keep it inert.

## What write-back never does

- It never mutates canonical entities, events, projection or diagnosis
  runs, decisions, or the *content* of existing proposal rows — the
  only new writes are `executions` rows, the trace, and the adapter's
  own output file. The only exception is step 1: the same backfill
  repair `decide`/`proposals` run may add a missing `proposals` index
  row for a run recorded before the table existed.
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
