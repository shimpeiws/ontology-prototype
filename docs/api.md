# Local API

`apps/api` (`ontology-api`) is a local, single-user FastAPI application
that exposes the shared core's workspace contracts over HTTP for the FDE
authoring UI. It owns transport only — request/response typing, workspace
root enforcement, and error mapping. Every validation result comes from
`ontology_core`; the API never re-validates, never runs the CLI as a
subprocess, and — on the authoring surface documented here — never touches
canonical state, proposals, approvals, or write-back storage.

The operational surface is a separate set of endpoints governed by
[ADR 0003](adr/0003-api-operational-boundary.md): inspection reads and
human decision writes through the same core — never execution, write-back,
or run triggers.

## Install and startup

```bash
pip install -e "packages/core" -e "apps/api"
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
loopback origins only — `http(s)://localhost:<port>` and
`http(s)://127.0.0.1:<port>` — with no credentials. Any other origin is
rejected. The Vite dev proxy remains the primary development path (see
ADR 0001); CORS exists so direct cross-origin calls from a local dev
server also work.

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
metrics/aggregations/units/comparators, deliverables).

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
canonical reads go through a `readonly=True` store — these endpoints
never create `.ontology/` or `canon.db` and never mutate runtime state,
so a workspace with no recorded history reports an empty view rather
than an error.

All seven take `{"path": "<workspace under a configured root>"}` plus
the extra field listed; `path` follows the same root-containment rules
as every other endpoint.

| Endpoint | CLI equivalent | Success payload |
| --- | --- | --- |
| `POST /api/v1/workspaces/trace` | `ontology trace --json` | Trace overview sections (`journal`, `evidence`, `canonical`, `projection_runs`, `diagnosis_runs`, `proposals`, `decisions`, `executions`, `evaluation_runs`, `links`, `diagnostics`) |
| `POST /api/v1/workspaces/trace/subject` | `trace --subject Entity/Id --json` | `trace` — the subject's artifacts and timeline |
| `POST /api/v1/workspaces/trace/run` | `trace --run <id> --json` | `trace` — the anchored artifact (any recorded run, proposal, ingest, or execution id) |
| `POST /api/v1/workspaces/evaluations` | — (no CLI equivalent) | `evaluation_runs` — every frozen evaluation report record, in the same shape `evaluations/run` returns |
| `POST /api/v1/workspaces/evaluations/run` | `evaluate --run <id> --json` | `run` — the frozen evaluation report |
| `POST /api/v1/workspaces/proposals` | `proposals --json` | `proposals` — every persisted proposal with `status`, `decisions`, and `executions` |
| `POST /api/v1/workspaces/proposals/show` | `proposals --show <id> --json` | `proposal` — one proposal with `status`, `decisions`, and `executions` |

Extra request fields:

- `trace/subject`: `{"subject": "Job/101"}` — a malformed subject returns
  `ok: false` with `TRACE.INVALID_SUBJECT`.
- `trace/run`: `{"run_id": "<id>"}`.
- `evaluations/run`: `{"run_id": "<id>"}`.
- `proposals/show`: `{"proposal_id": "<id>"}`.

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
| `WORKSPACE.*` | Proposal inspection loads the workspace bundle (like the CLI), so invalid definitions surface their own diagnostics |

Trace and evaluation endpoints never load the definition files — a
frozen report or recorded run stays inspectable after a later edit
breaks the workspace; only proposal inspection validates the bundle,
matching what `ontology proposals` requires.

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
| 404 | `API.WORKSPACE_NOT_FOUND` | Resolved path is not a directory |
| 404 | `API.NOT_FOUND` | Unknown route |
| 405 | `API.METHOD_NOT_ALLOWED` | Method not allowed on a known route |
| 422 | `API.MALFORMED_REQUEST` | Body failed request schema validation |
| 500 | `API.INTERNAL_ERROR` | Unexpected server failure (details stay server-side) |
| 503 | `API.ROOTS_UNCONFIGURED` | No workspace roots configured |

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
