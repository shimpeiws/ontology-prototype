# Proposals and human decisions

Validated action proposals are persisted when a diagnosis run is recorded.
A human reviews the exact proposal — through the CLI, or through the Web
console's Proposals page which posts to the API's `decide` endpoint
([api.md](api.md#human-decisions)) — and records an explicit
approve/reject decision bound to the payload. Both surfaces run the same
core `decide` on the same `canon.db`: a decision recorded over HTTP is
honored by `ontology execute` exactly like a CLI decision. Approval never
executes anything — write-back is a separate stage gated by the approval
check, implemented by `ontology execute` (see
[execution.md](execution.md)). The API exposes no execution or run
trigger (ADR 0003).

## Lifecycle

```
proposed ──(decide --approve)──▶ approved
    └──────(decide --reject)───▶ rejected
```

- `proposed` — validated by diagnosis, no decision yet.
- `approved` / `rejected` — the latest recorded decision.
- Decisions are append-only: re-deciding is allowed, the newest *recorded*
  decision wins (insertion order, not wall-clock `decided_at` — a clock
  rewind cannot resurrect an overridden decision), and the full history
  stays inspectable.

## Usage

```bash
# List persisted proposals with their current status
ontology proposals <workspace>

# Inspect one proposal: payload, digest, canonical target,
# related entities, evidence links, and decision history
ontology proposals <workspace> --show <proposal-id>

# Record a human decision (reviewer is required and recorded)
ontology decide <workspace> <proposal-id> --approve --reviewer fde-sato \
    --reason "correct job and assignee"
ontology decide <workspace> <proposal-id> --reject --reviewer fde-sato
```

The Web console's Proposals page is the same review rendered over HTTP:
each card shows the exact `action`/`params`, the `payload_digest` the
decision will bind, the canonical target and related entities, the
grounding evidence, and the recorded AI rationale — labeled as model
hypothesis, never as fact — plus the full decision history and any
recorded execution attempts (status only; there is no execute button).
Approve/Reject posts `decision`, the page-level reviewer identity, and an
optional reason to `POST /api/v1/workspaces/proposals/decide`.

Proposal ids derive from the diagnosis run (`<run-id>-p<index>`), where
`<index>` is the proposal's position in the model's raw `action_proposals`
output — so a persisted proposal always points back at the exact recorded
output item it was validated from. `--json` is supported on both commands.

## What is persisted

`proposals` (append-only, trigger-enforced like events and runs):

- `proposal_id`, `diagnosis_run_id`, `proposal_index`
- `action`, `params`, `reason` — the exact validated payload
- `subject_entity`/`subject_id` — the canonical target the proposal cites
  (the first ref param to the projection subject)
- `related_entities` — every other ref param as `{param, entity, id}`,
  including additional refs to the subject entity, so a proposal
  targeting two candidates keeps both visible
- `evidence` — the diagnosed candidate's evidence links (fact key,
  observation ids, ingest ids)
- `payload_digest` — sha256 over `"v1\n" + canonical JSON of
  `{action, params}` (the version tag lets a future recipe change be told
  apart from a payload change)
- `created_at`

`decisions` (append-only):

- `decision_id`, `proposal_id`, `decision` (`approved`/`rejected`)
- `reviewer`, `reason`, `decided_at`
- `payload_digest` — the digest the reviewer decided on

## Binding and the approval check

`check_approval` is the executor-facing gate. It requires the exact
payload about to execute (`action`/`params` are mandatory — there is no
"check whatever is stored" mode) and fails closed on every miss:

| case | result |
|---|---|
| unknown proposal id | `DECISION.UNKNOWN_PROPOSAL` |
| no decision (`proposed`) | `DECISION.NOT_APPROVED` |
| latest recorded decision `rejected` | `DECISION.NOT_APPROVED` |
| presented action/params differ from the approved digest | `DECISION.PAYLOAD_CHANGED` |
| approved payload no longer satisfies the current contract | `DECISION.CONTRACT_CHANGED` |
| approved + digest match + contract still holds | `approved` |

Because proposals are immutable and the decision stores the reviewed
digest, any edit to a payload is a *different* payload — it fails the
check and requires a new proposal and a new decision. The gate also
revalidates the approved payload against the *current* workspace
contract: an action removed from `permitted_actions`, a param schema
change, or a ref target that no longer resolves all fail the check —
an approval made under an old contract cannot silently ride into
execution under a new one.

## Repair and backfill

`proposals` is a review index over the diagnosis run record — never the
only copy. Two gaps self-heal the next time `proposals`/`decide` runs:

- Runs recorded before the `proposals` table existed keep their
  validated proposals only inside the run record; inspection backfills
  index rows for them (with empty `evidence`, since run records keep no
  candidates).
- A run whose proposal indexing failed (`DIAGNOSIS.PROPOSAL_ROWS_ERROR`)
  is repaired the same way.

Backfilled rows reconstruct history conservatively:

- `subject_entity` comes from the frozen projection run (not the current
  contract), so a workspace edited since diagnosis cannot relabel what
  was proposed.
- Rows already indexed for a run keep their ids: a proposal is matched
  to an existing row positionally first (same `proposal_index` and
  payload digest), then by payload digest plus `reason` to reconcile
  ids assigned under an older numbering scheme — decisions recorded
  against a row are never split across a renumbered duplicate, and
  identical payloads are told apart by their reason.
- For records that carry the model-output `index`, the `-pN` suffix is
  the raw output position. Older records without it try to recover the
  position by matching each proposal back to the recorded model output
  (single unambiguous action+params match required); when no match is
  possible the suffix is the validated-array position, and collisions
  inside a run are resolved by scanning upward — in both cases the
  suffix identifies the row, not a guaranteed raw-output offset.
- Backfill is idempotent and best-effort: a failure leaves the table
  unchanged rather than breaking inspection.

## What the agent cannot do

Decisions only enter through `decide` — the CLI command or the API's
`proposals/decide` endpoint, which runs the same core function — and both
require a non-empty reviewer identity. Model output has no path to this
API: extra keys a model adds to a proposal (e.g. `"approved": true`,
`"reviewer": "x"`) are ignored by validation and never reach the
decisions table. An FDE's decision is the only thing that moves a
proposal out of `proposed`.

## Concurrent writers

The CLI and the API share the same `canon.db`. The store runs in WAL
journal mode with an explicit bounded `busy_timeout` (five seconds by
default), so readers never block behind a writer and never see a partial
transaction. A writer that cannot obtain the write lock within the bound
fails with the stable `STORAGE.WRITE_BUSY` code — on the CLI a non-zero
exit naming that code, on the API a `503` — instead of an untyped
`database is locked`. Contention is transient: the same call succeeds
once the holder commits (see
[ADR 0003](adr/0003-api-operational-boundary.md)).

## Trace

Every `decide` call — including failed attempts (unknown proposal,
invalid decision value, missing reviewer) — emits `decision.recorded` or
`decision.failed` to `.ontology/trace.jsonl` with the decision id,
proposal id, and decision value. A trace write failure itself surfaces
as a `DECISION.TRACE_ERROR` warning: a recorded decision without a
trace event is never silently normal.
