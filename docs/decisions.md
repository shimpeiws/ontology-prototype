# Proposals and human decisions

Validated action proposals are persisted when a diagnosis run is recorded.
A human reviews the exact proposal through the CLI and records an explicit
approve/reject decision bound to the payload. Approval never executes
anything — write-back is a separate later stage gated by the approval
check.

## Lifecycle

```
proposed ──(decide --approve)──▶ approved
    └──────(decide --reject)───▶ rejected
```

- `proposed` — validated by diagnosis, no decision yet.
- `approved` / `rejected` — the latest recorded decision.
- Decisions are append-only: re-deciding is allowed, the newest decision
  wins, and the full history stays inspectable.

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

Proposal ids derive from the diagnosis run (`<run-id>-p<index>`), so a
proposal is always traceable back to the run and candidates that produced
it. `--json` is supported on both commands.

## What is persisted

`proposals` (append-only, trigger-enforced like events and runs):

- `proposal_id`, `diagnosis_run_id`, `proposal_index`
- `action`, `params`, `reason` — the exact validated payload
- `subject_entity`/`subject_id` — the canonical target the proposal cites
- `related_entities` — every other ref param as `{param, entity, id}`
- `evidence` — the diagnosed candidate's evidence links (fact key,
  observation ids, ingest ids)
- `payload_digest` — sha256 over canonical JSON of `{action, params}`
- `created_at`

`decisions` (append-only):

- `decision_id`, `proposal_id`, `decision` (`approved`/`rejected`)
- `reviewer`, `reason`, `decided_at`
- `payload_digest` — the digest the reviewer decided on

## Binding and the approval check

`check_approval` is the executor-facing gate. It fails closed on every
miss:

| case | result |
|---|---|
| unknown proposal id | `DECISION.UNKNOWN_PROPOSAL` |
| no decision (`proposed`) | `DECISION.NOT_APPROVED` |
| latest decision `rejected` | `DECISION.NOT_APPROVED` |
| presented action/params differ from the approved digest | `DECISION.PAYLOAD_CHANGED` |
| approved + digest match | `approved` |

Because proposals are immutable and the decision stores the reviewed
digest, any edit to a payload is a *different* payload — it fails the
check and requires a new proposal and a new decision.

## What the agent cannot do

Decisions only enter through `decide`, which requires a non-empty
reviewer identity. Model output has no path to this API: extra keys a
model adds to a proposal (e.g. `"approved": true`, `"reviewer": "x"`) are
ignored by validation and never reach the decisions table. An FDE's
decision is the only thing that moves a proposal out of `proposed`.

## Trace

Each decide emits `decision.recorded` (or `decision.failed`) to
`.ontology/trace.jsonl` with the decision id, proposal id, and decision
value.
