# Local Web app

`apps/web` (`ontology-web`) is the FDE workspace authoring UI: a React
Router v7 Framework Mode app built as a static SPA (`ssr: false`, per
[ADR 0001](adr/0001-web-framework.md)). It implements the web shell half
of the authoring contract in [authoring-flow.md](authoring-flow.md):
workspace open, navigation across the four definitions, and the shared
loading/error/empty/draft-status patterns.

## Boundaries

The Web app is presentation only:

- It reaches workspace data exclusively through `POST/GET /api/v1`
  ([api.md](api.md)). It contains no SQLite access, no filesystem access,
  and no reimplementation of core validation — every diagnostic shown is
  the core's own `severity`/`code`/`file`/`path`/`line`/`hint` payload.
- API failures are structured errors (`{error: {code, message}}`) rendered
  in an error state with a retry affordance only when the failure is
  retryable (network/5xx). They never appear in the diagnostics list;
  client-synthesized failures use `WEB.*` codes so they cannot be confused
  with the server's `API.*` codes.
- Draft edits live in browser memory: a draft layer over the byte-exact
  active bundle. Editing marks a file `unsaved`, never mutates the active
  baseline, and invalidates any displayed draft-validation result.
  "Validate draft" posts the whole bundle to `/api/v1/workspaces/drafts/validate`.
  Saving to disk is not implemented here (#19).
- The Domain page offers structured authoring on top of the raw editor: a
  visual graph of entities/events/projections, an inspectable list, and
  per-concept forms that mutate the draft `domain.yaml` through the
  `yaml` CST-preserving document API — comments, key order, and unmodeled
  sections survive edits. Rename/remove goes through a reference-impact
  panel that scans all four draft files at the positions the core
  validators dereference (`app/lib/references.ts`); the FDE chooses
  whether references are retargeted. Property types come from
  `GET /api/v1/contract`, never a hardcoded client copy. Projections can
  be renamed, removed, and re-subjected but not created — projection
  authoring is an unassigned gap in authoring-flow.md. The Mapping /
  Evaluation / Agent task pages still render read-only definitions next
  to the raw editor; their forms belong to #17/#18.

## Development

From a clean checkout (Python 3.11+, Node ≥ 20.19):

```bash
# API
pip install -e "packages/core" -e "apps/api"
ontology-api --root examples          # serves /api/v1 on 127.0.0.1:8000

# Web (separate terminal)
npm --prefix apps/web ci
npm --prefix apps/web run dev         # http://127.0.0.1:5173
```

The Vite dev server proxies `/api` to `http://127.0.0.1:8000`, so the
browser stays same-origin. To point the client elsewhere, set
`VITE_API_BASE_URL` (default `/api/v1`).

Open `http://127.0.0.1:5173`, enter `job-invoicing` (resolved against the
API's `--root`), and the overview shows the bundle status, active digest,
per-file status, and core diagnostics. The Domain / Mapping / Evaluation /
Agent task pages render the parsed definitions next to a raw-YAML draft
editor; unsaved changes warn before in-app navigation and tab close.

## Checks

```bash
npm --prefix apps/web run typecheck   # react-router typegen + tsc
npm --prefix apps/web run test        # vitest (jsdom)
npm --prefix apps/web run build       # static SPA → apps/web/build/client
```

`build/client` is a static bundle; serve it behind the same origin as the
API (or a reverse proxy forwarding `/api`) — there is no runtime server.

## Testing note — synthetic fixture mode

Unit tests run in jsdom with `fetch` stubbed and use hand-constructed
payloads in `app/test/fixtures.ts` — explicitly marked **SYNTHETIC
FIXTURE**, shaped after the documented contract and the job-invoicing
example. They verify client behavior (error mapping, draft/dirty/staleness
rules, labeled controls), not the API itself. Contract-level checks belong
to `apps/api` tests; real end-to-end loading uses the commands above.

## Known limitation

Workspace state is held in memory: a full page reload returns to the open
form. Drafts are intentionally not persisted to browser storage
(authoring-flow.md lists draft-restore as an open gap).
