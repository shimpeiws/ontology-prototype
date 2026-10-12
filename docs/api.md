# Local API

`apps/api` (`ontology-api`) is a local, single-user FastAPI application
that exposes the shared core's workspace contracts over HTTP for the FDE
authoring UI. It owns transport only — request/response typing, workspace
root enforcement, and error mapping. Every validation result comes from
`ontology_core`; the API never re-validates, never runs the CLI as a
subprocess, and — on the authoring surface documented here — never touches
canonical state, proposals, approvals, or write-back storage.

The operational surface is a separate set of endpoints governed by
[ADR 0003](adr/0003-api-operational-boundary.md) (inspection reads and
the human-decision write) and
[ADR 0004](adr/0004-write-console-triggers.md) (one run trigger per loop
stage — ingest, project, diagnose, evaluate, execute) — all through the
same core, on the same `canon.db`.

## Install and startup

```bash
uv sync --package ontology-api && source .venv/bin/activate
ontology-api --root /path/to/workspaces
# or: python -m ontology_api --root /path/to/workspaces
# or: ONTOLOGY_API_ROOTS=/path/a:/path/b python -m uvicorn ontology_api.main:app
```

| Setting | Flag | Environment | Default |
| --- | --- | --- | --- |
| Workspace roots (repeatable) | `--root DIR` | `ONTOLOGY_API_ROOTS` (`os.pathsep`-separated) | none — required |
| Bind host | `--host` | `ONTOLOGY_API_HOST` | `127.0.0.1` |
| Bind port | `--port` | `ONTOLOGY_API_PORT` | `8000` |

Startup fails without at least one workspace root. The server binds to
loopback by default; this is a local tool and is not meant to be exposed
on a network interface.

### Workspace roots

Request paths may be absolute or relative. A relative path is resolved
against the configured roots — the first root containing it wins, and
when no root contains it, it resolves under the first root (so a draft's
not-yet-created identity path still stays in bounds). Client paths never
resolve against the server's working directory. Every path in a request
is resolved (symlinks followed, `..` collapsed) and must land inside one
of the configured roots — a workspace reachable only through a symlink
escape, a `..` traversal, or a lookalike prefix is rejected before any
file is read. Individual definition files that are themselves symlinks
escaping every root are validated by the core (same as the CLI) but
their contents are never returned to the client.

### CORS

The API serves a browser UI, so `OPTIONS` preflights are answered for
an explicit origin allowlist — by default the console's own dev and
preview origins (`http://localhost:5173`, `http://127.0.0.1:5173`,
`http://localhost:4173`, `http://127.0.0.1:4173`), with no credentials.
`ONTOLOGY_API_ORIGINS` (comma-separated) overrides the list at startup.
A page on any other origin — a different loopback port included —
fails preflight, so an untrusted local web page cannot drive the
write-side triggers through a browser. The Vite dev proxy remains the
primary development path (see ADR 0001); CORS exists so direct
cross-origin calls from the console's own dev server also work.

CORS is a browser control, not authentication: the API is a
single-user local tool bound to loopback and deliberately
unauthenticated — any process running under the local user account
(curl, scripts, other local tools) can call every endpoint regardless
of origin. The trust boundary is the machine.

## Endpoints

All endpoints live under `/api/v1`. Interactive OpenAPI docs are at
`/docs` on a running server.

### `GET /api/v1/contract`

Contract and schema metadata for the frontend, derived from the core's
authoritative constants: API version, core version, supported
`schema_version`, the four workspace file names, diagnostic field names,
the operation list, and the vocabularies the editors need
(`property_types`, `scalar_types`, `cast_types`, `time_casts`,
`source_formats`, `preview_limits`, condition operators, evaluation
metrics/aggregations/units/comparators, projection-selection
comparators/duration units, deliverables).

### `POST /api/v1/workspaces/open`

Load and inspect a workspace directory.

```json
{ "path": "job-invoicing" }
```

Response `200`:

```json
{
  "api_version": "v1",
  "valid": true,
  "workspace": { "path": "/abs/path", "digest": "…" },
  "files": { "domain.yaml": "…", "mapping.yaml": "…", "evaluation.yaml": "…", "agent.yaml": "…" },
  "definitions": { "domain": {…}, "mapping": {…}, "evaluation": {…}, "agent": {…} },
  "diagnostics": []
}
```

- `files` holds the byte-exact UTF-8 text of each definition file — the
  draft baseline the UI edits (no newline normalization).
- `definitions` is the parsed typed workspace (entities, bindings,
  contract, agent task); `null` when the workspace has errors.
  `definitions.mapping` is always returned in normalized `sources[]`
  form — a legacy single-source `mapping.yaml` (`source:` + top-level
  `entities`/`events`) is reported as a one-element `sources` list. This
  normalized shape applies to every response that carries `definitions`
  (`open`, `validate`, `save`, `preview`).
- `workspace.digest` is `bundle_digest` over the four files — the active
  digest the save flow (#19) checks drafts against. It is `null` when
  any file present on disk could not be returned (oversized,
  undecodable, or an escaping symlink), because a hash over a silent
  subset would misrepresent the bundle it claims to describe.
- `diagnostics` are the core's own diagnostics (`severity`, `code`,
  `message`, `file`, `path`, `line`, `hint`) — identical to what
  `ontology check` reports for the same directory.

### `POST /api/v1/workspaces/drafts/validate`

Validate a draft bundle with no side effects: nothing is read from or
written to disk, no entities/events are created, no model is called, no
write-back occurs. `path` identifies the workspace the draft belongs to
(it must still resolve under a configured root, but need not exist on
disk); `files` maps workspace file names to their draft text. Omitted
files are reported as `WORKSPACE.MISSING_FILE`. Each file's text is
subject to the same 1 MiB cap as on-disk loading; the cap is applied by
the core loader, so oversized request bodies are rejected after parsing
— acceptable for a local single-user tool.

```json
{
  "path": "job-invoicing",
  "files": { "domain.yaml": "…", "mapping.yaml": "…", "evaluation.yaml": "…", "agent.yaml": "…" }
}
```

Response `200` carries `valid`, the draft `digest`, `definitions` when
valid, and the cross-definition `diagnostics` — the same list the core
produces for the equivalent on-disk bundle, so a draft and the CLI always
agree.

### `POST /api/v1/workspaces/save`

Validate a draft bundle and activate it as the workspace on disk — the
explicit save behind every editor (#19).

```json
{
  "path": "job-invoicing",
  "base_digest": "<digest returned by workspaces/open>",
  "files": { "domain.yaml": "…", "mapping.yaml": "…", "evaluation.yaml": "…", "agent.yaml": "…" }
}
```

`base_digest` is required and non-empty: a save that cannot verify what
the draft was based on never runs. Response `200` carries a domain
outcome, never a transport error:

```json
{
  "api_version": "v1",
  "outcome": "saved",
  "valid": true,
  "digest": "…",
  "definitions": { "domain": {…}, "mapping": {…}, "evaluation": {…}, "agent": {…} },
  "diagnostics": [],
  "message": null
}
```

| outcome | Meaning | digest |
| --- | --- | --- |
| `saved` | The bundle validated and activated atomically; `definitions` is the new active workspace. | Digest of the saved bundle — the client's new base. |
| `invalid` | The draft failed whole-bundle validation; nothing was written. `diagnostics` are the core's, identical to `drafts/validate` on the same texts. | The draft digest. |
| `conflict` | The on-disk bundle no longer matches `base_digest` — an external/CLI edit or a file appearing/disappearing since open. Nothing was written; the client should reload (re-open) and review before retrying. | The current on-disk digest. |
| `failed` | Persistence failed: not a directory, an unreadable or symlinked definition file, or an I/O error during activation. A commit that failed partway is rolled back to the pre-save bytes. `message` carries the detail; a retry is meaningful. | null |

`files` keys outside the four workspace file names are rejected with
`API.UNKNOWN_WORKSPACE_FILE`. The save writes only the four definition
files — never canonical state, observations, traces, proposals, or other
files in the directory — and never creates the workspace directory
itself. Activation semantics (staging, per-file rename, rollback) are in
[workspace.md](workspace.md#saving).

### `POST /api/v1/workspaces/drafts/preview`

Resolve a draft mapping against a bounded in-memory source sample. The
draft bundle is validated exactly like `drafts/validate`; only a valid
bundle produces a `preview` payload. The preview is fully isolated: it
reads nothing from disk (the posted `files` are the whole input), writes
nothing to workspace files, `.ontology/` runtime artifacts, or the
canonical store, and never runs arbitrary expressions — only the
restricted condition language the core defines.

```json
{
  "path": "job-invoicing",
  "files": { "domain.yaml": "…", "mapping.yaml": "…", "evaluation.yaml": "…", "agent.yaml": "…" },
  "sample": { "text": "id,status,assignee\n101,completed,taro", "source": "jobs" }
}
```

`sample.source` selects which declared `mapping.sources[]` entry the
sample resolves through; it is optional when the draft declares a single
source and required for a multi-source draft (`INGEST.SOURCE_REQUIRED`,
`INGEST.UNKNOWN_SOURCE` otherwise). The sample format is the selected
source's `format` (`csv` or `json`); there is no request-side format
override. The response echoes the selection in `preview.source` and
`preview.format` — both `null` when the draft declared no usable source.
Bounds come from the
core (`MAX_PREVIEW_BYTES`, `MAX_PREVIEW_RECORDS`, exposed under
`contract.preview_limits`): an oversized sample is a `PREVIEW.*`
diagnostic, not an HTTP error, and an over-long record list is truncated
with `truncated: true` plus a warning.

Response `200`:

```json
{
  "api_version": "v1",
  "valid": true,
  "digest": "sha256:…",
  "diagnostics": [],
  "preview": {
    "preview_id": "prev-…",
    "source": "jobs",
    "format": "csv",
    "input_digest": "sha256:…",
    "mapping_digest": "sha256:…",
    "observed_at": "2026-10-05T12:00:00Z",
    "record_count": 2,
    "truncated": false,
    "observations": [
      {
        "observation_id": "obs-…",
        "ingest_id": "prev-…",
        "source": "jobs.csv",
        "row": 2,
        "status": "accepted",
        "observed_at": "…",
        "content_digest": "sha256:…",
        "mapping_digest": "sha256:…",
        "record": { "id": "101", "invoiced_at": null },
        "entities": [ { "entity": "Job", "id": "101", "properties": {…} } ],
        "events": [ { "event": "JobCompleted", "subject": {…}, "occurred_at": "…", "observed_at": "…" } ],
        "diagnostics": []
      }
    ],
    "diagnostics": []
  }
}
```

- `preview` is `null` when the draft bundle did not validate; the draft
  `diagnostics` explain why.
- `input_digest`/`mapping_digest` and the derived `preview_id`/
  `observation_id`s make the input, mapping versions, and selected source
  of a result explicit — a rerun over identical bytes under the same
  source reports identical ids, while identical bytes under a different
  source do not.
- `observations[].record` holds the normalized record (blanks/nulls as
  `null`); rejected rows keep the record and the diagnostics that
  rejected it — a record is accepted whole or rejected whole.
- `diagnostics` at the payload level aggregates every finding — run-level
  (`INGEST.ENCODING_ERROR`, `PREVIEW.TRUNCATED`, …) and per-record. Each
  observation repeats its own findings under `observations[].diagnostics`,
  so clients rendering per-row diagnostics should read that field instead
  of filtering the payload list.

## Operational inspection

Read-only views over a workspace's `.ontology/` runtime, added under
[ADR 0003](adr/0003-api-operational-boundary.md). Every payload is the
CLI's `--json` output for the same query verbatim, plus `api_version`;
domain outcomes (`ok: false` with core `diagnostics`) are `200`
responses, while `{error}` stays reserved for transport failures. All
canonical reads go through a `readonly=True` store — the read endpoints
never create `.ontology/` or `canon.db` and never mutate runtime state,
so a workspace with no recorded history reports an empty view rather
than an error. The same applies to a `canon.db` written by an older
schema revision: a read-only open never migrates, so history tables or
columns the older store lacks read as empty sections instead of a
store error. Every `.ontology/` entry must also resolve inside the
configured roots — a symlink escaping them is refused with
`API.RUNTIME_OUTSIDE_ROOTS` rather than read.

The eight read endpoints take `{"path": "<workspace under a configured
root>"}` plus the extra field listed; `path` follows the same
root-containment rules as every other endpoint. The ninth row in the
table is the human-decision write documented under
[Human decisions](#human-decisions); the five run triggers added by
ADR 0004 are documented under
[Loop run triggers](#loop-run-triggers).

| Endpoint | CLI equivalent | Success payload |
| --- | --- | --- |
| `POST /api/v1/workspaces/trace` | `ontology trace --json` | Trace overview sections (`journal`, `evidence`, `canonical`, `projection_runs`, `diagnosis_runs`, `proposals`, `decisions`, `executions`, `evaluation_runs`, `links`, `diagnostics`) |
| `POST /api/v1/workspaces/trace/subject` | `trace --subject Entity/Id --json` | `trace` — the subject's artifacts and timeline |
| `POST /api/v1/workspaces/trace/run` | `trace --run <id> --json` | `trace` — the anchored artifact (any recorded run, proposal, ingest, or execution id) |
| `POST /api/v1/workspaces/evaluations` | — (no CLI equivalent) | `evaluation_runs` — every frozen evaluation report record, in the same shape `evaluations/run` returns |
| `POST /api/v1/workspaces/evaluations/run` | `evaluate --run <id> --json` | `run` — the frozen evaluation report |
| `POST /api/v1/workspaces/proposals` | `proposals --json` | `proposals` — every persisted proposal with `status`, `decisions`, and `executions` |
| `POST /api/v1/workspaces/proposals/show` | `proposals --show <id> --json` | `proposal` — one proposal with `status`, `decisions`, and `executions` |
| `POST /api/v1/workspaces/diagnosis-runs/show` | `diagnose --inspect <id> --json` | `run` — the recorded diagnosis run (provider, model, deliverables, proposals) |
| `POST /api/v1/workspaces/proposals/decide` | `decide <id> --approve/--reject --json` | the decision outcome — see below |

Extra request fields:

- `trace/subject`: `{"subject": "Job/101"}` — a malformed subject returns
  `ok: false` with `TRACE.INVALID_SUBJECT`.
- `trace/run`: `{"run_id": "<id>"}`.
- `evaluations/run`: `{"run_id": "<id>"}`.
- `proposals/show`: `{"proposal_id": "<id>"}`.
- `diagnosis-runs/show`: `{"run_id": "<id>"}`.

Domain failures the payload can report (`ok: false`, `diagnostics`):

| Code | When |
| --- | --- |
| `TRACE.INVALID_SUBJECT` | `subject` is not `Entity/Id` |
| `TRACE.UNKNOWN_ID` | No recorded artifact with that `run_id` |
| `TRACE.JOURNAL_CORRUPT` | A journal line is unreadable — a warning; the rest of the history still assembles |
| `EVALUATION.STORE_ERROR` | The canonical store could not be opened for reading |
| `EVALUATION.UNKNOWN_RUN` | No frozen evaluation report with that `run_id` |
| `DECISION.STORE_ERROR` | The canonical store could not be opened for reading |
| `DECISION.UNKNOWN_PROPOSAL` | No persisted proposal with that `proposal_id` |
| `DIAGNOSIS.STORE_ERROR` | The canonical store could not be opened for reading |
| `DIAGNOSIS.UNKNOWN_RUN` | No recorded diagnosis run with that `run_id` |
| `WORKSPACE.*` | Proposal and diagnosis-run inspection load the workspace bundle (like the CLI), so invalid definitions surface their own diagnostics |

Trace and evaluation endpoints never load the definition files — a
frozen report or recorded run stays inspectable after a later edit
breaks the workspace; only proposal and diagnosis-run inspection
validate the bundle, matching what `ontology proposals` and
`ontology diagnose --inspect` require.

One divergence from the CLI is deliberate: proposals recorded only in a
diagnosis run — never indexed into the `proposals` table, e.g. by a
store predating the index — are not backfilled by `proposals` or
`proposals/show`, because the read-only store cannot write them. The
CLI's `proposals` backfills them on first read. The API reports the
index as-is (`proposals` omits them, `proposals/show` returns
`DECISION.UNKNOWN_PROPOSAL`) while `trace` still surfaces them under
`proposals.unindexed`; run `ontology proposals` once to index them.

## Human decisions

`POST /api/v1/workspaces/proposals/decide` is the operational surface's
review write (ADR 0003): a human approve/reject on a persisted proposal,
recorded through the shared core's `decide` on the same writable
`canon.db` the CLI uses — so a decision made over HTTP is honored by
`ontology execute` exactly like a CLI decision.

```json
{
  "path": "job-invoicing",
  "proposal_id": "dgx-…-p0",
  "decision": "approved",
  "reviewer": "fde-sato",
  "reason": "optional — recorded with the decision"
}
```

`decision` is `"approved"` or `"rejected"`; `reviewer` is required,
self-asserted, and recorded verbatim on the decision row. The response
is the `ontology decide --json` payload plus `api_version`:

```json
{
  "api_version": "v1",
  "workspace": "/abs/path",
  "ok": true,
  "decision_id": "dec-…",
  "proposal_id": "dgx-…-p0",
  "decision": "approved",
  "recorded": true,
  "diagnostics": []
}
```

`ok: false` with `diagnostics` is a domain failure — the same codes the
CLI reports (`DECISION.INVALID_DECISION`, `DECISION.NO_REVIEWER`,
`DECISION.UNKNOWN_PROPOSAL`) — and a `200` response, so a rejected
attempt is a result the UI can render, not an exception. Decisions are
append-only and bind the proposal's persisted `payload_digest`: a later
decision supersedes by insertion order, the full history stays
inspectable, and every attempt — including a failed one — emits a
`decision.recorded`/`decision.failed` trace event.

Lock contention is the one failure that is *not* a domain outcome: a
writer that loses the bounded `busy_timeout` wait reports
`STORAGE.WRITE_BUSY`, which the API maps to `503` —
`{"error": {"code": "STORAGE.WRITE_BUSY", "message": "…"}}` — because
the condition is transient and a retry is meaningful. The same
contention on the CLI exits non-zero naming `STORAGE.WRITE_BUSY`.

## Loop run triggers

One route per loop stage, added under
[ADR 0004](adr/0004-write-console-triggers.md) so the console can run
the whole loop. Each route mirrors its CLI subcommand: the success
payload is the `--json` output verbatim plus `api_version`, the run goes
through the same core call on the same `canon.db`, and a run started
over HTTP is the same trace artifact a `trace --run` lookup returns.
Domain outcomes — `ok: false` with the core's `diagnostics` — are `200`
responses; `STORAGE.WRITE_BUSY` stays the `503` transport error. What a
retry means is stage-specific: `execute` is idempotent (a re-run replays
the stored receipt) and `ingest` of identical bytes re-observes without
duplicating, while `project`, `evaluate`, and `diagnose` each record a
new run — and a `diagnose` retry is a new provider call too.

Every trigger resolves `path` under the configured roots and refuses an
`.ontology/` tree that escapes them before any write — the same guards
as every other endpoint.

| Endpoint | CLI equivalent | Success payload |
| --- | --- | --- |
| `POST /api/v1/workspaces/ingest` | `ontology ingest --json` | `workspace`, `snapshot`, `ingest_id`, `source`, `content_digest`, `mapping_digest`, `observed_at`, `accepted`, `rejected`, `canonical`, `evidence`, `diagnostics` |
| `POST /api/v1/workspaces/project` | `ontology project --json` | `run_id`, `projection`, `as_of`, `definition_digest`, `recorded`, `candidates`, `inputs`, `diagnostics` |
| `POST /api/v1/workspaces/diagnose` | `ontology diagnose --json` | `run_id`, `provider`, `model`, `status`, `proposals`, `rejected`, `deliverables`, `diagnostics` |
| `POST /api/v1/workspaces/evaluate` | `ontology evaluate --json` | `run_id`, `boundary`, `as_of`, the frozen `report`, `diagnostics` |
| `POST /api/v1/workspaces/execute` | `ontology execute --json` | `execution_id`, `idempotency_key`, `attempt`, `status`, `replayed`, `receipt`, `error`, `diagnostics` |

Request bodies all carry `"path"` plus:

```jsonc
// ingest — the snapshot text is staged server-side; `name` is a label
// sanitized to a basename, `source` is required when the mapping
// declares several sources
{ "path": "job-invoicing",
  "snapshot": { "text": "id,status,…", "name": "jobs.csv", "source": "jobs" } }

// project — `projection` optional, defaults to the agent task's
{ "path": "job-invoicing", "as_of": "2026-10-21T00:00:00Z",
  "projection": null }

// diagnose — exactly one candidate source; `provider` optional and
// defaults to the CLI's `openrouter` (the Web console preselects `stub`,
// per ADR 0004); `model` optional, resolved through the same
// env/default chain as the CLI
{ "path": "job-invoicing", "run_id": "prj-…", "as_of": null,
  "provider": "stub", "model": null }

// evaluate — both instants required, ISO 8601 with an explicit offset
{ "path": "job-invoicing", "boundary": "2026-10-15T00:00:00Z",
  "as_of": "2026-10-26T00:00:00Z" }

// execute — the persisted proposal id; the core's check_approval gate
// re-evaluates before the adapter call exactly as on the CLI
{ "path": "job-invoicing", "proposal_id": "dgx-…-p0" }
```

Validation order mirrors the CLI: a diagnosis request naming both
`run_id` and `as_of` — or neither — is refused as a domain diagnostic
(`DIAGNOSIS.CONFLICTING_ARGS` / `DIAGNOSIS.NO_INPUT`) before provider
resolution, so a credential error can never mask a missing input.
Provider credentials come only from the server environment; no request
field accepts one and no payload echoes one. Execution stays gated by
`check_approval` inside the core executor — an undecided, rejected, or
payload-changed proposal returns `ok: false` with the CLI's
`DECISION.NOT_APPROVED`, and a repeated execute of a delivered proposal
replays its receipt (`replayed: true`, `attempt` unchanged). Idempotent
re-ingest of identical bytes re-observes rather than duplicates, exactly
as on the CLI.

## Errors

API failures are structured errors, never diagnostics: the `diagnostics`
channel carries core validation findings only, so a failed request can
never masquerade as a validation result.

```json
{ "error": { "code": "API.PATH_OUTSIDE_ROOTS", "message": "…" } }
```

| HTTP | Code | Meaning |
| --- | --- | --- |
| 400 | `API.INVALID_PATH` | Path could not be resolved |
| 400 | `API.UNKNOWN_WORKSPACE_FILE` | Draft `files` contained a name outside the four definitions |
| 403 | `API.PATH_OUTSIDE_ROOTS` | Resolved path escapes every configured root |
| 403 | `API.RUNTIME_OUTSIDE_ROOTS` | A `.ontology/` entry resolves outside every configured root |
| 404 | `API.WORKSPACE_NOT_FOUND` | Resolved path is not a directory |
| 404 | `API.NOT_FOUND` | Unknown route |
| 405 | `API.METHOD_NOT_ALLOWED` | Method not allowed on a known route |
| 422 | `API.MALFORMED_REQUEST` | Body failed request schema validation |
| 500 | `API.INTERNAL_ERROR` | Unexpected server failure (details stay server-side) |
| 503 | `API.ROOTS_UNCONFIGURED` | No workspace roots configured |
| 503 | `STORAGE.WRITE_BUSY` | A write lost the bounded `busy_timeout` wait on `canon.db` — transient, retry is meaningful |

Every HTTP failure — including unknown routes and unhandled exceptions —
uses the same `{ "error": { "code", "message" } }` shape.

## Ownership

- `apps/api` owns HTTP handling, typed/versioned requests and results,
  root containment, and mapping core results to responses.
- `packages/core` owns workspace validation, the restricted YAML loader,
  and the bundle digest recipe (`load_workspace`,
  `load_workspace_texts`, `bundle_digest`).
- `apps/cli` keeps using the same files and the same loader — the API and
  CLI accept and reject identical configurations.
- `packages/core` also owns workspace persistence: `save_workspace`
  performs whole-bundle validation, the base-digest conflict check, and
  atomic activation with rollback — the API maps its outcome verbatim.
- The workspace service (`WorkspaceService` protocol) is injectable via
  `create_app(config, service)` for tests and embeddings.
