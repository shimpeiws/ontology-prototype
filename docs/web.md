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
  "Revert to active" (per file) and "Revert all" (whole bundle) restore the
  byte-exact active text behind an explicit, cancellable confirmation; a
  revert bumps the draft revision, so shown validation and preview results
  go stale exactly like an edit.
  "Validate draft" posts the whole bundle to `/api/v1/workspaces/drafts/validate`.
  "Save" is explicit only (never autosave): it posts the draft with the
  active digest as `base_digest` to `/api/v1/workspaces/save`, and the four
  outcomes render distinctly — `saved` rebases the active bundle to the
  sent files, `invalid` surfaces the core diagnostics the save ran,
  `conflict` offers an explicit reload that re-bases the draft without
  losing it, and `failed` offers retry. Save is disabled while the draft
  is clean, while a save or reload is in flight, or the active digest is
  unavailable, because a baseless save can never be conflict-checked.
  Save and reload are mutually exclusive in flight — a late save response
  is discarded rather than allowed to roll the reloaded baseline back.
  Every discard path — revert, remove from draft, close, and leave — is
  disabled while a save is in flight, because a sent save cannot be
  recalled. A pending revert confirmation is bound to the active baseline
  it was armed against: it is withdrawn when a save starts, when the draft
  turns clean, or when a landed save or reload replaces the baseline, so it
  can never discard against on-disk text the FDE did not confirm.
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
  authoring is an unassigned gap in authoring-flow.md.
- Draft edits are made in two equivalent views: a raw-text editor and
  structured per-definition forms (evaluation and agent task from #18;
  the domain form is part of the Domain editor above; the mapping form
  is described below). The forms mutate the same draft YAML text via
  the `yaml` Document API, so unexposed fields, comments, and ordering
  are preserved; a draft that does not parse degrades the form to a
  notice and leaves the raw editor usable. Select options come from
  `GET /api/v1/contract`; entity/event/projection suggestions come
  from the domain draft — the core remains the only validator.
- The Mapping page carries the structured mapping form plus a preview
  panel (#17). The form edits `mapping.yaml`'s restricted mapping
  language: one section per declared source (name + format), each with
  its own entity bindings (id + property `{field|literal, as}`
  assignments) and event bindings (subject, `when` condition tree over
  the allowlisted operators, occurrence and optional observation-time
  assignments restricted to the contract's `time_casts`). Sources can be
  added and removed; a legacy single-source draft (`source:` +
  top-level `entities`/`events`) can be rewritten into `sources:` form
  with the form's "Convert to sources list" action. The panel posts a pasted CSV/JSON sample plus
  the whole draft bundle to `/api/v1/workspaces/drafts/preview`; the
  format is the selected source's declared `format`, and the
  byte/record bounds come from `contract.preview_limits`. A draft with
  several sources shows a source picker and sends the choice as
  `sample.source`; single-source drafts resolve to that one source and
  send its name the same way. Results show each
  source row next to the canonical entities/events it produced —
  normalized nulls and timestamps, `accepted`/`rejected` status,
  evidence references (observation id, preview id, row, input and
  mapping digests), and per-record diagnostics. A displayed result is
  keyed to the draft revision, sample text, and selected source it ran
  against: editing the draft, the sample, or the source selection marks
  it stale until re-run, so a preview can never present results for an
  older mapping as current.
- The Proposals page (#63) is the operational console's review surface:
  every persisted proposal renders its exact `action`/`params`, the
  `payload_digest` a decision binds, the canonical target and related
  entities, the grounding evidence, and the recorded AI rationale —
  labeled as model hypothesis, never as fact — plus the append-only
  decision history and recorded execution attempts.
  A page-level reviewer identity (self-asserted, recorded verbatim on
  every decision) gates the Approve/Reject pair on each card; the
  decision posts to `POST /api/v1/workspaces/proposals/decide` and the
  list refreshes so the new status and appended history show. Domain
  failures (`DECISION.*`) render in the card's diagnostics channel;
  `503 STORAGE.WRITE_BUSY` renders as a retryable transport error. A
  decision here is recorded through the same core `decide` on the same
  `canon.db` the CLI uses, so `ontology execute` honors it identically.
  An approved proposal additionally offers an execute control (ADR
  0004): the action arms on a first click and only the armed second
  click posts to `POST /api/v1/workspaces/execute`. A re-run on a
  delivered proposal stays available because the core answers it
  idempotently — it replays the stored receipt rather than writing
  again. The result renders inline — execution id, attempt, `receipt`,
  and a `replayed` "already executed" state — while a refusal
  (`DECISION.NOT_APPROVED`, `EXECUTION.UNKNOWN_PROPOSAL`) shows the
  core's diagnostics in place. The core's `check_approval` gate, not
  the button, is the authority: a proposal that loses its approval
  between render and submit is refused the same as on the CLI.
- The Trace and Reports pages (#62) are read-only inspection over the
  operational endpoints (`workspaces/trace`, `trace/subject`, `trace/run`,
  `workspaces/evaluations`, `evaluations/run`). The Trace page loads the
  journal/overview summary, then follows one subject (`Entity/Id`) or one
  artifact id through the recorded chain — observations, facts, projection
  and diagnosis runs, proposals, decisions, execution attempts, later
  correlated facts, and evaluations — in stage order with the core's
  nature labels. Failed stages, unindexed proposals, corrupt journal
  lines, and unresolved links render in place as evidence; lookup
  inputs are sent verbatim so `TRACE.INVALID_SUBJECT`/`TRACE.UNKNOWN_ID`
  stay server-owned domain diagnostics. The Reports page lists frozen
  evaluation runs and renders each stored report verbatim: cohort
  pairing (paired/pending/invalid/ambiguous/excluded), medians and
  counts, the target verdict, guardrail verdicts with evidence, and the
  report's own no-causation note. Verdict styling is centralized in one
  tone map (`components/StatusBadge.tsx`): `not_met`/`missed` are
  failure, `unverified` and `unavailable` are visually distinct from any
  success tone, and unknown statuses render neutral — a report can never
  look successful unless the core said so. Both pages read
  `.ontology/` history through the API only; they never touch workspace
  definitions, so frozen artifacts stay inspectable across draft edits.
- The Run page (`/workspace/run`, ADR 0004) sequences the whole loop on
  one screen — ingest → project → diagnose → review/execute → evaluate —
  each stage a card that posts to its run-trigger endpoint
  (`workspaces/ingest`, `project`, `diagnose`, `evaluate`; stage four
  links to Proposals or embeds the same armed execute control). A
  "next action" banner derives where the loop stands from the recorded
  store state — no ingest yet, candidates awaiting diagnosis, proposals
  awaiting decision, an approved proposal ready to execute, or a report
  ready to freeze — and deep-links to Trace, Proposals, and Reports.
  Each stage renders its trigger response in place: ingest shows
  accepted/rejected counts and row diagnostics, project lists the
  candidates, diagnose names the provider/model/status and new
  proposals, evaluate links the frozen report, and every stage shows
  core `diagnostics` verbatim with `503 STORAGE.WRITE_BUSY` as a
  retryable error. The Diagnose stage lists the providers the contract
  advertises (`contract.diagnosis.providers`) and defaults to `stub`,
  so a billed model call is always a deliberate choice.

## Development

From a clean checkout (Python 3.11+, uv, Node ≥ 20.19):

```bash
# API
uv sync --package ontology-api && source .venv/bin/activate
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
editor; unsaved changes warn before in-app navigation and tab close. The
Run page drives the loop stages through the API triggers; the Trace /
Reports / Proposals pages inspect and govern recorded history, frozen
evaluation reports, and proposals.

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
