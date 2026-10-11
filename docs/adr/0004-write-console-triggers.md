# ADR 0004: Write-side console triggers for the operational loop

- Status: Accepted
- Date: 2026-10-12
- Decision owner: repository maintainer

## Context

v0.4's operational surface ([ADR 0003](0003-api-operational-boundary.md))
kept every loop stage on the CLI: the console reads recorded runs and
records human approve/reject decisions, but `ingest`, `project`,
`diagnose`, `evaluate`, and `execute` stayed terminal-only. That boundary
was pinned in tests —
`apps/web/app/test/proposals-page.test.tsx` asserts no execute
affordance, `apps/api/tests/test_operational.py::TestDecide::test_no_execute_route`
asserts no execute route, and `test_contract_lists_decide` asserts the
contract exposes no run triggers.

v0.5 asks whether the console may trigger each stage while the shared
core stays the only writer and the CLI keeps parity. The v0.4 record
itself anticipated this: exposing execution was "rejected for v0.4;
revisitable if a later milestone requires remote write-back". The
requirement is now concrete — a business demo must run the whole loop
from one screen — and the coordination groundwork (WAL, bounded
`busy_timeout`, `STORAGE.WRITE_BUSY`, the shared `check_approval` gate)
already landed with the decision endpoint.

## Decision

### Allowed triggers and confirmation per stage

The operational surface gains one run trigger per loop stage. Each is a
human-invoked request; nothing is scheduled, automatic, or bulk.

| Stage | Route | May trigger | Confirmation |
| --- | --- | --- | --- |
| Ingest | `POST /api/v1/workspaces/ingest` | Yes | Explicit submit of a named snapshot: the request carries the snapshot text, the declared source it resolves through, and a label. The submit is the confirmation — ingestion is append-only and re-ingesting identical bytes is a no-op. |
| Project | `POST /api/v1/workspaces/project` | Yes | Explicit `as_of` input per request. The submit is the confirmation — a projection run is a frozen computation, free of side effects outside the store. |
| Diagnose | `POST /api/v1/workspaces/diagnose` | Yes | Explicit candidate source (`run_id` or `as_of`) and explicit `provider` per request — the provider choice is the confirmation that a model call (possibly billed) is intended. The console defaults to `stub`, the credential-free double; `openrouter` must be chosen deliberately. `model` is optional and follows the CLI's env/default chain. |
| Evaluate | `POST /api/v1/workspaces/evaluate` | Yes | Explicit `boundary` and `as_of` per request. The submit is the confirmation — a frozen report is read-model output. |
| Execute | `POST /api/v1/workspaces/execute` | Yes | The request names the exact persisted `proposal_id`; the approval gate (`check_approval` inside the core executor) re-evaluates immediately before the adapter call, exactly as on the CLI. Because write-back delivers to the adapter destination, the console additionally requires an armed second click — an explicit "confirm write-back" step — before the request is sent. The transport itself needs no extra confirmation token: the proposal id plus the stored approval is the identity the gate checks. |

The decision endpoint (`proposals/decide`) is unchanged — it already
carries the reviewer's explicit self-asserted identity.

### Run identity and receipt display

Every trigger response is the corresponding CLI `--json` payload
verbatim, plus `api_version`. Run identity is therefore identical on
both surfaces:

- ingest → `ingest_id`, `content_digest`, evidence paths, per-row
  accepted/rejected counts and diagnostics;
- project → `run_id`, `as_of`, `candidates` with their elapsed values;
- diagnose → `run_id`, `provider`, `model`, `status`, `proposals`,
  `rejected`, deliverables;
- evaluate → `run_id`, the frozen `report` with verdicts and guardrail
  evidence;
- execute → `execution_id`, `idempotency_key`, `attempt`, `status`,
  `receipt`, `replayed`.

The console renders these ids so a run started in the UI is the same
trace artifact a `trace --run` lookup returns — a console run and a CLI
run are indistinguishable downstream because they are the same code
path.

### Failure surfaces

- **Domain failures travel in the payload**: `ok: false` with the CLI's
  `diagnostics`, at HTTP 200 — unknown projections, naive or missing
  as-of, `DIAGNOSIS.PROVIDER_ERROR`, `DECISION.NOT_APPROVED`,
  `EXECUTION.UNKNOWN_PROPOSAL`, invalid workspaces, row-level ingest
  rejections. The UI shows them as results, not crashes.
- **Provider failures during a run are recorded runs**: a model call
  that fails mid-run produces the same failed-run record the CLI
  records; resolving an unknown or uncredentialed provider produces a
  `DIAGNOSIS.PROVIDER_ERROR` diagnostic payload, never an unhandled
  error.
- **Lock contention stays transport-level**: a writer that loses the
  bounded `busy_timeout` wait reports `503 STORAGE.WRITE_BUSY` — the
  condition is transient (same mapping as `decide`). What a retry does
  is stage-specific: `execute` and `ingest` are idempotent, while
  `project`/`evaluate`/`diagnose` each record a new run — and a
  `diagnose` retry is a new provider call. The console surfaces the
  retry as a deliberate re-run, not a silent repeat.
- **Path and runtime guards are unchanged**: every trigger resolves the
  workspace under the configured roots and refuses an `.ontology` tree
  that escapes them (`API.PATH_OUTSIDE_ROOTS`,
  `API.RUNTIME_OUTSIDE_ROOTS`) before any write.
- **Validation order mirrors the CLI**: candidate-source conflicts
  (`DIAGNOSIS.CONFLICTING_ARGS`, `DIAGNOSIS.NO_INPUT`) are rejected
  before provider resolution, so a credential error can never mask a
  missing input.
- **Credentials never enter the transport**: provider keys come only
  from the server environment (`OPENROUTER_API_KEY`); no request field
  accepts a credential and no error echoes one.

### Request shapes (contract sketch)

All bodies include `"path"` (workspace under a configured root) plus:

```jsonc
// POST /api/v1/workspaces/ingest
{ "path": "…",
  "snapshot": { "text": "<csv|json bytes as text>",
                "name": "jobs-current.csv",   // optional label; sanitized to a basename
                "source": "jobs" } }          // required when several sources declared

// POST /api/v1/workspaces/project
{ "path": "…", "as_of": "2026-10-21T00:00:00Z",
  "projection": "uninvoiced_jobs" }            // optional; defaults to the agent's projection

// POST /api/v1/workspaces/diagnose
{ "path": "…",
  "as_of": "2026-10-21T00:00:00Z",   // XOR
  "run_id": "prj-…",                 // a frozen projection run
  "provider": "stub",                // optional; server defaults to the CLI's
                                     // openrouter — the console sends stub
                                     // unless deliberately changed
  "model": null }                    // optional; env/default chain like the CLI

// POST /api/v1/workspaces/evaluate
{ "path": "…", "boundary": "2026-10-15T00:00:00Z",
  "as_of": "2026-10-26T00:00:00Z" }

// POST /api/v1/workspaces/execute
{ "path": "…", "proposal_id": "dgx-…-p0" }
```

Error codes: `422 API.MALFORMED_REQUEST` (schema), `403`
(`API.PATH_OUTSIDE_ROOTS`, `API.RUNTIME_OUTSIDE_ROOTS`), `404`
`API.WORKSPACE_NOT_FOUND`, `503` (`API.ROOTS_UNCONFIGURED`,
`STORAGE.WRITE_BUSY`). Everything else is a 200 payload — `ok: true`
with the run payload, or `ok: false` with `diagnostics`.

### v0.4 invariants and tests this decision replaces

| v0.4 pin | Replacement rule | Pinning test |
| --- | --- | --- |
| `TestDecide::test_no_execute_route` — `POST /workspaces/execute` is 404 | The route exists and returns the `execute --json` payload verbatim; the approval gate still refuses undecided/rejected/unknown proposals with the CLI's codes | `test_operational.py::TestRunTriggers` — execute success, refusal, and replay cases |
| `TestDecide::test_contract_lists_decide` — `execute`/`diagnose`/`project`/`ingest`/`evaluate` absent from `operations` | `operations` names all five triggers explicitly; nothing else may appear | `test_contract_lists_run_triggers` asserting the exact operation set |
| `proposals-page.test.tsx` "no execute affordance" | The card offers execute behind an armed confirm; it shows run, attempt, receipt, and already-executed states; refusals render the CLI's diagnostic code | updated `proposals-page.test.tsx` affordance tests |
| ADR 0003 "No run triggers" / "Execution is never exposed" bullets | Superseded by this record for v0.5; ADR 0003 remains the record for storage coordination, the decision write, and self-asserted identity | — |
| `docs/api.md`/`docs/web.md`/`docs/v0.4.md` "CLI-only" claims | Rewritten to name the v0.5 boundary (this record) | — |

## Alternatives

### Keep triggers CLI-only

Rejected: it is exactly the v0.4 stance, and the milestone exists
because a two-terminal demo cannot narrate the loop.

### Triggers for read-side stages only, execution stays CLI-only

Rejecting execution while allowing the other triggers keeps the write
boundary one step shorter, but the demonstration's climax — an approved
proposal delivering — would still need a terminal. The approval gate
lives inside the core executor (`check_approval` against the persisted
payload digest, re-evaluated before the adapter call), so no transport
can bypass it; a console execute is identical to a CLI execute. The
armed confirmation covers the remaining concern, accidental clicks.
Rejected: the boundary it would protect is already structural.

### A generic "run stage" endpoint

One `POST /workspaces/run` taking a stage name would shrink the route
table but push stage-specific validation into a stringly-typed request
and break the one-to-one CLI payload parity the inspection surface
established. Rejected in favor of five named routes that mirror the CLI
subcommands.

### Async run submission with polling

Diagnosis can take tens of seconds against a live provider. A job
queue would let the UI poll, but it adds lifecycle state the single-user
local tool does not need: the CLI already blocks for the same duration,
and a synchronous request records the same run row. Rejected for v0.5;
revisitable if interactive latency becomes a real complaint.

## Consequences

- `GET /api/v1/contract` `operations` grows by five entries and the
  contract advertises the diagnosis providers.
- `ontology-api` depends on `ontology-cli` for the provider adapters
  (`resolve_provider`, `PROVIDERS`, `DEFAULT_MODEL`, `MODEL_ENV`) — the
  core stays adapter-free, and the CLI keeps its own import path
  unchanged.
- Console ingest stages the submitted text as a temp file whose name is
  the sanitized label; the stored evidence is identical to a CLI ingest
  of the same bytes.
- Reviewer/run identity stays self-asserted (ADR 0003 consequence) —
  now additionally true for the reviewer who armed the write-back.
  Multi-user or network-exposed deployment still requires revisiting
  trust, exactly as ADR 0003 warned.
- Every concurrency guarantee stays where ADR 0003 put it: SQLite WAL +
  bounded busy wait arbitrates writers, `STORAGE.WRITE_BUSY` is the only
  coordination error a caller sees, and append-only history absorbs
  cross-surface races.
- Browser access narrows from "any loopback origin" to an explicit
  allowlist (`ONTOLOGY_API_ORIGINS`, defaulting to the console's own
  dev/preview origins), so a page on an unrelated localhost port can no
  longer drive a write trigger through a browser. This is a browser
  control, not authentication: the API stays unauthenticated on purpose
  — the trust boundary is the local user account, and non-browser
  clients on the same machine can call every endpoint regardless.
