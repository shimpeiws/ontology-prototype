# Local API

`apps/api` (`ontology-api`) is a local, single-user FastAPI application
that exposes the shared core's workspace contracts over HTTP for the FDE
authoring UI. It owns transport only — request/response typing, workspace
root enforcement, and error mapping. Every validation result comes from
`ontology_core`; the API never re-validates, never runs the CLI as a
subprocess, and never touches canonical state, proposals, approvals, or
write-back storage.

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
  "sample": { "text": "id,status,assignee\n101,completed,taro" }
}
```

The sample format is the draft's `mapping.source.format` (`csv` or
`json`); there is no request-side format override. Bounds come from the
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
  `observation_id`s make the input and mapping versions of a result
  explicit — a rerun over identical bytes reports identical ids.
- `observations[].record` holds the normalized record (blanks/nulls as
  `null`); rejected rows keep the record and the diagnostics that
  rejected it — a record is accepted whole or rejected whole.
- `diagnostics` at the payload level aggregates every finding — run-level
  (`INGEST.ENCODING_ERROR`, `PREVIEW.TRUNCATED`, …) and per-record. Each
  observation repeats its own findings under `observations[].diagnostics`,
  so clients rendering per-row diagnostics should read that field instead
  of filtering the payload list.

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
- The workspace service (`WorkspaceService` protocol) is injectable via
  `create_app(config, service)` for tests and embeddings.

Saving drafts to disk, digest conflict detection, and atomic activation
belong to the save/interoperability issue (#19) and are intentionally not
endpoints yet.
