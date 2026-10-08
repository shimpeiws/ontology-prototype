# ADR 0003: API boundary for operational state

- Status: Proposed
- Date: 2026-10-07
- Decision owner: repository maintainer

## Context

The v0.4 milestone (FDE Operational Console) lets an FDE follow a run from
evidence to evaluation in the UI and record approve/reject decisions that the
CLI executor honors exactly like CLI decisions. Until now the API was kept
away from all operational state: [ADR 0001](0001-web-framework.md) states that
the authoring flow does not access canonical SQLite data, traces, proposals,
approvals, or write-back storage, and [api.md](../api.md) repeats that
boundary. [monorepo.md](../monorepo.md) requires an explicit storage-
coordination decision before the API touches operational state, because the
API process and CLI invocations share the same `.ontology/` store.

The operational store today:

- `SqliteCanonicalStore` holds canonical facts, frozen runs, proposals,
  decisions, executions, and evaluation reports in `.ontology/canon.db`.
  History tables — events, runs, proposals, decisions, executions, fact
  links — are append-only by trigger. The `entities` table is different: a
  current-state read model updated by upsert (`ON CONFLICT DO UPDATE`),
  not history. Each write runs in a single transaction that commits whole
  or rolls back. Connections carry Python's default five-second SQLite
  timeout rather than an explicit `busy_timeout`, so a writer that meets a
  held lock waits up to five seconds before failing with `SQLITE_BUSY`,
  and with the default rollback journal a reader can hit the same lock
  while a write transaction is open.
- `.ontology/trace.jsonl` is an append-only evidence journal; single-line
  appends from two processes can interleave at worst. It is audit evidence,
  not correctness state.
- `traceview` reassembles the store (opened `readonly=True`), the journal,
  and the evidence files into a linked, strictly read-only run history.
- `decisions.decide` is the only decision path: it requires a non-empty
  `reviewer`, records append-only decisions bound to the proposal's payload
  digest, and emits a trace event even for failed attempts.
  `check_approval` is the executor-facing gate and fails closed on every
  miss; `execution.execute` is the only write-back stage.

## Decision

### Operational surface

The API gains an **operational surface** alongside the authoring surface,
with exactly these capabilities:

- **Inspection reads.** The API may read operational state through core read
  APIs only — `SqliteCanonicalStore(readonly=True)` for canonical facts,
  frozen runs, proposals with evidence and decision history, execution
  records, and evaluation reports, plus `traceview` for the linked run
  history and the trace journal. The API never opens the SQLite file itself,
  never queries it with its own SQL, and never reimplements assembly logic
  the core already owns.
- **Decision writes.** The API may record human decisions — and nothing else
  — by calling `decisions.decide` directly. That call also runs its
  documented, idempotent proposal-index backfill (`backfill_proposals`) for
  diagnosis runs recorded before the `proposals` index existed: the repair
  writes append-only index rows inside the same path and is part of the
  decision operation's contract, not a second mutation surface. The request
  carries an explicit non-empty `reviewer`, enforced by the same
  `DECISION.NO_REVIEWER` diagnostic the CLI relies on. Reviewer identity is
  self-asserted, the same trust level as `ontology decide --reviewer` on
  this single-user local tool. An API-recorded decision is an ordinary row
  in the append-only `decisions` table: `check_approval` and
  `ontology execute` honor it exactly like a CLI decision because there is
  one gate and one store.
- **No run triggers in v0.4.** Ingest, project, diagnose, and evaluate stay
  CLI-invoked. The console follows recorded runs; it does not start them.
- **Execution is never exposed.** `execute` and the write-back adapters stay
  CLI-only. Approval recorded through the API authorizes a payload; it never
  triggers delivery. `check_approval` remains the only gate, inside the
  shared execution boundary, so no transport can bypass it.

The authoring surface (`open`, `validate_draft`, `save`, `preview_draft`)
keeps its v0.2 boundary unchanged.

### Storage coordination

One `.ontology/` store per workspace is shared by the API process and CLI
invocations. Coordination is decided as follows:

- **SQLite is the arbiter of the single writer.** No advisory lock files and
  no API-side mutex as the mechanism — a process-level lock cannot arbitrate
  a CLI process, and a second lock registry would drift from the store it
  claims to guard. In-process serialization inside the API may complement
  the engine's arbitration but never replaces it.
- **The store runs in WAL journal mode with an explicit bounded
  `busy_timeout`.** Readers never block behind a writer and never see a
  partial transaction — a read returns the last committed state. Writers
  already wait under Python's five-second default; making the bound
  explicit lets each surface choose it deliberately instead of inheriting a
  library constant. A writer that cannot obtain the write lock within the
  bounded wait fails instead of holding the request open.
- **The named failure behavior for concurrent CLI/API writes is
  `STORAGE.WRITE_BUSY`.** The losing writer's transaction rolls back whole —
  never a partial write — and the failure is surfaced as a typed, retryable
  error: the CLI exits non-zero naming the busy store, and the API maps it
  to `503` with `error.code` so the caller knows a retry is meaningful.
- **Concurrent decisions are not a write conflict.** Decisions are
  append-only and the newest *recorded* decision wins by insertion order,
  not wall-clock `decided_at`. Two racing decides on the same proposal both
  persist and stay inspectable; the record arbitrates, not a lock. The same
  holds for every append-only history table — races append, they do not
  overwrite. The `entities` current-state table is the exception:
  concurrent merges are serialized writers, each committing whole, and the
  upsert's guard keeps the *newest observed* state — an incoming
  observation wins the row only when its `updated_at`, tie-broken by
  `last_ingest_id`, outranks the stored one — with the event stream still
  carrying every observation behind it.
- **`trace.jsonl` stays best-effort.** Concurrent appends may interleave on
  oversized events; the journal is evidence, so no lock is taken on it.
  Correctness state lives only in `canon.db`.

Enabling WAL and an explicit `busy_timeout` on `SqliteCanonicalStore`,
plus the `STORAGE.WRITE_BUSY` mapping in the CLI and API, was deferred to
the first write-capable endpoint — it landed with the decision endpoint
and Proposals console (issue #63).

### Document amendments

- [ADR 0001](0001-web-framework.md)'s boundary section is rescoped to the
  authoring flow; the operational surface is governed by this record.
- [api.md](../api.md) states the two-surface boundary: the authoring
  surface's exclusions stand, and the operational surface is limited to
  inspection reads and decision writes through the core — never execution,
  write-back, or run triggers.
- [authoring-flow.md](../authoring-flow.md) keeps stating that the authoring
  UI holds no approval or write-back path, and notes that operational
  governance is a separate surface covered by this record.
- [monorepo.md](../monorepo.md)'s storage-coordination requirement is
  satisfied by the coordination section above.

## Alternatives

### Keep the API authoring-only

The v0.2 boundary forbids all operational access, which blocks the console
entirely — every trace, proposal, and decision view would need a non-API
channel. Rejected: it defeats the milestone this decision exists to unblock.

### Expose execution through the API

Letting the API call `execute` would remove the last human-invoked boundary
before write-back delivery. Write-back is an operational act with external
side effects; the console's scope is inspection and governance. Rejected for
v0.4; revisitable if a later milestone requires remote write-back, at which
point authentication and delivery confirmation become real requirements.

### The API brokers all writes, including the CLI's

Making the API the single writer — the CLI calling the API for store
mutations — would centralize locking, but it forces every local CLI
invocation to depend on a running server and adds a network hop to a tool
that must work standalone. Rejected.

### Advisory lock files or a shared mutex

A lock file beside `canon.db` could serialize writers, but it introduces a
second source of truth that can leak or drift (stale locks after a crash)
and still leaves readers racing writers. Rejected in favor of the engine's
own arbitration.

### Keep the implicit default wait

Python's `sqlite3` already applies a five-second busy handler, so the
store today does wait — silently and untyped. Depending on an implicit
library default leaves the bound undocumented and unversioned: it cannot
be tuned per surface, and the failure still arrives as a bare
`database is locked` with no contract name. Rejected: the wait itself is
fine — it is the implicitness that fails the coordination requirement.

## Consequences

- The `operations` list in `GET /api/v1/contract` will grow when the v0.4
  endpoints land; `test_contract_exposes_no_action_authority` is updated
  then to keep `execute`, `diagnose`, and run triggers forbidden.
- `SqliteCanonicalStore` gains WAL mode and an explicit `busy_timeout`,
  and busy errors get the `STORAGE.WRITE_BUSY` name in the CLI and `503`
  in the API — small core and transport changes owned by the endpoint
  issues.
- Append-only history plus serialized entity upserts carry the concurrency
  story: races between writers produce ordered appends or
  observation-ordered upserts, not corruption, and the busy failure is the
  only coordination error a caller ever sees.
- Reviewer identity is self-asserted on both surfaces. Any future
  multi-user or network-exposed deployment must revisit this — it is a
  deliberate consequence of the single-user local tool, not an oversight.
- This record is Proposed until the maintainer accepts it; the endpoint
  issues should not start from it before acceptance.
