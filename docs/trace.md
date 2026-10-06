# Trace inspection and export: one linked run across the loop

```bash
ontology trace <workspace>
ontology trace <workspace> --subject Job/101
ontology trace <workspace> --run dgx-20261005T120000000000Z-abcd1234
ontology trace <workspace> --export trace-export.json
ontology trace <workspace> --subject Job/101 --json
```

Every stage of the loop already leaves durable artifacts — immutable
observation evidence and raw snapshots, canonical entities and append-only
facts, frozen projection/diagnosis/evaluation runs, persisted proposals,
human decisions, write-back attempts, and the per-stage journal at
`.ontology/trace.jsonl`. `ontology trace` reassembles them — strictly
read-only — into one linked view, so a run can be followed from source
observation through the outcome report.

Inspection never loads or validates the workspace definitions: history
stays inspectable after a `domain.yaml` edit breaks the current
configuration, and it never creates `.ontology/` or `canon.db` on a
workspace that has not ingested.

## Views

- **overview** (default) — counts per stage: journal events, ingest runs
  and observations, canonical entities and facts, frozen projection /
  diagnosis / evaluation runs, proposals by status, decision and
  execution-attempt counts, and the link totals with unresolved count.
- **`--subject Entity/Id`** — follows one subject through the loop in
  stage order: the observation evidence and snapshots that produced its
  facts, the facts themselves with observation provenance, the projection
  runs where it was an input or candidate, the diagnosis runs covering it,
  its proposals with human decisions and write-back attempts, later facts
  correlated with the intervention, and its entry in each frozen
  evaluation report.
- **`--run <ID>`** — anchors on one artifact id (ingest id, observation
  id, snapshot digest, entity `Entity/Id`, fact key, projection/diagnosis/
  evaluation run id, proposal id, decision id, execution id, or
  idempotency key), prints the stored record, its direct links, the
  subjects it touches, and the journal events that mention it. An id that
  appears only in the journal — e.g. a run that failed before freezing —
  resolves as `journal_only`; an id that exists nowhere is
  `TRACE.UNKNOWN_ID`.
- **`--export <path>`** — writes a self-contained JSON bundle
  (`ontology-trace`, `format_version` 1) embedding the journal, all
  observation records, the raw snapshot contents, canonical entities and
  facts, every frozen run, and the full link graph. An existing file is
  never overwritten — an export is point-in-time evidence; choose a new
  path.

Every view is available as JSON via `--json`. Non-JSON output prints any
diagnostics first, then the view.

## The link graph

Assembly records every reference between artifact ids as a link —
`{from, to, via, resolved}` — so "one run" means a connected chain, not a
collection of rows. Links follow the recorded evidence: observation →
ingest run → snapshot digest, observation → resolved entity, observation
→ asserted fact, fact → related fact or proposal (`possibly_corrects`,
`correlated_intervention`), projection run → input fact keys and
candidate evidence, diagnosis run → projection run, proposal → run,
evidence, subject, and related entities, decision → proposal, execution →
proposal, idempotency key → execution, evaluation run → input and paired
fact keys.

A link whose endpoint exists in no stored artifact and no journal event
is reported in `unresolved` with a `TRACE.UNRESOLVED_LINK` warning —
dangling references are surfaced, never silently dropped.

## Nature labels

Each view tags artifacts with a nature so the four voices of the loop
stay distinguishable:

- `observed` — observations, ingest runs, snapshots, entities, facts;
- `derived` — deterministic projection runs;
- `ai` — diagnosis runs and the proposals they produced;
- `human` — approve/reject decisions;
- `writeback` — execution attempts and their receipts;
- `evaluated` — frozen outcome reports.

## Failures stay inspectable

Failed runs are recorded evidence, not gaps: a provider failure leaves a
`failed` diagnosis run with its diagnostics and a `diagnosis.failed`
journal event; rejected proposal items stay inside the run record;
rejected proposals keep their decision; `attempting`, `failed`, and
`uncertain` execution rows retain their errors and partial receipts;
unavailable guardrails and unverified outcomes keep their full frozen
reports.

## Diagnostics

- `TRACE.NO_WORKSPACE` — the workspace directory does not exist.
- `TRACE.CONFLICTING_ARGS` — `--export` with `--run`/`--subject`, or
  `--run` and `--subject` together.
- `TRACE.INVALID_SUBJECT` — `--subject` is not `Entity/Id`.
- `TRACE.UNKNOWN_ID` — `--run` names no stored artifact or journal id.
- `TRACE.EXPORT_EXISTS` — the export path already exists.
- `TRACE.EXPORT_ERROR` — the export could not be written.
- `TRACE.JOURNAL_CORRUPT` — a journal line is not a JSON object; skipped.
- `TRACE.JOURNAL_UNREADABLE` — the journal cannot be read.
- `TRACE.EVIDENCE_CORRUPT` / `TRACE.EVIDENCE_UNREADABLE` — an observation
  file or line cannot be read; skipped.
- `TRACE.STORE_ERROR` — the canonical store cannot be read.
- `TRACE.UNRESOLVED_LINK` — a recorded reference points at nothing.

## Privacy

Everything under `<workspace>/.ontology/` is local runtime data — raw
snapshots, normalized records, prompts, model output, decisions, and
receipts — and stays out of version control (`.ontology/` is git-ignored).
An `--export` bundle embeds the same data verbatim, so it must be handled
like the runtime directory itself: write it to a local path, share it
deliberately, and never commit it. See [docs/ingestion.md](ingestion.md)
for what the evidence layer stores.
